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
13. [Deploy Free Tier (Sem Custo)](#deploy-free-tier-sem-custo)
14. [Testes & CI/CD](#testes--cicd)
15. [Extensibilidade & Customização](#extensibilidade--customização)
16. [Considerações de Performance](#considerações-de-performance)
17. [Considerações de Segurança](#considerações-de-segurança)

---

## Status da Implementação

| Item | Status | Artefato |
|------|--------|----------|
| API Service (FastAPI) | ✅ Completo | `api/main.py` |
| Worker Service | ✅ Completo | `worker/worker.py` |
| Busca Híbrida (RRF) | ✅ Completo | `api/search.py` |
| Autenticação (API Key) | ✅ Completo | `api/security.py` |
| Rate Limiting | ✅ Completo | `api/rate_limiter.py` |
| DLQ + Retry Logic | ✅ Completo | `api/rabbitmq_client.py`, `worker/worker.py` |
| Métricas Prometheus | ✅ Completo | `api/metrics.py` |
| Dashboards Grafana | ✅ Completo | `grafana/dashboards/` |
| TLS/SSL (nginx) | ✅ Completo | `nginx/nginx.conf`, `nginx/nginx.free.conf` |
| Backup/Restore | ✅ Completo | `scripts/backup.sh`, `scripts/restore.sh` |
| Testes (unit + integração) | ✅ Completo | `tests/` |
| CI/CD (GitHub Actions) | ✅ Completo | `.github/workflows/ci.yml` |
| Deploy Free Tier | ✅ Completo | `docker-compose.free.yml`, `scripts/bootstrap-free.sh` |
| Autenticação OAuth2/JWT | ⏳ Planejado | Roadmap Fase 2 |
| GPU acceleration | ⏳ Planejado | Roadmap Fase 3 |

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
- **Defense in Depth**: Rate limiting na aplicação + rate limiting no nginx
- **Custo Zero**: Embeddings locais, sem dependência de APIs pagas

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
- **Tecnologias**: FastAPI, SQLAlchemy 2.0, Pydantic v2, python-multipart, prometheus-client

### 1.1 Middleware de Rate Limiting
- **Implementação**: Sliding window (janela deslizante de 60 segundos)
- **Identificação do cliente**: API Key (`X-API-Key`) ou IP de origem
- **Exceções**: `/healthz`, `/readyz`, `/metrics` não são limitados
- **Headers de resposta**: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- **Algoritmo alternativo**: Token bucket (implementado em `api/rate_limiter.py`)
- **Defense in depth**: nginx aplica limites adicionais por zona (`api_limit`, `search_limit`, `auth_limit`)

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
- **Dead Letter Exchange**: `document.dlx` (tipo direct)
- **Queues Principais**: `document.upload.queue`, `document.reindex.queue` (duráveis)
- **Queues DLQ**: `document.upload.dlq`, `document.reindex.dlq` (duráveis)
- **Routing Keys**: Correspondem aos nomes dos exchanges para roteamento direto
- **Padrão**: Entrega at-least-once com acknowledgment manual
- **Argumentos das Queues**:
  ```python
  {
      "x-dead-letter-exchange": "document.dlx",
      "x-dead-letter-routing-key": "document.upload.dlq",
      "x-message-ttl": 86400000  # 24h TTL
  }
  ```
- **Retry Policy**: Worker republica com header `x-retry-count` até 3 tentativas, depois `basic_nack(requeue=False)` → DLQ

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

### 6. Reverse Proxy (Nginx)
- **Responsabilidade**: Terminação TLS, rate limiting por zona, roteamento `/grafana`
- **Configurações**: `nginx/nginx.conf` (produção), `nginx/nginx.free.conf` (free tier, buffers otimizados)
- **Zonas de Rate Limit**: `api_limit` (10r/s), `search_limit` (5r/s), `auth_limit` (5r/m)
- **Security Headers**: HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy

### 7. Stack de Monitoramento
- **Prometheus**: Coleta métricas em `/metrics` (intervalo 15s, retenção 15d/1GB)
- **Grafana**: Dashboards pré-provisionados em `grafana/dashboards/enterprise-rag.json`
- **Dashboards incluídos**: Upload rate, Search latency (p50/p95/p99), Queue depth, Worker processing time, Error rate, Rate limited requests

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

## Deploy Free Tier (Sem Custo)

### Visão Geral
O sistema foi projetado para rodar **100% grátis** em tiers gratuitos de cloud providers ou VMs locais, sem dependência de serviços pagos.

### Opções de Deploy Gratuito

| Provedor | Recursos Free Tier | Ideal Para |
|----------|-------------------|------------|
| **Oracle Cloud Always Free** | 4 ARM CPUs, 24 GB RAM, 200 GB disco | **Produção real** (recomendado) |
| **AWS Free Tier** | 1 t2.micro (1 vCPU, 1 GB) por 12 meses | Testes temporários |
| **Google Cloud Free Tier** | 1 e2-micro (2 vCPU, 1 GB) | Testes temporários |
| **Azure Free Tier** | 1 B1S (1 vCPU, 1 GB) por 12 meses | Testes temporários |
| **Local/On-premise** | Hardware próprio | Desenvolvimento/POC |

### Stack Free Tier (`docker-compose.free.yml`)

Otimizações para recursos limitados:
- **PostgreSQL**: 2GB RAM, `shared_buffers=256MB`, `max_connections=100`
- **RabbitMQ**: 512MB RAM
- **MinIO**: 512MB RAM
- **API**: 1GB RAM
- **Worker**: 1.5GB RAM (escalável: `--scale worker=2`)
- **Prometheus**: 512MB RAM, retenção 15d/1GB
- **Grafana**: 256MB RAM
- **Nginx**: 64MB RAM, buffers otimizados

### Bootstrap Automatizado

Script `scripts/bootstrap-free.sh` para VM Ubuntu:
```bash
# Como root na VM:
curl -fsSL https://raw.githubusercontent.com/SEU_USUARIO/enterprise-rag/main/scripts/bootstrap-free.sh | sudo bash
```

O script executa:
1. Instala Docker + Docker Compose
2. Clona repositório
3. Gera senhas fortes (`openssl rand`)
4. Cria certificado self-signed (ou orienta Let's Encrypt/Cloudflare Tunnel)
5. Deploy da stack completa
6. Health checks de todos os serviços

### SSL/TLS Gratuito

| Opção | Como Funciona | Prós | Contras |
|-------|---------------|------|---------|
| **Cloudflare Tunnel** | `cloudflared` cria túnel para localhost | Grátis, sem abrir portas, DDoS protection, SSL automático | Requer conta Cloudflare |
| **Let's Encrypt** | `certbot` emite certificado válido | Padrão da indústria, válido 90 dias | Precisa domínio + porta 80 aberta |
| **Self-signed** | `openssl` gera certificado local | Imediato, sem dependências | Aviso de segurança no browser |

**Recomendação**: Cloudflare Tunnel para produção, self-signed para testes.

### Variáveis de Ambiente Free Tier (`.env.free`)

```bash
# Senhas geradas automaticamente pelo bootstrap
POSTGRES_PASSWORD=senha_gerada_32_chars
RABBITMQ_PASSWORD=senha_gerada_32_chars
MINIO_ROOT_PASSWORD=senha_gerada_32_chars
API_KEY=chave_gerada_64_chars_hex
GRAFANA_PASSWORD=senha_gerada_16_chars
```

### Recursos Mínimos Recomendados

| Recurso | Mínimo | Recomendado |
|---------|--------|-------------|
| CPU | 2 vCPU | 4 vCPU (ARM) |
| RAM | 4 GB | 8-16 GB |
| Disco | 20 GB | 50+ GB SSD |
| Rede | 1 Gbps | 1+ Gbps |

---

## Testes & CI/CD

### Estratégia de Testes

| Tipo | Localização | Cobertura | Quando Rodar |
|------|-------------|-----------|--------------|
| **Unit Tests** | `tests/test_api.py`, `tests/test_worker.py` | ~85% | Todo commit (CI) |
| **Integration Tests** | `tests/test_integration.py` | Pipeline completa | PR + nightly |
| **Contract Tests** | `tests/test_api.py` (schemas) | API contracts | Todo commit |
| **Load Tests** | `scripts/load-test.k6.js` (futuro) | Performance | Pré-release |

### Testes Unitários

**API (`tests/test_api.py`):**
- Health endpoints (`/healthz`, `/readyz`)
- Autenticação (401 sem API key, 200 com key válida)
- CRUD documentos (upload, get, delete, reindex)
- Busca (modos hybrid/lexical/vector, filtros)
- Métricas Prometheus

**Worker (`tests/test_worker.py`):**
- Extração de texto (PDF, DOCX, HTML, TXT)
- Chunking (tamanho, overlap, edge cases)
- Embeddings (dimensão, tipo)
- Dispatch de extratores por MIME type

### Testes de Integração (`tests/test_integration.py`)

Cenários cobertos:
1. **Pipeline completa**: Upload → Worker processa → Busca retorna resultados
2. **Idempotência**: Upload duplicado retorna documento existente (SHA-256)
3. **Modos de busca**: hybrid, lexical, vector
4. **Filtros**: tenant_id, datas, tags
5. **Reindex**: Reset status + reprocessamento
6. **Delete**: Remove MinIO + DB (cascata)

### CI/CD Pipeline (`.github/workflows/ci.yml`)

**Jobs paralelos:**
1. **lint-and-type-check**: Ruff + MyPy
2. **unit-tests**: PostgreSQL, RabbitMQ, MinIO containers (testcontainers)
3. **integration-tests**: Pipeline completa com serviços reais
4. **docker-build**: Build + smoke test das imagens
5. **security-scan**: Trivy (vulnerabilidades) + pip-audit (dependências)
6. **notify**: Falha → notificação (Slack/Teams/email)

**Serviços de teste (GitHub Actions):**
```yaml
services:
  postgres:
    image: postgres:15
    env: {POSTGRES_USER: raguser, POSTGRES_PASSWORD: ragpass, POSTGRES_DB: ragdb}
    ports: ["5432:5432"]
  rabbitmq:
    image: rabbitmq:3-management
    env: {RABBITMQ_DEFAULT_USER: raguser, RABBITMQ_DEFAULT_PASS: ragpass}
    ports: ["5672:5672"]
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    env: {MINIO_ROOT_USER: minioadmin, MINIO_ROOT_PASSWORD: minioadmin}
    ports: ["9000:9000"]
```

### Cobertura de Código

```bash
# Local
pytest --cov=api --cov=worker --cov-report=html --cov-report=term-missing

# CI: Codecov upload automático
# Target: >80% coverage
```

### Comandos de Teste

```bash
# Instalar dependências de dev
pip install -e ".[dev]"

# Unit tests rápidos
pytest tests/test_api.py tests/test_worker.py -v

# Integration tests (requer serviços rodando)
pytest tests/test_integration.py -v

# Todos com cobertura
pytest --cov=api --cov=worker --cov-report=html

# Lint + type check
ruff check api/ worker/ tests/
mypy api/ worker/
```

### Quality Gates

| Gate | Threshold | Ferramenta |
|------|-----------|------------|
| **Coverage** | >80% | pytest-cov |
| **Lint** | 0 errors | Ruff |
| **Type Check** | 0 errors | MyPy |
| **Security** | 0 high/critical | Trivy + pip-audit |
| **Build** | Success | Docker |
| **Tests** | 100% pass | pytest |

---

## Extensibilidade & Customization

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

### ✅ Phase 1: MVP Enhancements (CONCLUÍDO)
- [x] Weighted RRF com pesos configuráveis (`api/search.py`)
- [x] Prometheus metrics para business KPIs (`api/metrics.py`)
- [x] API authentication (API keys) (`api/security.py`)
- [x] Rate limiting (sliding window + token bucket) (`api/rate_limiter.py`)
- [x] DLQ + Retry logic (RabbitMQ + Worker)
- [x] File type validation (MIME allowlist)
- [x] Backup/restore scripts (`scripts/backup.sh`, `scripts/restore.sh`)

### ✅ Phase 2: Enterprise Features (CONCLUÍDO)
- [x] Multi-tenancy (tenant_id em todos endpoints + isolamento DB)
- [x] Backup/restore automation scripts
- [x] Prometheus + Grafana monitoring stack
- [x] TLS/SSL via nginx reverse proxy
- [x] CI/CD pipeline (GitHub Actions)
- [x] Free tier deployment otimizado

### 🔄 Phase 3: Performance & Scale (EM ANDAMENTO / PLANEJADO)
- [ ] GPU acceleration para embedding generation
- [ ] Batch embedding processing (acumular chunks)
- [ ] Read replicas para search queries
- [ ] Sharding strategies para massive scale
- [ ] Edge deployment options
- [ ] Sentence-aware chunking (spaCy/NLTK)
- [ ] Token-based chunking para LLM integration

### 🔮 Phase 4: Intelligence & UX (FUTURO)
- [ ] Query understanding and expansion
- [ ] Re-ranking com cross-encoders
- [ ] Conversational search interface
- [ ] Analytics dashboard em padrões de uso
- [ ] Automated metadata extraction e tagging
- [ ] OAuth2/JWT + RBAC completo
- [ ] Audit logging para compliance

## Conclusion

The Enterprise RAG pipeline provides a robust, scalable foundation for document-centric AI applications. Its event-driven architecture, resilience patterns, and clear separation of concerns enable evolution from MVP to enterprise-grade system. The implementation balances simplicity with extensibility, allowing teams to adapt components as requirements evolve while maintaining operational excellence.

Key strengths:
- **Resilience**: Idempotency, graceful error handling, retry patterns
- **Observability**: Comprehensive logging, metrics, health checks
- **Performance**: Efficient hybrid search with RRF fusion
- **Scalability**: Horizontal scaling via stateless services and message queuing
- **Maintainability**: Modular design, clear contracts, comprehensive documentation

This system is ready for production deployment with the recommended enhancements for security, monitoring, and operational maturity.