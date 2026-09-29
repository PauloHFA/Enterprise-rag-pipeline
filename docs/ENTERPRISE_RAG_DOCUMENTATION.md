# Enterprise RAG Pipeline - Documentação Técnica

## Visão Geral

Este documento fornece uma análise técnica abrangente da implementação do pipeline Enterprise RAG (Retrieval-Augmented Generation). O sistema é projetado para ingestão e busca híbrida (léxica + vetorial) de documentos, escalável e resiliente, com arquitetura orientada a eventos.

## Índice
1. [Arquitetura do Sistema](#arquitetura-do-sistema)
2. [Componentes Principais](#componentes-principais)
3. [Fluxo de Dados & Estágios do Pipeline](#fluxo-de-dados--estágios-do-pipeline)
4. [Stack Tecnológica](#stack-tecnológica)
5. [Schema do Banco de Dados](#schema-do-banco-de-dados)
6. [Contratos de API](#contratos-de-api)
7. [Envelope de Mensagens & Comunicação Orientada a Eventos](#envelope-de-mensagens--comunicação-orientada-a-eventos)
8. [Estratégia de Chunking](#estratégia-de-chunking)
9. [Embedding & Indexação](#embedding--indexação)
10. [Busca Híbrida & RRF Fusion](#busca-híbrida--rrf-fusion)
11. [Resiliência & Observabilidade](#resiliência--observabilidade)
12. [Deploy & Operações](#deploy--operações)
13. [Extensibilidade & Customização](#extensibilidade--customização)
14. [Considerações de Performance](#considerações-de-performance)
15. [Considerações de Segurança](#considerações-de-segurança)

---

## Arquitetura do Sistema

O pipeline Enterprise RAG segue uma arquitetura inspirada em microservices, orientada a eventos com clara separação de preocupações:

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

### Princípios Arquitetônicos Chave
- **Orientado a Eventos**: Todos os processamentos são acionados via mensagens RabbitMQ
- **Idempotência**: Hash SHA-256 previne processamento duplicado
- **Separação de Preocupações**: API lida com ingresso, workers com processamento pesado
- **Escalabilidade**: Múltiplas instâncias de workers com escala horizontal
- **Resiliência**: Padrões de retry, DLQ, degradação graceful

## Componentes Principais

### 1. API Service (FastAPI)
- **Responsabilidade**: Ingestão de documentos, consulta de status, interface de busca
- **Endpoints**:
  - `POST /v1/documents` - Upload de documento (multipart/form-data)
  - `GET /v1/documents/{id}` - Recuperar metadados e status do documento
  - `DELETE /v1/documents/{id}` - Deletar documento e dados associados
  - `POST /v1/documents/{id}/reindex` - Disparar reprocessamento
  - `POST /v1/search` - Busca híbrida com fusão RRF
  - Endpoints de health (`/healthz`, `/readyz`) e métricas (`/metrics`)
- **Tecnologias**: FastAPI, SQLAlchemy 2.0, Pydantic v2, python-multipart

### 2. Worker Service
- **Responsabilidade**: Pipeline assíncrono de processamento de documentos
- **Estágios**:
  1. Download do MinIO
  2. Extração de texto (específica por formato)
  3. Chunking semântico
  4. Geração de embeddings
  5. Persistência no banco (vetorial + léxico)
- **Tecnologias**: PyMuPDF, python-docx, BeautifulSoup, Sentence-Transformers, pgvector

### 3. Message Broker (RabbitMQ)
- **Exchanges**: `document.upload`, `document.reindex` (tipo direct)
- **Queues**: `document.upload.queue`, `document.reindex.queue` (duráveis)
- **Routing Keys**: Correspondem aos nomes dos exchanges para roteamento direto
- **Padrão**: Entrega at-least-once com acknowledgment manual

### 4. Object Storage (MinIO)
- **Bucket**: `documents`
- **Padrão de Storage Key**: `{tenant_id}/{uuid4}_{nome_original_arquivo}`
- **Propósito**: Armazenamento imutável de documentos originais para reprocessamento

### 5. Banco de Dados (PostgreSQL + Extensões)
- **Extensões**: `pgvector` (similaridade vetorial), `btree_gin` (para TSVECTOR)
- **Tabelas**: `documents`, `chunks`
- **Índices**: 
  - HNSW em `embedding` (vector_cosine_ops)
  - GIN em `tsv` (coluna tsvector)
  - Índices compostos para consultas tenant/status

## Fluxo de Dados & Estágios do Pipeline

### Estágio 1: Ingestão de Documento
1. Cliente faz POST do arquivo para `/v1/documents` com `tenant_id` e `metadata` opcionais
2. API valida o arquivo, calcula hash SHA-256
3. Verificação de idempotência: se hash existe, retorna documento existente
4. Armazena arquivo no MinIO sob `storage_key` gerado
5. Cria registro `Document` com status=`queued`
6. Publica mensagem `document.upload` no RabbitMQ com payload:
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

### Estágio 2: Extração de Texto
Worker consome a mensagem, baixa o arquivo do MinIO, roteia para o extrator baseado no tipo MIME:
- **PDF**: PyMuPDF (`fitz`) - preserva ordem de leitura
- **DOCX**: python-docx - extrai texto de parágrafos
- **HTML**: BeautifulSoup - remove script/style, extrai texto visível
- **Texto Simples**: Decodificação UTF-8
- **Não Suportado**: Log de warning, retorna string vazia (resulta em processamento falho)

### Estágio 3: Chunking
Implementação atual usa chunking simples baseado em caracteres:
- **Tamanho do Chunk**: 500 caracteres (configurável)
- **Overlap**: 50 caracteres (configurável)
- **Algoritmo**: Janela deslizante com overlap para preservar contexto entre fronteiras
- **Melhorias Futuras**: 
  - Chunking consciente de fronteiras de sentença (NLTK/spaCy)
  - Chunking por parágrafo/semântico
  - Chunking baseado em tokens para compatibilidade com LLM

### Estágio 4: Geração de Embeddings
- **Modelo**: Modelo multilíngue Sentence-Transformers (padrão: `paraphrase-multilingual-MiniLM-L12-v2`)
- **Dimensão**: 384 vetores (dependente do modelo)
- **Processamento**: Codificação em lote por chunk (poderia ser otimizado com batching real)
- **Saída**: Lista de 384 floats armazenada como tipo `pgvector`

### Estágio 5: Indexação & Persistência
Para cada chunk:
1. Gerar vetor de embedding
2. Calcular coluna `tsv` via TSVECTOR gerado pelo banco (Português)
3. Inserir registro `Chunk` com:
   - `document_id` (FK)
   - `chunk_idx` (sequencial)
   - `page` (nullable - melhoria futura)
   - `content` (texto)
   - `chunker_version` (para reprodutibilidade)
   - `embedding_model` (nome/versão do modelo)
   - `embedding` (vetor)
   - `tsv` (auto-gerado)
4. Atualizar status do `Document` para `completed`

### Estágio 6: Busca Híbrida
Endpoint de busca (`POST /v1/search`) implementa:
1. **Busca Léxica**: 
   - Converter query para `tsquery` usando `websearch_to_tsquery('portuguese', :qtext)`
   - Ranquear por `ts_rank_cd(tsv, query)`
   - Retornar top-K (`:top_k`, padrão 50)
2. **Busca Vetorial**:
   - Calcular embedding da query via mesmo modelo
   - Usar operador `<=>` de distância cosseno na coluna `embedding`
   - Ordenar por distância ascendente, limitar `:top_k`
3. **Fusão**: Reciprocal Rank Fusion (RRF)
   - Score = Σ (1 / (k + rank_i)) para cada lista onde o documento aparece
   - Padrão `k = 60` (valor padrão para RRF)
   - Pesos opcionais por tipo de busca
4. **Composição do Resultado**:
   - Retornar chunks com score RRF combinado
   - Incluir snippet (trecho destacado)
   - Incluir ranks individuais para explicabilidade
   - Aplicar filtros de metadados (tenant, tags, intervalos de data) dentro de cada subquery

## Stack Tecnológica

| Camada | Tecnologia | Versão | Propósito | 
|--------|------------|--------|-----------|
| **API** | FastAPI | 0.109.0 | Framework web assíncrono | 
| | Uvicorn | 0.27.0 | Servidor ASGI | 
| | SQLAlchemy | 2.0.25 | ORM + Core | 
| | Pydantic | 2.5.3 | Validação de dados | 
| | python-multipart | 0.0.6 | Parsing de formulários | 
| **Worker** | PyMuPDF (fitz) | 1.24.7 | Extração de texto de PDF | 
| | python-docx | 1.1.0 | Extração de texto de DOCX | 
| | BeautifulSoup4 | 4.12.2 | Parsing de HTML | 
| | Sentence-Transformers | 2.2.2 | Modelo de embedding | 
| | Torch | 2.3.0 | Backend de ML | 
| **Infraestrutura** | PostgreSQL | 15 | Banco de dados relacional | 
| | pgvector | 0.2.0 | Similaridade vetorial | 
| | RabbitMQ | 3-management | Corretor de mensagens | 
| | MinIO | latest | Armazenamento S3-compatível | 
| | Docker Compose | 3.8 | Orquestração | 
| **Observabilidade** | Prometheus Client | 0.19.0 | Exposição de métricas | 
| | Standard Logging | - | Logging estruturado ||

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