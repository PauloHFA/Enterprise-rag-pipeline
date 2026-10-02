# Enterprise RAG Pipeline - Plano de Implementação e Deploy

## 📋 Resumo Executivo

Este documento detalha o estado atual da implementação, as lacunas para produção e um plano passo a passo para completar o deploy do sistema Enterprise RAG.

---

## ✅ **O que já foi Implementado**

### 1. **API Service (FastAPI)**
- **Endpoints completos:**
  - `POST /v1/documents` - Upload de documentos com idempotência SHA-256
  - `GET /v1/documents/{id}` - Consulta de status
  - `DELETE /v1/documents/{id}` - Exclusão (cascata para chunks + MinIO)
  - `POST /v1/documents/{id}/reindex` - Reindexação
  - `POST /v1/search` - Busca híbrida (lexical + vector) com RRF fusion
  - `GET /healthz` / `GET /readyz` - Health checks
  - `GET /metrics` - Endpoint Prometheus (placeholder)

- **Segurança:** API Key authentication via header `X-API-Key`
- **Configuração:** Pydantic Settings com validação de tipos

### 2. **Worker Service (Processamento Assíncrono)**
- **Pipeline completo:**
  - Extração de texto: PDF (PyMuPDF), DOCX (python-docx), HTML (BeautifulSoup), TXT
  - Chunking: Baseado em caracteres com overlap configurável
  - Embeddings: Sentence-Transformers (multilingual, suporte a PT-BR)
  - Indexação: PostgreSQL + pgvector (vector) + tsvector (lexical)

- **Mensageria:** RabbitMQ com filas duráveis (`document.upload.queue`, `document.reindex.queue`)
- **Idempotência:** SHA-256 previne processamento duplicado

### 3. **Busca Híbrida com RRF Fusion**
- **Implementação SQL nativa** (performance otimizada):
  - Busca léxica: `ts_rank_cd` + `websearch_to_tsquery` (português)
  - Busca vetorial: `embedding <=> query_embedding` (cosine similarity via pgvector)
  - RRF: `score = weight_lexical/(k + rank_lexical) + weight_vector/(k + rank_vector)`
  - Filtros: tenant_id, datas, tags (via metadata JSONB)

### 4. **Infraestrutura (Docker Compose)**
- **Serviços orquestrados:**
  - PostgreSQL 15 + pgvector
  - RabbitMQ 3 Management
  - MinIO (S3-compatible)
  - API Service (porta 8000)
  - Worker Service

### 5. **Documentação**
- `docs/ENTERPRISE_RAG_DOCUMENTATION.md` - Documentação técnica completa (PT-BR)
- `README.md` - Quick start e visão geral

---

## ❌ **Lacunas para Produção**

| Item | Status | Ação Necessária |
|------|--------|-----------------|
| **Credenciais reais** | ❌ | Copiar `.env.example` → `.env` e preencher valores de produção |
| **Testes** | ❌ | Unit tests + Integration tests (pytest) |
| **CI/CD** | ❌ | GitHub Actions pipeline (build, test, deploy) |
| **Rate Limiting** | ⚠️ | Configurado no settings, não implementado no middleware |
| **DLQ (Dead Letter Queue)** | ⚠️ | Estrutura pronta, precisa configurar no RabbitMQ |
| **Monitoring/Alerting** | ⚠️ | Prometheus endpoint existe, falta Grafana dashboards + alertas |
| **TLS/SSL** | ❌ | Certificados para produção (Let's Encrypt / cert-manager) |
| **Backup Strategy** | ❌ | Scripts de backup/restore PostgreSQL + MinIO |

---

## 🚀 **Plano de Implementação - Próximas Etapas**

### **FASE 1: Fundação e Testes (Semana 1-2)**

#### 1.1 Configuração de Ambiente de Produção
```bash
# Copiar e configurar variáveis de ambiente
cp .env.example .env
# Editar .env com valores de produção:
# - DATABASE_URL (managed PostgreSQL: RDS, Cloud SQL, Azure Database)
# - RABBITMQ_URL (managed: CloudAMQP, Azure Service Bus, etc.)
# - MINIO_ENDPOINT (ou S3 real: AWS S3, MinIO standalone, Azure Blob)
# - API_KEY (gerar chave forte: openssl rand -hex 32)
# - EMBEDDING_MODEL_NAME (confirmar modelo)
```

#### 1.2 Testes Unitários (API)
```python
# tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_upload_document_invalid_api_key():
    response = client.post("/v1/documents", files={"file": ("test.txt", b"content")})
    assert response.status_code == 401

def test_upload_document_valid():
    response = client.post(
        "/v1/documents",
        files={"file": ("test.txt", b"test content")},
        headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 202
    assert "id" in response.json()
```

#### 1.3 Testes Unitários (Worker)
```python
# tests/test_worker.py
import pytest
from worker.worker import extract_text, chunk_text, generate_embedding

def test_extract_text_pdf():
    # Mock PDF content
    pass

def test_chunk_text():
    text = "A" * 1000
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 3  # 500 + 450 + 50 overlap
    assert chunks[0][-50:] == chunks[1][:50]  # overlap check

def test_generate_embedding():
    embedding = generate_embedding("test query")
    assert len(embedding) == 384  # EMBEDDING_DIMENSION
    assert all(isinstance(x, float) for x in embedding)
```

#### 1.4 Testes de Integração
```python
# tests/test_integration.py
import pytest
import docker
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer
from testcontainers.minio import MinioContainer

@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:15") as pg:
        # Install pgvector extension
        yield pg

@pytest.fixture(scope="session")
def rabbitmq():
    with RabbitMqContainer("rabbitmq:3-management") as rmq:
        yield rmq

@pytest.fixture(scope="session")
def minio():
    with MinioContainer("minio/minio") as mc:
        yield mc

def test_full_pipeline(postgres, rabbitmq, minio):
    # 1. Start API and Worker with test containers
    # 2. Upload document via API
    # 3. Wait for worker to process
    # 4. Search and verify results
    pass
```

#### 1.5 Configurar pytest e Coverage
```toml
# pyproject.toml (adicionar)
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "--cov=api --cov=worker --cov-report=term-missing --cov-report=html"

[tool.coverage.run]
source = ["api", "worker"]
omit = ["*/tests/*", "*/migrations/*"]
```

---

### **FASE 2: CI/CD Pipeline (Semana 2-3)**

#### 2.1 GitHub Actions - Build e Test
```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: raguser
          POSTGRES_PASSWORD: ragpass
          POSTGRES_DB: ragdb
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      rabbitmq:
        image: rabbitmq:3-management
        env:
          RABBITMQ_DEFAULT_USER: raguser
          RABBITMQ_DEFAULT_PASS: ragpass
        ports: ["5672:5672"]
      minio:
        image: minio/minio
        command: server /data --console-address ":9001"
        env:
          MINIO_ROOT_USER: minioadmin
          MINIO_ROOT_PASSWORD: minioadmin
        ports: ["9000:9000"]

    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      
      - name: Install dependencies
        run: |
          pip install -r api/requirements.txt
          pip install -r worker/requirements.txt
          pip install pytest pytest-cov pytest-asyncio httpx
      
      - name: Run tests
        env:
          DATABASE_URL: postgresql://raguser:ragpass@localhost:5432/ragdb
          RABBITMQ_URL: amqp://raguser:ragpass@localhost:5672
          MINIO_ENDPOINT: localhost:9000
          MINIO_ACCESS_KEY: minioadmin
          MINIO_SECRET_KEY: minioadmin
          API_KEY: test-key
        run: pytest --cov=api --cov=worker
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

#### 2.2 GitHub Actions - Build Docker Images
```yaml
# .github/workflows/docker.yml
name: Docker Build

on:
  push:
    branches: [main]
    tags: ["v*"]

jobs:
  build-api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build API image
        run: docker build -t ${{ secrets.DOCKERHUB_USERNAME }}/enterprise-rag-api:${{ github.sha }} ./api
      - name: Push API image
        run: docker push ${{ secrets.DOCKERHUB_USERNAME }}/enterprise-rag-api:${{ github.sha }}

  build-worker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Worker image
        run: docker build -t ${{ secrets.DOCKERHUB_USERNAME }}/enterprise-rag-worker:${{ github.sha }} ./worker
      - name: Push Worker image
        run: docker push ${{ secrets.DOCKERHUB_USERNAME }}/enterprise-rag-worker:${{ github.sha }}
```

#### 2.3 GitHub Actions - Deploy (Kubernetes/ACA)
```yaml
# .github/workflows/deploy.yml
name: Deploy to Production

on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment to deploy'
        required: true
        type: choice
        options: [staging, production]

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: ${{ github.event.inputs.environment }}
    steps:
      - uses: actions/checkout@v4
      
      - name: Azure Login
        uses: azure/login@v1
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}
      
      - name: Deploy to Azure Container Apps
        run: |
          az containerapp up \
            --name enterprise-rag-api \
            --resource-group ${{ secrets.AZURE_RG }} \
            --image ${{ secrets.DOCKERHUB_USERNAME }}/enterprise-rag-api:${{ github.sha }} \
            --ingress external \
            --target-port 8000 \
            --env-vars \
              DATABASE_URL=${{ secrets.DATABASE_URL }} \
              RABBITMQ_URL=${{ secrets.RABBITMQ_URL }} \
              MINIO_ENDPOINT=${{ secrets.MINIO_ENDPOINT }} \
              MINIO_ACCESS_KEY=${{ secrets.MINIO_ACCESS_KEY }} \
              MINIO_SECRET_KEY=${{ secrets.MINIO_SECRET_KEY }} \
              API_KEY=${{ secrets.API_KEY }}
```

---

### **FASE 3: Resiliência e Observabilidade (Semana 3-4)**

#### 3.1 Rate Limiting (Middleware FastAPI)
```python
# api/rate_limiter.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import time
from collections import defaultdict
from .config import settings

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/healthz", "/readyz", "/metrics"]:
            return await call_next(request)
        
        # Get client identifier (API key or IP)
        api_key = request.headers.get("X-API-Key")
        client_id = api_key or request.client.host
        
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests
        self.requests[client_id] = [t for t in self.requests[client_id] if t > minute_ago]
        
        # Check limit
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."}
            )
        
        # Record request
        self.requests[client_id].append(now)
        
        response = await call_next(request)
        return response
```

```python
# api/main.py - Adicionar middleware
from .rate_limiter import RateLimitMiddleware

app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)
```

#### 3.2 Dead Letter Queue (DLQ) - RabbitMQ
```python
# api/rabbitmq_client.py - Atualizar setup_queues
async def setup_queues(self):
    # Main queues
    await self.channel.declare_queue("document.upload.queue", durable=True)
    await self.channel.declare_queue("document.reindex.queue", durable=True)
    
    # DLQ queues
    await self.channel.declare_queue("document.upload.dlq", durable=True)
    await self.channel.declare_queue("document.reindex.dlq", durable=True)
    
    # Dead letter exchanges
    await self.channel.declare_exchange("document.dlx", "direct", durable=True)
    
    # Bind DLQs to DLX
    await self.channel.bind_queue("document.upload.dlq", "document.dlx", "document.upload.dlq")
    await self.channel.bind_queue("document.reindex.dlq", "document.dlx", "document.reindex.dlq")
    
    # Main queues with DLX
    await self.channel.declare_queue(
        "document.upload.queue",
        durable=True,
        arguments={
            "x-dead-letter-exchange": "document.dlx",
            "x-dead-letter-routing-key": "document.upload.dlq",
            "x-message-ttl": 86400000  # 24h TTL
        }
    )
    await self.channel.declare_queue(
        "document.reindex.queue",
        durable=True,
        arguments={
            "x-dead-letter-exchange": "document.dlx",
            "x-dead-letter-routing-key": "document.reindex.dlq",
            "x-message-ttl": 86400000
        }
    )
```

#### 3.3 Worker - Retry Logic e DLQ Handling
```python
# worker/worker.py - Atualizar process_document_message
def process_document_message(ch, method, properties, body):
    max_retries = 3
    retry_count = properties.headers.get("x-retry-count", 0) if properties.headers else 0
    
    try:
        # ... existing processing logic ...
    except Exception as e:
        logger.error(f"Error processing message (attempt {retry_count + 1}): {e}")
        
        if retry_count < max_retries:
            # Requeue with incremented retry count
            new_headers = dict(properties.headers or {})
            new_headers["x-retry-count"] = retry_count + 1
            
            ch.basic_publish(
                exchange="",
                routing_key=method.routing_key,
                body=body,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    headers=new_headers
                )
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Max retries exceeded - send to DLQ (handled automatically by RabbitMQ)
            logger.error(f"Max retries exceeded for document {document_id}, sending to DLQ")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
```

#### 3.4 Prometheus Metrics Completo
```python
# api/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import Response

# Metrics
DOCUMENTS_UPLOADED = Counter("enterprise_rag_documents_uploaded_total", "Total documents uploaded", ["tenant_id", "status"])
DOCUMENTS_PROCESSED = Counter("enterprise_rag_documents_processed_total", "Total documents processed", ["tenant_id", "status"])
SEARCH_REQUESTS = Counter("enterprise_rag_search_requests_total", "Total search requests", ["mode", "status"])
SEARCH_LATENCY = Histogram("enterprise_rag_search_latency_seconds", "Search latency in seconds", ["mode"])
WORKER_PROCESSING_TIME = Histogram("enterprise_rag_worker_processing_seconds", "Worker processing time", ["stage"])
ACTIVE_WORKERS = Gauge("enterprise_rag_active_workers", "Number of active workers")
QUEUE_DEPTH = Gauge("enterprise_rag_queue_depth", "Queue depth", ["queue_name"])

# api/main.py - Atualizar endpoint /metrics
from .metrics import generate_latest

@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type="text/plain")
```

#### 3.5 Grafana Dashboards
```json
// grafana/dashboards/enterprise-rag.json
{
  "dashboard": {
    "title": "Enterprise RAG Pipeline",
    "panels": [
      {
        "title": "Documents Uploaded",
        "type": "graph",
        "targets": [{"expr": "rate(enterprise_rag_documents_uploaded_total[5m])"}]
      },
      {
        "title": "Search Latency (p95)",
        "type": "graph",
        "targets": [{"expr": "histogram_quantile(0.95, rate(enterprise_rag_search_latency_seconds_bucket[5m]))"}]
      },
      {
        "title": "Queue Depth",
        "type": "graph",
        "targets": [
          {"expr": "enterprise_rag_queue_depth{queue_name=\"document.upload.queue\"}"},
          {"expr": "enterprise_rag_queue_depth{queue_name=\"document.reindex.queue\"}"}
        ]
      },
      {
        "title": "Worker Processing Time",
        "type": "graph",
        "targets": [{"expr": "rate(enterprise_rag_worker_processing_seconds_sum[5m]) / rate(enterprise_rag_worker_processing_seconds_count[5m])"}]
      }
    ]
  }
}
```

---

### **FASE 4: Segurança e Produção (Semana 4-5)**

#### 4.1 TLS/SSL Configuration
```yaml
# docker-compose.prod.yml (adicionar reverse proxy)
services:
  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    ports:
      - "443:443"
      - "80:80"
    depends_on:
      - api
```

```nginx
# nginx/nginx.conf
events {
    worker_connections 1024;
}

http {
    upstream api {
        server api:8000;
    }
    
    server {
        listen 80;
        server_name rag.example.com;
        return 301 https://$server_name$request_uri;
    }
    
    server {
        listen 443 ssl http2;
        server_name rag.example.com;
        
        ssl_certificate /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        
        location / {
            proxy_pass http://api;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
```

#### 4.2 Backup Strategy
```bash
#!/bin/bash
# scripts/backup.sh
set -e

BACKUP_DIR="/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# PostgreSQL backup
pg_dump -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  --no-owner --no-privileges --format=custom \
  > "$BACKUP_DIR/postgres.dump"

# MinIO backup (using mc mirror)
mc mirror --overwrite minio/documents "$BACKUP_DIR/minio/"

# Compress
tar -czf "$BACKUP_DIR.tar.gz" -C "$BACKUP_DIR" .
rm -rf "$BACKUP_DIR"

# Upload to cloud storage (optional)
# aws s3 cp "$BACKUP_DIR.tar.gz" s3://my-backup-bucket/enterprise-rag/

# Cleanup old backups (keep last 30 days)
find /backups -name "*.tar.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR.tar.gz"
```

```bash
#!/bin/bash
# scripts/restore.sh
set -e

BACKUP_FILE=$1
if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup-file.tar.gz>"
    exit 1
fi

# Extract
TEMP_DIR=$(mktemp -d)
tar -xzf "$BACKUP_FILE" -C "$TEMP_DIR"

# Restore PostgreSQL
pg_restore -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB \
  --clean --if-exists --no-owner --no-privileges \
  "$TEMP_DIR/postgres.dump"

# Restore MinIO
mc mirror --overwrite "$TEMP_DIR/minio/" minio/documents

# Cleanup
rm -rf "$TEMP_DIR"

echo "Restore completed from $BACKUP_FILE"
```

#### 4.3 Security Hardening
```python
# api/security.py - Enhancements
from fastapi import Security, HTTPException, Depends
from fastapi.security import APIKeyHeader
import secrets
import hashlib

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

def hash_api_key(api_key: str) -> str:
    """Hash API key for storage (never store plaintext)."""
    return hashlib.sha256(api_key.encode()).hexdigest()

def verify_api_key(provided_key: str, stored_hash: str) -> bool:
    """Verify API key against stored hash."""
    return secrets.compare_digest(hash_api_key(provided_key), stored_hash)

async def get_api_key(api_key: str = Security(API_KEY_HEADER)):
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key required")
    
    # In production, fetch from secure store (Vault, Key Vault, etc.)
    # For now, compare with env var hash
    expected_hash = hash_api_key(settings.API_KEY)
    if not verify_api_key(api_key, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid API Key")
    
    return api_key
```

---

### **FASE 5: Otimizações Avançadas (Semana 5-6)**

#### 5.1 Melhorias no Chunking
```python
# worker/chunker.py (novo arquivo)
import re
from typing import List, Tuple

class SemanticChunker:
    """Chunk text respecting sentence/paragraph boundaries."""
    
    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk(self, text: str) -> List[Tuple[str, int, int]]:
        """
        Returns list of (chunk_text, start_char, end_char).
        Tracks character positions for page mapping.
        """
        # Split by paragraphs first
        paragraphs = re.split(r'\n\s*\n', text)
        
        chunks = []
        current_chunk = ""
        current_start = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # If adding this paragraph exceeds chunk_size, finalize current chunk
            if len(current_chunk) + len(para) > self.chunk_size and current_chunk:
                chunks.append((current_chunk, current_start, current_start + len(current_chunk)))
                # Start new chunk with overlap
                overlap_text = current_chunk[-self.overlap:] if len(current_chunk) > self.overlap else current_chunk
                current_chunk = overlap_text + "\n\n" + para
                current_start = current_start + len(current_chunk) - len(overlap_text) - len(para) - 2
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
                    current_start = text.find(para)
        
        # Add final chunk
        if current_chunk:
            chunks.append((current_chunk, current_start, current_start + len(current_chunk)))
        
        return chunks
```

#### 5.2 Hybrid Search Otimizado
```python
# api/search.py - Adicionar busca com reranking
def hybrid_search_with_rerank(
    db: Session,
    query_text: str,
    top_k: int = 10,
    rerank_top_n: int = 50,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Two-stage retrieval: RRF fusion -> Cross-encoder rerank.
    """
    # Stage 1: Get more candidates via RRF
    candidates = hybrid_search(db, query_text, top_k=rerank_top_n, **kwargs)
    
    if not candidates:
        return []
    
    # Stage 2: Rerank with cross-encoder (optional, requires additional model)
    # from sentence_transformers import CrossEncoder
    # reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    # pairs = [(query_text, c["content"]) for c in candidates]
    # scores = reranker.predict(pairs)
    # for c, s in zip(candidates, scores):
    #     c["rerank_score"] = float(s)
    # candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
    
    return candidates[:top_k]
```

#### 5.3 Multi-tenancy Isolation
```python
# api/main.py - Adicionar middleware de tenant
@app.middleware("http")
async def tenant_middleware(request: Request, call_next):
    # Extract tenant from API key or header
    api_key = request.headers.get("X-API-Key")
    tenant_id = request.headers.get("X-Tenant-ID", "default")
    
    # Validate tenant access (in production, check against DB)
    request.state.tenant_id = tenant_id
    
    response = await call_next(request)
    return response
```

---

## 📦 **Checklist de Deploy para Produção**

### Pré-Deploy
- [ ] `.env` configurado com credenciais de produção
- [ ] Testes unitários passando (>80% coverage)
- [ ] Testes de integração passando
- [ ] Docker images buildados e pushados para registry
- [ ] Secrets configurados no CI/CD (GitHub Secrets, Azure Key Vault, etc.)

### Infraestrutura
- [ ] PostgreSQL managed instance (RDS/Cloud SQL/Azure Database) com pgvector
- [ ] RabbitMQ managed (CloudAMQP/Azure Service Bus) ou cluster HA
- [ ] Object Storage (S3/MinIO/Azure Blob) com versioning e lifecycle
- [ ] Kubernetes cluster (AKS/EKS/GKE) ou Azure Container Apps
- [ ] Ingress controller + TLS certificates (cert-manager + Let's Encrypt)
- [ ] Monitoring stack (Prometheus + Grafana + Alertmanager)

### Deploy
- [ ] Helm charts / Kustomize / Bicep / Terraform para infra
- [ ] Blue-green ou rolling deployment configurado
- [ ] Health checks e readiness probes funcionando
- [ ] Horizontal Pod Autoscaler (HPA) configurado
- [ ] Network policies aplicadas

### Pós-Deploy
- [ ] Smoke tests em produção
- [ ] Dashboards Grafana importados e validados
- [ ] Alertas configurados (latência, error rate, queue depth, disk usage)
- [ ] Backup jobs agendados e testados (restore test)
- [ ] Documentação de runbooks atualizada
- [ ] Load test executado (k6/Locust)

---

## 📅 **Cronograma Sugerido**

| Semana | Foco | Entregáveis |
|--------|------|-------------|
| 1-2 | Testes & Qualidade | Test suite completa, coverage >80% |
| 2-3 | CI/CD | Pipeline GitHub Actions funcional |
| 3-4 | Resiliência | Rate limiting, DLQ, métricas completas |
| 4-5 | Segurança & Prod | TLS, backup, security hardening |
| 5-6 | Otimizações | Semantic chunking, reranking, multi-tenancy |

---

## 🔗 **Referências e Recursos**

- [FastAPI Testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [Testcontainers Python](https://testcontainers-python.readthedocs.io/)
- [Prometheus Python Client](https://github.com/prometheus/client_python)
- [RabbitMQ DLX Pattern](https://www.rabbitmq.com/dlx.html)
- [pgvector Documentation](https://github.com/pgvector/pgvector)
- [Sentence Transformers](https://www.sbert.net/)
- [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/)
- [GitHub Actions](https://docs.github.com/actions)

---

## 📝 **Notas de Manutenção**

1. **Modelo de Embedding**: Atualizar periodicamente para versões mais recentes do sentence-transformers
2. **Índices PostgreSQL**: Monitorar performance e adicionar índices parciais conforme necessário
3. **RabbitMQ**: Configurar HA policy e quorum queues para produção
4. **MinIO**: Habilitar versioning e lifecycle policies para retenção
5. **Logs**: Centralizar com Loki/ELK para debugging distribuído

---

*Documento gerado em: 2026-09-29*  
*Versão: 1.0*  
*Status: Pronto para execução*