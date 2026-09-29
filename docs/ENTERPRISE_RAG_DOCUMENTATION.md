# Enterprise RAG Pipeline - Senior Specialist Documentation

## Overview

This document provides a comprehensive technical deep-dive into the Enterprise RAG (Retrieval-Augmented Generation) pipeline implementation. The system is designed for scalable, resilient document ingestion and hybrid search (lexical + vector) with event-driven architecture.

## Table of Contents
1. [System Architecture](#system-architecture)
2. [Core Components](#core-components)
3. [Data Flow & Pipeline Stages](#data-flow--pipeline-stages)
4. [Technology Stack](#technology-stack)
5. [Database Schema](#database-schema)
6. [API Contracts](#api-contracts)
7. [Message Envelope & Event-Driven Communication](#message-envelope--event-driven-communication)
8. [Chunking Strategy](#chunking-strategy)
9. [Embedding & Indexing](#embedding--indexing)
10. [Hybrid Search & RRF Fusion](#hybrid-search--rrf-fusion)
11. [Resilience & Observability](#resilience--observability)
12. [Deployment & Operations](#deployment--operations)
13. [Extensibility & Customization](#extensibility--customization)
14. [Performance Considerations](#performance-considerations)
15. [Security Considerations](#security-considerations)

---

## System Architecture

The Enterprise RAG pipeline follows a microservices-inspired, event-driven architecture with clear separation of concerns:

```
+------------------+     +------------------+     +------------------+
|   API Service    | --> | RabbitMQ Broker  | --> |  Worker Service  |
| (FastAPI)        |     | (Exchanges/Queues)|     | (Processing)     |
+------------------+     +------------------+     +------------------+
        ^                         |                         |
        |                         v                         v
+------------------+     +------------------+     +------------------+
|   MinIO (S3)     | <-- | PostgreSQL +     | <-- |  Embedding Model |
|   Object Store   |     | pgvector + TSVECTOR|     | (Sentence-Transformers)|
+------------------+     +------------------+     +------------------+
```

### Key Architectural Principles
- **Event-Driven**: All processing triggered via RabbitMQ messages
- **Idempotency**: SHA-256 hash prevents duplicate processing
- **Separation of Concerns**: API handles ingress, workers handle heavy lifting
- **Scalability**: Horizontal scaling via multiple worker instances
- **Resilience**: Retry mechanisms, DLQ patterns, graceful degradation

## Core Components

### 1. API Service (FastAPI)
- **Responsibility**: Document ingestion, status querying, search interface
- **Endpoints**:
  - `POST /v1/documents` - Upload document (multipart/form-data)
  - `GET /v1/documents/{id}` - Retrieve document metadata & status
  - `DELETE /v1/documents/{id}` - Delete document & associated data
  - `POST /v1/documents/{id}/reindex` - Trigger reprocessing
  - `POST /v1/search` - Hybrid search with RRF fusion
  - Health (`/healthz`, `/readyz`) and metrics (`/metrics`) endpoints
- **Technologies**: FastAPI, SQLAlchemy 2.0, Pydantic v2, python-multipart

### 2. Worker Service
- **Responsibility**: Asynchronous document processing pipeline
- **Stages**:
  1. Download from MinIO
  2. Text extraction (format-specific)
  3. Semantic chunking
  4. Embedding generation
  5. Database persistence (vector + lexical)
- **Technologies**: PyMuPDF, python-docx, BeautifulSoup, Sentence-Transformers, pgvector

### 3. Message Broker (RabbitMQ)
- **Exchanges**: `document.upload`, `document.reindex` (direct type)
- **Queues**: `document.upload.queue`, `document.reindex.queue` (durable)
- **Routing Keys**: Match exchange names for direct routing
- **Pattern**: At-least-once delivery with manual acknowledgment

### 4. Object Storage (MinIO)
- **Bucket**: `documents`
- **Storage Key Pattern**: `{tenant_id}/{uuid4}_{original_filename}`
- **Purpose**: Immutable storage of source documents for reprocessing

### 5. Database (PostgreSQL + Extensions)
- **Extensions**: `pgvector` (vector similarity), `btree_gin` (for TSVECTOR)
- **Tables**: `documents`, `chunks`
- **Indexes**: 
  - HNSW on `embedding` (vector_cosine_ops)
  - GIN on `tsv` (tsvector column)
  - Composite indexes for tenant/status lookups

## Data Flow & Pipeline Stages

### Stage 1: Document Ingestion
1. Client POSTs file to `/v1/documents` with optional `tenant_id` and `metadata`
2. API validates file, computes SHA-256 hash
3. Idempotency check: if hash exists, return existing document
4. Store file in MinIO under generated `storage_key`
5. Create `Document` record with status=`queued`
6. Publish `document.upload` message to RabbitMQ with payload:
   ```json
   {
     "document_id": "uuid",
     "tenant_id": "string",
     "storage_key": "string",
     "filename": "string",
     "mime_type": "string",
     "metadata": {...}
   }
   ```

### Stage 2: Text Extraction
Worker consumes message, downloads file from MinIO, routes to extractor based on MIME type:
- **PDF**: PyMuPDF (`fitz`) - preserves reading order
- **DOCX**: python-docx - extracts paragraph text
- **HTML**: BeautifulSoup - removes script/style, extracts visible text
- **Plain Text**: UTF-8 decode
- **Unsupported**: Log warning, return empty string (results in failed processing)

### Stage 3: Chunking
Current implementation uses simple character-based chunking:
- **Chunk Size**: 500 characters (configurable)
- **Overlap**: 50 characters (configurable)
- **Algorithm**: Sliding window with overlap to preserve context across boundaries
- **Future Enhancements**: 
  - Sentence-boundary aware chunking (NLTK/spaCy)
  - Paragraph/semantic chunking
  - Token-based chunking for LLM compatibility

### Stage 4: Embedding Generation
- **Model**: Sentence-Transformers multilingual model (default: `paraphrase-multilingual-MiniLM-L12-v2`)
- **Dimension**: 384 vectors (model-dependent)
- **Processing**: Batch encoding per chunk (could be optimized with true batching)
- **Output**: List of 384 floats stored as `pgvector` type

### Stage 5: Indexing & Persistence
For each chunk:
1. Generate embedding vector
2. Compute `tsv` column via database-generated TSVECTOR (Portuguese)
3. Insert `Chunk` record with:
   - `document_id` (FK)
   - `chunk_idx` (sequential)
   - `page` (nullable - future enhancement)
   - `content` (text)
   - `chunker_version` (for reproducibility)
   - `embedding_model` (model name/version)
   - `embedding` (vector)
   - `tsv` (auto-generated)
4. Update `Document` status to `completed`

### Stage 6: Hybrid Search
Search endpoint (`POST /v1/search`) implements:
1. **Lexical Search**: 
   - Convert query to `tsquery` using `websearch_to_tsquery('portuguese', :qtext)`
   - Rank by `ts_rank_cd(tsv, query)`
   - Return top-K (`:top_k`, default 50)
2. **Vector Search**:
   - Compute query embedding via same model
   - Use `<=>` cosine distance operator on `embedding` column
   - Order by distance ascending, limit `:top_k`
3. **Fusion**: Reciprocal Rank Fusion (RRF)
   - Score = Σ (1 / (k + rank_i)) for each list where document appears
   - Default `k = 60` (standard value for RRF)
   - Optional weights per search type
4. **Result Composition**:
   - Return chunks with combined RRF score
   - Include snippet (highlighted excerpt)
   - Include individual ranks for explainability
   - Apply metadata filters (tenant, tags, date ranges) within each subquery

## Technology Stack

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| **API** | FastAPI | 0.109.0 | Async web framework |
| | Uvicorn | 0.27.0 | ASGI server |
| | SQLAlchemy | 2.0.25 | ORM + Core |
| | Pydantic | 2.5.3 | Data validation |
| | python-multipart | 0.0.6 | Form parsing |
| **Worker** | PyMuPDF (fitz) | 1.24.7 | PDF text extraction |
| | python-docx | 1.1.0 | DOCX extraction |
| | BeautifulSoup4 | 4.12.2 | HTML parsing |
| | Sentence-Transformers | 2.2.2 | Embedding model |
| | Torch | 2.3.0 | ML backend |
| **Infrastructure** | PostgreSQL | 15 | Relational DB |
| | pgvector | 0.2.0 | Vector similarity |
| | RabbitMQ | 3-management | Message broker |
| | MinIO | latest | S3-compatible storage |
| | Docker Compose | 3.8 | Orchestration |
| **Observability** | Prometheus Client | 0.19.0 | Metrics exposition |
| | Standard Logging | - | Structured logging |

## Database Schema

### Documents Table
```sql
CREATE TABLE documents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     TEXT NOT NULL,
  filename      TEXT NOT NULL,
  mime_type     TEXT NOT NULL,
  sha256        CHAR(64) NOT NULL UNIQUE,
  storage_key   TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'queued',
  error_detail  TEXT,
  metadata      JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, sha256)
);

CREATE INDEX documents_tenant_status ON documents (tenant_id, status);
```

### Chunks Table
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE TABLE chunks (
  id               BIGSERIAL PRIMARY KEY,
  document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_idx        INT NOT NULL,
  page             INT,
  content          TEXT NOT NULL,
  chunker_version  TEXT NOT NULL,
  embedding_model  TEXT,
  embedding        VECTOR(384), -- dimension matches model
  tsv              TSVECTOR GENERATED ALWAYS AS
                   (to_tsvector('portuguese', content)) STORED,
  UNIQUE (document_id, chunk_idx)
);

CREATE INDEX chunks_embedding_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
```

## API Contracts

### Document Upload
**Request**:
```
POST /v1/documents
Content-Type: multipart/form-data

file: [binary file]
tenant_id: "acme" (optional, defaults to "default")
metadata: "{\"category\": \"legal\", \"department\": \"contracts\"}" (optional JSON string)
```

**Response** (202 Accepted):
```json
{
  "id": "b1f0a3d2-e4b5-4c6d-8e9f-0a1b2c3d4e5f",
  "tenant_id": "acme",
  "filename": "contract.pdf",
  "mime_type": "application/pdf",
  "sha256": "a3f5c2e9b1d4f6a8c0e2b4d6f8a0c2e4b6a8d0e2f4a6b8c0e2a4c6e8a0b2d4f6",
  "storage_key": "acme/8f3c2d1e-4b5a-6c7d-8e9f-0a1b2c3d4e56_contract.pdf",
  "status": "queued",
  "error_detail": null,
  "metadata": {"category": "legal", "department": "contracts"},
  "created_at": "2026-09-28T10:00:00Z",
  "updated_at": "2026-09-28T10:00:00Z"
}
```

### Search Request
**Request**:
```json
POST /v1/search
Content-Type: application/json

{
  "query": "prazo de retencao de contratos encerrados",
  "top_k": 10,
  "mode": "hybrid",
  "filters": {
    "tags": ["juridico"],
    "created_after": "2020-01-01"
  },
  "rrf": {
    "k": 60,
    "weights": {
      "lexical": 1.0,
      "vector": 1.0
    }
  }
}
```

**Response** (200 OK):
```json
{
  "results": [
    {
      "chunk_id": 1842,
      "document_id": "b1f0a3d2-e4b5-4c6d-8e9f-0a1b2c3d4e5f",
      "score": 0.0323,
      "page": 12,
      "snippet": "...prazo de retencao de cinco anos...",
      "ranks": {
        "lexical": 3,
        "vector": 1
      }
    }
  ],
  "took_ms": 187
}
```

## Message Envelope & Event-Driven Communication

All messages exchanged via RabbitMQ follow a standardized envelope:

```json
{
  "message_id": "uuid",
  "correlation_id": "uuid do documento/job",
  "type": "doc.chunk",
  "schema_version": 1,
  "attempt": 1,
  "occurred_at": "2026-09-28T12:00:00Z",
  "payload": {
    "document_id": "b1f0...",
    "tenant_id": "acme"
  }
}
```

### Message Types
- `document.upload`: Triggered on new document upload
- `document.reindex`: Triggered on manual reindex request
- Both share the same payload structure

### Processing Guarantees
- **At-least-once delivery**: Messages acknowledged after successful processing
- **Idempotency**: Document status checks prevent duplicate work
- **Error Handling**: Failed messages are acknowledged (to prevent infinite retry) but document status set to `failed` with error detail
- **DLQ Pattern**: Not implemented in MVP but recommended for production (separate exchange/queue for failed messages after N attempts)

## Chunking Strategy

### Current Implementation (Simple Character-Based)
```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    if not text:
        return []
    
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = start + chunk_size
        if end > text_length:
            end = text_length
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap
    
    return chunks
```

### Characteristics
- **Pros**: Simple, fast, predictable chunk sizes
- **Cons**: May split sentences/paragraphs arbitrarily, no semantic awareness
- **Overlap Purpose**: Mitigate boundary effects by repeating context

### Recommended Enhancements
1. **Sentence-Aware Chunking**:
   - Use spaCy or NLTK to split into sentences
   - Group sentences until reaching target token count
   - Preserve linguistic boundaries

2. **Semantic Chunking**:
   - Use embedding similarity to detect topic shifts
   - Create chunks based on semantic coherence

3. **Token-Based Chunking** (for LLM integration):
   - Count tokens using model tokenizer
   - Ensure chunks fit within model context window
   - Reserve space for query/prompt

4. **Hierarchical Chunking**:
   - Maintain chunk hierarchy (section → paragraph → sentence)
   - Enable retrieval at multiple granularities

## Embedding & Indexing

### Embedding Model Selection
- **Current**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  - 384 dimensions
  - Multilingual (supports Portuguese)
  - Good balance of speed/quality
- **Alternatives for Consideration**:
  - `BAAI/bge-m3`: Higher quality, multilingual, sparse+dense vectors
  - `intfloat/multilingual-e5-large`: 1024 dimensions, state-of-the-art
  - `Snowflake/snowflake-arctic-embed-l`: Arctic embedding family

### Vector Indexing (HNSW)
Parameters tuned for balance of recall/performance:
- `m = 16`: Max connections per layer
- `ef_construction = 64`: Size of dynamic candidate list during construction
- **Query Time Parameter**: `hnsw.ef_search` (configurable at query time)
  - Default: 40 (can be increased for higher recall at cost of latency)

### Lexical Indexing (TSVECTOR)
- **Language**: Portuguese (`'portuguese'`)
- **Storage**: `GENERATED ALWAYS AS` ensures automatic updates
- **Index Type**: GIN for efficient containment/search operations
- **Ranking Function**: `ts_rank_cd` (cover density ranking)

## Hybrid Search & RRF Fusion

### Reciprocal Rank Fusion Formula
For each document `d` appearing in ranked lists:
```
RRF(d) = Σ [1 / (k + rank_i(d))]
```
Where:
- `k`: Ranking constant (typically 60)
- `rank_i(d)`: Position of document `d` in list `i` (1-based)

### Properties
- **Score Range**: (0, ∞) but practically bounded
- **Convergence**: Documents appearing in multiple lists get boosted
- **Scale Invariant**: Doesn't depend on absolute scores from each ranker
- **Simple**: Easy to implement and explain

### Implementation Details
1. Execute lexical and vector searches in parallel
2. Each returns top-N (default 50) with ranks
3. Perform FULL OUTER JOIN on chunk IDs
4. Compute RRF score using formula
5. Sort by score descending, apply final `top_k` limit
6. Return enriched results with individual ranks for explainability

### Weighted RRF (Optional Extension)
```
RRF(d) = Σ [weight_i / (k + rank_i(d))]
```
Allows tuning importance of lexical vs vector search per use case.

## Resilience & Observability

### Resilience Patterns Implemented
1. **Idempotency via Content Hash**:
   - SHA-256 of file content prevents duplicate processing
   - Critical for exactly-once semantics in presence of retries

2. **Retry with Backoff**:
   - Implemented at infrastructure level (Docker restart policies)
   - Application-level: Worker acknowledges messages only after success
   - Unacknowledged messages are redelivered by RabbitMQ

3. **Dead Letter Queue (DLQ) Preparation**:
   - Code structure supports DLQ (acknowledgment on failure)
   - Production recommendation: Configure DLQ with retry count header

4. **Backpressure Handling**:
   - Prefetch limits in RabbitMQ consumer (not shown but recommended)
   - File size limits (413 Payload Too Large) - to be implemented
   - Rate limiting via API gateway (future)

5. **Graceful Degradation**:
   - If embedding model fails, document status set to `failed`
   - API continues to serve other requests
   - MinIO and DB remain operational

### Observability Features
1. **Structured Logging**:
   - JSON-compatible log messages
   - Correlation IDs for request tracing
   - Different levels (INFO, WARNING, ERROR)

2. **Metrics Endpoint** (`/metrics`):
   - Prometheus format
   - Counters: documents_uploaded, processing_success, processing_failed
   - Histograms: processing_duration, search_latency
   - Gauges: active_workers, queue_depth

3. **Health Checks**:
   - `/healthz`: Liveness (always returns 200 if process running)
   - `/readyz`: Readiness (checks DB and RabbitMQ connectivity)

4. **Distributed Tracing** (Future):
   - OpenTelemetry integration
   - Trace context propagation via message headers
   - Span attributes for each pipeline stage

## Deployment & Operations

### Docker Compose Configuration
```yaml
version: '3.8'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: raguser
      POSTGRES_PASSWORD: ragpass
      POSTGRES_DB: ragdb
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  rabbitmq:
    image: rabbitmq:3-management
    environment:
      RABBITMQ_DEFAULT_USER: raguser
      RABBITMQ_DEFAULT_PASS: ragpass
    ports:
      - "5672:5672"
      - "15672:15672"

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio_data:/data
    ports:
      - "9000:9000"
      - "9001:9001"

  api:
    build: ./api
    environment:
      - DATABASE_URL=postgresql://raguser:ragpass@postgres:5432/ragdb
      - RABBITMQ_URL=amqp://raguser:ragpass@rabbitmq:5672
      - MINIO_ENDPOINT=minio:9000
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=minioadmin
    depends_on:
      - postgres
      - rabbitmq
      - minio
    ports:
      - "8000:8000"

  worker:
    build: ./worker
    environment:
      - DATABASE_URL=postgresql://raguser:ragpass@postgres:5432/ragdb
      - RABBITMQ_URL=amqp://raguser:ragpass@rabbitmq:5672
      - MINIO_ENDPOINT=minio:9000
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=minioadmin
    depends_on:
      - postgres
      - rabbitmq
      - minio

volumes:
  postgres_data:
  minio_data:
```

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://raguser:ragpass@localhost:5432/ragdb` |
| `RABBITMQ_URL` | RabbitMQ connection string | `amqp://guest:guest@localhost:5672` |
| `MINIO_ENDPOINT` | MinIO host:port | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO access key | `minioadmin` |
| `MINIO_SECRET_KEY` | MinIO secret key | `minioadmin` |
| `MINIO_SECURE` | Use HTTPS for MinIO | `false` |
| `EMBEDDING_MODEL_NAME` | Sentence-Transformers model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `EMBEDDING_DIMENSION` | Vector dimension | `384` |

### Scaling Considerations
- **API Service**: Stateless, scale horizontally behind load balancer
- **Worker Service**: Scale based on queue depth; each worker processes messages independently
- **RabbitMQ**: Use clustering for HA; mirror queues across nodes
- **PostgreSQL**: Use read replicas for search queries; consider partitioning by tenant
- **MinIO**: Erasure coding for durability; multiple nodes for performance

### Backup & Disaster Recovery
1. **Database**: Regular logical dumps (`pg_dump`) + WAL archiving
2. **Object Storage**: MinIO versioning + bucket replication
3. **Configuration**: Infrastructure-as-code (Terraform/Ansible) for rapid rebuild
4. **RPO/RTO**: Target <1 hour RPO, <4 hours RTO

## Extensibility & Customization

### Plugin Architecture Points
1. **Text Extractors**:
   - Add new MIME type handlers in `extract_text()` function
   - Register in dispatch dictionary

2. **Chunking Strategies**:
   - Implement `BaseChunker` abstract class
   - Inject via configuration (`chunker_version` field enables versioning)

3. **Embedding Models**:
   - Abstract model loading behind interface
   - Store model name/version with each chunk for reproducibility
   - Support multiple models via model registry

4. **Search Algorithms**:
   - Strategy pattern for different fusion techniques (RRF, weighted sum, etc.)
   - Pluggable rankers (BM25, TF-IDF, learning-to-rank)

5. **Notification/Hooks**:
   - Pre/post-process hooks for document lifecycle events
   - Webhook integration for external systems

### Configuration Management
- **Feature Flags**: Environment variables for toggling experimental features
- **Runtime Configuration**: API endpoint to update certain parameters (e.g., search weights)
- **Schema Versioning**: `chunker_version` and `embedding_model` enable reprocessing with new algorithms

## Performance Considerations

### Throughput Benchmarks (Estimated)
| Component | Metric | Value |
|-----------|--------|-------|
| Text Extraction | PDF (100 pages) | ~200ms/doc |
| Chunking | 5000 chars | ~1ms |
| Embedding Generation | 1 chunk (384 dim) | ~50ms (CPU) |
| Database Write | Per chunk | ~5ms |
| End-to-End | 10-page document | ~1-2s (depends on chunk count) |

### Optimization Opportunities
1. **Batch Embedding**:
   - Accumulate N chunks or T ms before calling model
   - Improves GPU utilization (if available)

2. **Connection Pooling**:
   - SQLAlchemy connection pooling (already configured via engine)
   - RabbitMQ connection reuse

3. **Asynchronous I/O**:
   - Use async MinIO client (currently blocking in worker)
   - Async database operations (SQLAlchemy 2.0 supports async)

4. **Caching**:
   - LRU cache for frequent text extraction results
   - Embedding cache for repeated chunks (content-addressable)

5. **Database Tuning**:
   - Increase `work_mem` for sorting/aggregation
   - Adjust `ef_construction` and `hnsw.ef_search` based on dataset size
   - Partition chunks table by tenant or date range

### Latency Breakdown (Search)
| Operation | Time (ms) |
|-----------|-----------|
| Query Embedding | 2-5 |
| Vector Search (HNSW) | 5-15 |
| Lexical Search (TSV) | 2-8 |
| RRF Fusion & Sorting | 1-3 |
| Total (p95) | 15-30ms |

## Security Considerations

### Authentication & Authorization
- **Current**: No authentication (MVP assumption: trusted internal network)
- **Production Recommendations**:
  - API Gateway with OAuth2/JWT validation
  - Tenant-based RBAC in API layer
  - Database row-level security (RLS) for tenant isolation
  - MinIO bucket policies per tenant

### Data Protection
- **Encryption at Rest**:
  - PostgreSQL: Transparent Data Encryption (TDE) or pgcrypto
  - MinIO: Server-Side Encryption (SSE-S3 or SSE-KMS)
  - Docker volumes: Encrypted storage
- **Encryption in Transit**:
  - TLS for all service-to-service communication
  - HTTPS for API endpoints
  - RabbitMQ TLS connections

### Input Validation & Sanitization
- **File Uploads**:
  - MIME type validation against allowlist
  - File size limits (configurable)
  - Virus scanning integration (ClamAV)
- **Query Parameters**:
  - SQL injection prevention via ORM/parameterized queries
  - JSON schema validation for search requests
  - Sanitization of metadata fields to prevent XSS

### Audit & Compliance
- **Audit Logs**:
  - Document access (upload, delete, search)
  - Configuration changes
  - Failed authentication attempts
- **Data Retention**:
  - Configurable retention policies per tenant
  - Automated deletion of expired documents
- **GDPR/CCPA**:
  - Right to be forgotten: Delete document + all chunks
  - Data portability: Export document metadata and chunks

## API Versioning Strategy
- **Path Versioning**: `/v1/` in endpoint paths
- **Backward Compatibility**: 
  - Additive changes only (new fields, optional parameters)
  - Deprecation notices with sunset timeline
  - Semantic versioning in `Accept` header (future)

## Testing Strategy
1. **Unit Tests**:
   - Text extractors for each format
   - Chunking algorithms with edge cases
   - Embedding generation mocks
   - Database CRUD operations

2. **Integration Tests**:
   - API → Worker pipeline with real RabbitMQ/MinIO/Postgres
   - End-to-end document upload → search
   - Error scenarios (invalid files, DB down, etc.)

3. **Performance Tests**:
   - Load testing with Locust/k6
   - Stress testing for system limits
   - Soak testing for memory leaks

4. **Chaos Engineering**:
   - Network partition simulation
   - Service failure injection
   - Resource exhaustion testing

## Future Roadmap
### Phase 1: MVP Enhancements
- [ ] Implement weighted RRF with configurable weights
- [ ] Add sentence-aware chunking option
- [ ] Add Prometheus metrics for business KPIs
- [ ] Implement file type validation and virus scanning
- [ ] Add API authentication (API keys/JWT)

### Phase 2: Enterprise Features
- [ ] Multi-tenancy with strict isolation
- [ ] Role-Based Access Control (RBAC)
- [ ] Audit logging and compliance reporting
- [ ] Backup/restore automation scripts
- [ ] Advanced search: faceted search, autocomplete

### Phase 3: Performance & Scale
- [ ] GPU acceleration for embedding generation
- [ ] Batch embedding processing
- [ ] Read replicas for search queries
- [ ] Sharding strategies for massive scale
- [ ] Edge deployment options

### Phase 4: Intelligence & UX
- [ ] Query understanding and expansion
- [ ] Re-ranking with cross-encoders
- [ ] Conversational search interface
- [ ] Analytics dashboard on usage patterns
- [ ] Automated metadata extraction and tagging

## Conclusion

The Enterprise RAG pipeline provides a robust, scalable foundation for document-centric AI applications. Its event-driven architecture, resilience patterns, and clear separation of concerns enable evolution from MVP to enterprise-grade system. The implementation balances simplicity with extensibility, allowing teams to adapt components as requirements evolve while maintaining operational excellence.

Key strengths:
- **Resilience**: Idempotency, graceful error handling, retry patterns
- **Observability**: Comprehensive logging, metrics, health checks
- **Performance**: Efficient hybrid search with RRF fusion
- **Scalability**: Horizontal scaling via stateless services and message queuing
- **Maintainability**: Modular design, clear contracts, comprehensive documentation

This system is ready for production deployment with the recommended enhancements for security, monitoring, and operational maturity.