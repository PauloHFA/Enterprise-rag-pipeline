# Enterprise RAG Pipeline

A scalable, event-driven RAG (Retrieval-Augmented Generation) pipeline for enterprise document ingestion and hybrid search.

## Quick Start

```bash
# Start all services
docker-compose up -d

# Upload a document
curl -X POST "http://localhost:8000/v1/documents" \
  -F "file=@document.pdf" \
  -F "tenant_id=my-tenant"

# Search
curl -X POST "http://localhost:8000/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "contract terms", "top_k": 5}'
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
- **RabbitMQ**: Message broker for event-driven communication
- **PostgreSQL + pgvector**: Vector similarity search and full-text search
- **MinIO**: S3-compatible object storage for documents

## Features

- ✅ Event-driven document processing pipeline
- ✅ Hybrid search (lexical + vector) with RRF fusion
- ✅ Idempotent document ingestion (SHA-256 deduplication)
- ✅ Multi-format text extraction (PDF, DOCX, HTML, TXT)
- ✅ Semantic chunking with overlap
- ✅ Multilingual embeddings (Portuguese supported)
- ✅ Tenant isolation
- ✅ Health checks and metrics

## Documentation

See [docs/ENTERPRISE_RAG_DOCUMENTATION.md](docs/ENTERPRISE_RAG_DOCUMENTATION.md) for comprehensive technical documentation.

## License

MIT