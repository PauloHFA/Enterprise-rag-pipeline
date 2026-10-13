import hashlib
import uuid
import json
import time
from typing import Optional
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends, BackgroundTasks, Security
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from . import models, database
from .schemas import DocumentCreate, DocumentResponse, ChunkResponse, SearchRequest, SearchResponse, SearchResult
from .minio_client import MinioClient
from .rabbitmq_client import RabbitMQClient
from .config import settings
from .security import get_tenant_id
from .rate_limiter import RateLimitMiddleware

app = FastAPI(title="Enterprise RAG API", version="0.1.0")

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)

# Dependency
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Initialize clients
minio_client = MinioClient()
rabbitmq_client = RabbitMQClient()

@app.on_event("startup")
async def startup_event():
    minio_client._ensure_bucket_exists()
    with database.engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
        models.Base.metadata.create_all(bind=connection)
    # Connect to RabbitMQ and set up queues
    await rabbitmq_client.connect()
    await rabbitmq_client.setup_queues()

@app.on_event("shutdown")
async def shutdown_event():
    await rabbitmq_client.close()

@app.post("/v1/documents", response_model=DocumentResponse, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: Optional[str] = Form(default=None),
    metadata: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id)
):
    if tenant_id is not None and tenant_id != authenticated_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    tenant_id = authenticated_tenant_id

    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Calculate SHA-256
    sha256_hash = hashlib.sha256(content).hexdigest()
    
    # Check if document already exists (idempotency)
    existing_doc = db.query(models.Document).filter(
        models.Document.sha256 == sha256_hash,
        models.Document.tenant_id == tenant_id,
    ).first()
    if existing_doc:
        return existing_doc
    
    metadata_dict = {}
    if metadata:
        try:
            metadata_dict = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid metadata JSON") from exc
        if not isinstance(metadata_dict, dict):
            raise HTTPException(status_code=400, detail="Metadata must be a JSON object")

    # Determine storage key
    storage_key = f"{tenant_id}/{uuid.uuid4()}_{file.filename}"
    
    # Upload to MinIO
    minio_client.upload_file(
        bucket_name="documents",
        object_name=storage_key,
        data=content,
        length=file_size,
        content_type=file.content_type
    )
    
    # Create document record
    db_document = models.Document(
        tenant_id=tenant_id,
        filename=file.filename,
        mime_type=file.content_type,
        sha256=sha256_hash,
        storage_key=storage_key,
        status="queued",
        metadata_=metadata_dict
    )
    db.add(db_document)
    db.commit()
    db.refresh(db_document)
    
    # Send message to RabbitMQ for processing
    message = {
        "document_id": str(db_document.id),
        "tenant_id": tenant_id,
        "storage_key": storage_key,
        "filename": file.filename,
        "mime_type": file.content_type,
        "metadata": metadata_dict
    }
    background_tasks.add_task(rabbitmq_client.publish_message, "document.upload", message)
    
    return db_document

@app.get("/v1/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    document = db.query(models.Document).filter(
        models.Document.id == document_id,
        models.Document.tenant_id == authenticated_tenant_id,
    ).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document

@app.delete("/v1/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    document = db.query(models.Document).filter(
        models.Document.id == document_id,
        models.Document.tenant_id == authenticated_tenant_id,
    ).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete from MinIO
    minio_client.delete_file(bucket_name="documents", object_name=document.storage_key)
    
    # Delete document (cascades to chunks)
    db.delete(document)
    db.commit()
    return Response(status_code=204)

@app.post("/v1/documents/{document_id}/reindex", status_code=202)
async def reindex_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id)
):
    document = db.query(models.Document).filter(
        models.Document.id == document_id,
        models.Document.tenant_id == authenticated_tenant_id,
    ).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Reset status to queued
    document.status = "queued"
    document.error_detail = None
    db.commit()
    
    # Send message to RabbitMQ for reprocessing
    message = {
        "document_id": str(document.id),
        "tenant_id": document.tenant_id,
        "storage_key": document.storage_key,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "metadata": document.metadata_
    }
    background_tasks.add_task(rabbitmq_client.publish_message, "document.reindex", message)
    
    return JSONResponse(status_code=202, content={"message": "Reindexing started"})

@app.post("/v1/search", response_model=SearchResponse)
async def search_documents(
    search_request: SearchRequest,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id)
):
    from .search import hybrid_search
    
    start_time = time.time()

    filters = dict(search_request.filters or {})
    requested_tenant_id = filters.get("tenant_id")
    if requested_tenant_id is not None and requested_tenant_id != authenticated_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    filters["tenant_id"] = authenticated_tenant_id
    
    # Perform hybrid search
    results = hybrid_search(
        db=db,
        query_text=search_request.query,
        top_k=search_request.top_k,
        mode=search_request.mode,
        filters=filters,
        rrf_k=search_request.rrf.get("k", 60) if search_request.rrf else 60,
        weights=search_request.rrf.get("weights", {"lexical": 1.0, "vector": 1.0}) if search_request.rrf else {"lexical": 1.0, "vector": 1.0}
    )
    
    took_ms = int((time.time() - start_time) * 1000)
    
    # Convert results to SearchResponse format
    search_results = []
    for result in results:
        search_results.append(
            SearchResult(
                chunk_id=result["id"],
                document_id=result["document_id"],
                score=result["score"],
                page=result.get("page"),
                snippet=result.get("snippet", ""),
                ranks=result.get("ranks", {"lexical": 0, "vector": 0})
            )
        )
    
    return SearchResponse(results=search_results, took_ms=took_ms)

@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

@app.get("/readyz")
async def readiness_check():
    # Check database and RabbitMQ connections
    try:
        # Simple database check
        db = database.SessionLocal()
        db.execute(text("SELECT 1"))
        # Check RabbitMQ (if connected)
        if rabbitmq_client.connection and not rabbitmq_client.connection.is_closed:
            return {"status": "ready"}
        else:
            return JSONResponse(status_code=503, content={"status": "not ready", "reason": "RabbitMQ not connected"})
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": str(e)})
    finally:
        if "db" in locals():
            db.close()

@app.get("/metrics")
async def metrics():
    from .metrics import get_metrics_response
    return get_metrics_response()
