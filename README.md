# Enterprise RAG Pipeline

A scalable, event-driven RAG (Retrieval-Augmented Generation) pipeline for enterprise document ingestion and hybrid search.

## Quick Start

### Free / production setup

Use [Security and operations](docs/SECURITY_OPERATIONS.md) for credentials,
trusted TLS certificates, migrations, monitoring and restore instructions.
The standalone files target a Linux Docker host; cost depends on its provider.

```bash
# 1. Clone o repositório
git clone git@github.com:PauloHFA/Enterprise-rag-pipeline.git enterprise-rag
cd enterprise-rag

# 2. Configure variáveis de ambiente
cp .env.free.example .env.production
# Preencha todos os valores CHANGE_ME.

# 3. Forneça certificados confiáveis em certs/fullchain.pem e certs/privkey.pem.

# 4. Deploy
docker compose --env-file .env.production -f docker-compose.free.yml up -d --build --wait

# 5. Teste
# Configure API_KEY no shell e use o domínio do certificado.
curl -X POST "https://seu-dominio/v1/documents" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@document.pdf" \
  -F "tenant_id=default"
```

### Bootstrap (Linux host)

```bash
# Docker Compose e OpenSSL devem estar instalados.
# Valida ambiente/certificados e inicia os serviços; não instala dependências.
bash scripts/bootstrap-free.sh .env.production
```

### 🔧 Development (Local)

```bash
# Copy .env.example to .env and configure API_KEY first.
# Add --profile monitoring to enable Prometheus/Grafana.
docker compose up -d --build --wait

# Upload a document
curl -X POST "http://localhost:8000/v1/documents" \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf" \
  -F "tenant_id=default"

# Search
curl -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"query": "contract terms", "top_k": 5}'
```

### 🏭 Production (Com Monitoring)

```bash
# Start all services (production with monitoring)
docker-compose -f docker-compose.prod.yml up -d
```

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   API       │────▶│ RabbitMQ    │────▶│  Worker     │
│  (FastAPI)  │     │   Broker    │     │  (Python)   │
└─────────────┘     └─────────────┘     └─────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   MinIO     │     │ PostgreSQL  │     │ Embedding   │
│  (Storage)  │◀────│  + pgvector │◀────│  Model      │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Components

- **API Service**: Document ingestion, status queries, search interface
- **Worker Service**: Asynchronous document processing (extraction, chunking, embedding)
- **RabbitMQ**: Message broker for event-driven communication with DLQ support
- **PostgreSQL + pgvector**: Vector similarity search and full-text search
- **MinIO**: S3-compatible object storage for documents
- **Prometheus + Grafana**: Monitoring and alerting (production)

## Features

- ✅ Event-driven document processing pipeline
- ✅ Hybrid search (lexical + vector) with RRF fusion
- ✅ Idempotent document ingestion (SHA-256 deduplication)
- ✅ Multi-format text extraction (PDF, DOCX, HTML, TXT)
- ✅ Semantic chunking with overlap
- ✅ Multilingual embeddings (Portuguese supported)
- ✅ Tenant isolation
- ✅ Health checks and metrics
- ✅ API Key authentication
- ✅ Rate limiting (sliding window)
- ✅ Dead Letter Queue (DLQ) for failed messages
- ✅ Bounded retry logic with retry count and DLQ routing
- ✅ Comprehensive Prometheus metrics
- ✅ Grafana dashboards
- ✅ Backup/Restore scripts
- ✅ TLS/SSL ready (nginx reverse proxy)
- ✅ CI/CD pipeline (GitHub Actions)

## Documentation

See [docs/DOCUMENT_PROCESSING.md](docs/DOCUMENT_PROCESSING.md) for structured extraction, token-aware chunking, batched embeddings, OCR and source citations. The ingestion terminal status is now `indexed`; update clients that wait for `completed` and reindex older documents to populate provenance.

See [docs/RELIABLE_DELIVERY.md](docs/RELIABLE_DELIVERY.md) for transactional outbox, retry/DLQ recovery and storage cleanup. Run the `outbox` service together with the API and workers; document deletion removes the storage object eventually through durable cleanup.

See [docs/CORRECTION_ROADMAP.md](docs/CORRECTION_ROADMAP.md) for the current correction stages and acceptance criteria. Earlier completion labels in this README and the implementation plan are historical; production readiness still requires the checks in that roadmap.

See [docs/ENTERPRISE_RAG_DOCUMENTATION.md](docs/ENTERPRISE_RAG_DOCUMENTATION.md) for comprehensive technical documentation (Português BR).

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for production deployment roadmap.

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
# Edit .env with your production values
```

Key variables:
- `DATABASE_URL` - PostgreSQL connection string
- `RABBITMQ_URL` - RabbitMQ connection string
- `MINIO_ENDPOINT` - MinIO/S3 endpoint
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` - MinIO credentials
- `API_KEY` - API authentication key (generate with `openssl rand -hex 32`)
- `TENANT_API_KEYS` - optional JSON object mapping tenant IDs to API keys, e.g. `{"acme":"key-a"}`
- `EMBEDDING_MODEL_NAME` - Sentence transformer model
- `RATE_LIMIT_PER_MINUTE` - API rate limit

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[api,worker,dev]"

# Run unit tests
pytest tests/test_api.py tests/test_worker.py -v

# Run integration tests (requires services)
# Set RAG_E2E_URL=http://localhost:8000 and RAG_E2E_API_KEY to the running API key.
# Uses real PostgreSQL, RabbitMQ, MinIO, worker and embeddings; skipped without these variables.
pytest tests/test_integration.py -v

# Run with coverage
pytest --cov=api --cov=worker --cov-report=html
```

### Linting & Type Checking

```bash
# Lint
ruff check api/ worker/ tests/

# Type check
mypy api/ worker/
```

## Production deployment and operations

The standalone production Compose starts two workers; free starts one.
Both require explicit passwords and trusted TLS certificates. Only Nginx 80/443
is public; backend services stay on the Docker network. Grafana 3000 and
Prometheus 9090 bind to loopback for SSH tunnel access. Public production
deployment has not been exercised.

API docs are at `/docs`. The proxy blocks `/metrics`. Local monitoring starts
with `docker compose --profile monitoring up -d`. Grafana uses admin and
GRAFANA_PASSWORD (development default: local-monitoring-only). The repository
provisions its datasource, dashboard and Prometheus alert rules.

Alembic runs at API startup; API and worker share `shared/models.py`.
Uploads default to 20 MiB, with content validation and extraction in a
resource-limited subprocess. API keys and RS256 JWT/OIDC are supported.

Backup requires stopped writers and a persistent destination. Restore requires
an isolated empty database/bucket. Follow the complete configuration, commands
and recovery checks in [Security and operations](docs/SECURITY_OPERATIONS.md).
[Operational scripts](scripts/README.md) wrap the same commands and fail when
any required component is missing. Kubernetes and managed service adapters
remain extensions.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/documents` | Upload document |
| GET | `/v1/documents/{id}` | Get document status |
| DELETE | `/v1/documents/{id}` | Delete document |
| POST | `/v1/documents/{id}/reindex` | Reindex document |
| POST | `/v1/search` | Hybrid search |
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe |
| GET | `/metrics` | Prometheus metrics |

## Search Modes

- `hybrid` (default): RRF fusion of lexical + vector
- `lexical`: PostgreSQL full-text search only
- `vector`: pgvector similarity search only

## License

MIT

Security, migrations, observability and backup/restore: [stage 4](docs/SECURITY_OPERATIONS.md).
