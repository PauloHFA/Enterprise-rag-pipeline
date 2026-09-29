import os
import uuid
import json
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, BackgroundTasks, Security
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from . import models, database
from .schemas import DocumentCreate, DocumentResponse, ChunkResponse, SearchRequest, SearchResponse
from .minio_client import MinioClient
from .rabbitmq_client import RabbitMQClient
from .config import settings
from .security import get_api_key

app = FastAPI(title="Enterprise RAG API", version="0.1.0")

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
    # Create database tables
    models.Base.metadata.create_all(bind=database.engine)
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
    tenant_id: str = "default",
    metadata: Optional[str] = None,
    db: Session = Depends(get_db),
    api_key: str = Security(get_api_key)
):
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Calculate SHA-256
    import hashlib
    sha256_hash = hashlib.sha256(content).hexdigest()
    
    # Check if document already exists (idempotency)
    existing_doc = db.query(models.Document).filter(models.Document.sha256 == sha256_hash).first()
    if existing_doc:
        return existing_doc
    
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
    
    # Parse metadata if provided
    metadata_dict = {}
    if metadata:
        try:
            metadata_dict = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid metadata JSON")
    
    # Create document record
    db_document = models.Document(
        tenant_id=tenant_id,
        filename=file.filename,
        mime_type=file.content_type,
        sha256=sha256_hash,
        storage_key=storage_key,
        status="queued",
        metadata=metadata_dict
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
async def get_document(document_id: uuid.UUID, db: Session = Depends(get_db), api_key: str = Security(get_api_key)):
    document = db.query(models.Document).filter(models.Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document

@app.delete("/v1/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db), api_key: str = Security(get_api_key)):
    document = db.query(models.Document).filter(models.Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete from MinIO
    minio_client.delete_file(bucket_name="documents", object_name=document.storage_key)
    
    # Delete document (cascades to chunks)
    db.delete(document)
    db.commit()
    return JSONResponse(status_code=204, content=None)

@app.post("/v1/documents/{document_id}/reindex", status_code=202)
async def reindex_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: str = Security(get_api_key)
):
    document = db.query(models.Document).filter(models.Document.id == document_id).first()
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
        "metadata": document.metadata
    }
    background_tasks.add_task(rabbitmq_client.publish_message, "document.reindex", message)
    
    return JSONResponse(status_code=202, content={"message": "Reindexing started"})

@app.post("/v1/search", response_model=SearchResponse)
async def search_documents(
    search_request: SearchRequest,
    db: Session = Depends(get_db),
    api_key: str = Security(get_api_key)
):
    # Import here to avoid circular imports
    from .search import hybrid_search_sql
    import time
    
    start_time = time.time()
    
    # Perform hybrid search
    results = hybrid_search_sql(
        db=db,
        query_text=search_request.query,
        top_k=search_request.top_k,
        mode=search_request.mode,
        filters=search_request.filters,
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
        db.execute("SELECT 1")
        db.close()
        # Check RabbitMQ (if connected)
        if rabbitmq_client.connection and not rabbitmq_client.connection.is_closed:
            return {"status": "ready"}
        else:
            return JSONResponse(status_code=503, content={"status": "not ready", "reason": "RabbitMQ not connected"})
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": str(e)})

@app.get("/metrics")
async def metrics():
    # Placeholder for Prometheus metrics
    return Response(content="# HELP enterprise_rag_info Information about the Enterprise RAG service\n# TYPE enterprise_rag_info gauge\nenterprise_rag_info{version=\"0.1.0\"} 1\n", media_type="text/plain")