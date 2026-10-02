# Enterprise RAG Pipeline

A scalable, event-driven RAG (Retrieval-Augmented Generation) pipeline for enterprise document ingestion and hybrid search.

## Quick Start

### 🆓 Free Tier Deployment (Recommended for Starting)

**100% grátis** - roda em qualquer VM (Oracle Always Free, AWS Free Tier, local, etc.)

```bash
# 1. Clone o repositório
git clone https://github.com/SEU_USUARIO/enterprise-rag.git
cd enterprise-rag

# 2. Configure variáveis de ambiente
cp .env.free .env
# Edite .env e preencha os valores CHANGE_ME (ou rode o bootstrap que gera automaticamente)

# 3. Configure SSL (escolha uma opção):
# Opção A: Cloudflare Tunnel (recomendado, grátis, sem abrir portas)
# Opção B: Let's Encrypt (precisa domínio)
# Opção C: Self-signed para teste (./scripts/bootstrap-free.sh faz isso)

# 4. Deploy
docker compose -f docker-compose.free.yml up -d

# 5. Teste
export API_KEY=$(grep API_KEY .env | cut -d= -f2)
curl -X POST "https://localhost/v1/documents" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@document.pdf" \
  -F "tenant_id=my-tenant"
```

### 🚀 Bootstrap Automatizado (VM Ubuntu)

```bash
# Na VM como root:
curl -fsSL https://raw.githubusercontent.com/SEU_USUARIO/enterprise-rag/main/scripts/bootstrap-free.sh | sudo bash
# Ou clone primeiro e rode localmente:
sudo ./scripts/bootstrap-free.sh
```

### 🔧 Development (Local)

```bash
# Start all services (development)
docker-compose up -d

# Upload a document
curl -X POST "http://localhost:8000/v1/documents" \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf" \
  -F "tenant_id=my-tenant"

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
- ✅ Retry logic with exponential backoff
- ✅ Comprehensive Prometheus metrics
- ✅ Grafana dashboards
- ✅ Backup/Restore scripts
- ✅ TLS/SSL ready (nginx reverse proxy)
- ✅ CI/CD pipeline (GitHub Actions)

## Documentation

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
- `EMBEDDING_MODEL_NAME` - Sentence transformer model
- `RATE_LIMIT_PER_MINUTE` - API rate limit

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests
pytest tests/test_api.py tests/test_worker.py -v

# Run integration tests (requires services)
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

## Production Deployment

### 🆓 Free Tier Deployment (Single VM - Recommended Start)

**Zero custo** - roda em Oracle Always Free, AWS Free Tier, ou qualquer VM Linux.

```bash
# 1. Configure
cp .env.free .env
# Edite .env (ou use bootstrap que gera senhas)

# 2. SSL (escolha uma):
#    A) Cloudflare Tunnel (grátis, sem abrir portas)
#    B) Let's Encrypt (precisa domínio)
#    C) Self-signed para teste

# 3. Deploy
docker compose -f docker-compose.free.yml up -d

# 4. Teste
./scripts/test-deploy.sh
```

**Recursos mínimos recomendados:** 2 CPU, 4 GB RAM, 20 GB disco

### 🏭 Production Deployment (Managed Services)

### Prerequisites

- Kubernetes cluster (AKS/EKS/GKE) or Azure Container Apps
- Managed PostgreSQL with pgvector (RDS, Cloud SQL, Azure Database)
- Managed RabbitMQ (CloudAMQP, Azure Service Bus) or HA cluster
- Object Storage (S3, MinIO, Azure Blob)
- TLS certificates (Let's Encrypt via cert-manager)
- Monitoring stack (Prometheus + Grafana)

### Deploy with Docker Compose (Single VM)

```bash
# Configure production values
cp .env.example .env
# Edit .env with production credentials

# Deploy
docker-compose -f docker-compose.prod.yml up -d
```

### Deploy to Kubernetes

```bash
# Apply manifests (create from docker-compose or use Helm)
kubectl apply -f k8s/

# Or use Azure Container Apps
az containerapp up --name enterprise-rag-api --resource-group my-rg --image myregistry/enterprise-rag-api:latest
```

### Monitoring

#### Free Tier (via nginx proxy)
- **API Docs**: https://seu-dominio/v1/docs
- **Grafana**: https://seu-dominio/grafana (user: admin, senha no .env)
- **Prometheus**: https://seu-dominio:9090 (rede interna apenas)

#### Development (portas diretas)
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)
- **RabbitMQ Management**: http://localhost:15672 (raguser/ragpass)
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

### Backup & Restore

```bash
# Backup
./scripts/backup.sh /mnt/backups

# Restore
./scripts/restore.sh /mnt/backups/20260929_120000.tar.gz
```

See [scripts/README.md](scripts/README.md) for details.

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