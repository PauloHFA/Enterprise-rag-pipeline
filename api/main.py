import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from shared.telemetry import configure_logging

from . import database, metrics, models
from .config import settings
from .minio_client import MinioClient
from .observability import ObservabilityMiddleware
from .outbox import enqueue_document, enqueue_storage_delete
from .rabbitmq_client import RabbitMQClient
from .rate_limiter import RateLimitMiddleware
from .schema import initialize_schema
from .schemas import (
    DocumentResponse,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .security import get_tenant_id
from .uploads import UploadLimitMiddleware, validate_upload

configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    from .security import validate_auth_configuration

    validate_auth_configuration()
    minio_client._ensure_bucket_exists()
    initialize_schema()
    yield
    await rabbitmq_client.close()


app = FastAPI(title="Enterprise RAG API", version="0.1.0", lifespan=lifespan)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)


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


@app.post("/v1/documents", response_model=DocumentResponse, status_code=202)
def upload_document(
    file: UploadFile = File(...),
    tenant_id: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    if tenant_id is not None and tenant_id != authenticated_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    tenant_id = authenticated_tenant_id

    # Read file content
    content = file.file.read(settings.MAX_UPLOAD_BYTES + 1)
    filename, mime_type = validate_upload(content, file.filename, file.content_type)
    file_size = len(content)

    # Calculate SHA-256
    sha256_hash = hashlib.sha256(content).hexdigest()
    # The same content in the same tenant gets one document and one event, even concurrently.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"upload:{tenant_id}:{sha256_hash}"},
    )

    # Check if document already exists (idempotency)
    existing_doc = (
        db.query(models.Document)
        .filter(
            models.Document.sha256 == sha256_hash,
            models.Document.tenant_id == tenant_id,
        )
        .first()
    )
    if existing_doc:
        db.commit()  # Release the hash lock before returning the duplicate response.
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
    storage_key = f"{tenant_id}/{uuid.uuid4()}_{filename}"

    # Upload to MinIO
    minio_client.upload_file(
        bucket_name=settings.MINIO_BUCKET,
        object_name=storage_key,
        data=content,
        length=file_size,
        content_type=mime_type,
    )

    # Create document record
    db_document = models.Document(
        id=uuid.uuid4(),
        generation=1,
        indexed_generation=0,
        extraction_metadata={},
        processing_history=[
            {"status": "queued", "generation": 1, "at": datetime.now(UTC).isoformat()}
        ],
        tenant_id=tenant_id,
        filename=filename,
        mime_type=mime_type,
        sha256=sha256_hash,
        storage_key=storage_key,
        status="queued",
        metadata_=metadata_dict,
    )
    db.add(db_document)
    enqueue_document(db, db_document, "document.upload")
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        # A dropped connection during COMMIT has an ambiguous outcome. Verify first.
        try:
            committed = (
                db.query(models.Document)
                .filter(
                    models.Document.sha256 == sha256_hash,
                    models.Document.tenant_id == tenant_id,
                )
                .first()
            )
        except SQLAlchemyError:
            logger.error(
                "Cannot verify upload commit; storage key needs reconciliation: %s", storage_key
            )
            raise HTTPException(
                status_code=503, detail="Upload could not be confirmed; retry the same file"
            ) from exc
        if committed is not None and committed.storage_key == storage_key:
            return committed
        try:
            minio_client.delete_file(settings.MINIO_BUCKET, storage_key)
        except Exception:
            db.rollback()
            enqueue_storage_delete(db, storage_key)
            db.commit()
        if committed is not None:
            return committed
        raise HTTPException(
            status_code=503, detail="Upload could not be saved; retry the same file"
        ) from exc
    db.refresh(db_document)
    metrics.record_document_upload("all", "accepted")

    return db_document


@app.get("/v1/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    document = (
        db.query(models.Document)
        .filter(
            models.Document.id == document_id,
            models.Document.tenant_id == authenticated_tenant_id,
        )
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@app.delete("/v1/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"document:{document_id}"},
    )
    document = (
        db.query(models.Document)
        .filter(
            models.Document.id == document_id,
            models.Document.tenant_id == authenticated_tenant_id,
        )
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete DB records atomically; storage cleanup is durable and eventually retried.
    enqueue_storage_delete(db, document.storage_key)
    db.delete(document)
    db.commit()
    metrics.record_document_deleted("all")
    return Response(status_code=204)


@app.post("/v1/documents/{document_id}/reindex", status_code=202)
def reindex_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"document:{document_id}"},
    )
    document = (
        db.query(models.Document)
        .filter(
            models.Document.id == document_id,
            models.Document.tenant_id == authenticated_tenant_id,
        )
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Reset status to queued
    document.status = "queued"
    document.error_detail = None
    document.generation += 1
    document.processing_history = [
        {"status": "queued", "generation": document.generation, "at": datetime.now(UTC).isoformat()}
    ]
    enqueue_document(db, document, "document.reindex")
    db.commit()
    metrics.record_document_reindexed("all", "accepted")

    return JSONResponse(status_code=202, content={"message": "Reindexing started"})


@app.post("/v1/search", response_model=SearchResponse)
def search_documents(
    search_request: SearchRequest,
    db: Session = Depends(get_db),
    authenticated_tenant_id: str = Security(get_tenant_id),
):
    from .search import hybrid_search

    start_time = time.time()

    filters = search_request.filters.model_dump(exclude_none=True) if search_request.filters else {}
    requested_tenant_id = filters.get("tenant_id")
    if requested_tenant_id is not None and requested_tenant_id != authenticated_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    filters["tenant_id"] = authenticated_tenant_id

    # Perform hybrid search
    try:
        results = hybrid_search(
            db=db,
            query_text=search_request.query,
            top_k=search_request.top_k,
            mode=search_request.mode,
            filters=filters,
            rrf_k=search_request.rrf.k if search_request.rrf else 60,
            weights=search_request.rrf.weights.model_dump()
            if search_request.rrf
            else {"lexical": 1.0, "vector": 1.0},
        )
    except Exception:
        metrics.record_search_request(search_request.mode, "error", time.time() - start_time, 0)
        raise

    took_ms = int((time.time() - start_time) * 1000)
    metrics.record_search_request(
        search_request.mode, "success", time.time() - start_time, len(results)
    )

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
                ranks=result.get("ranks", {"lexical": 0, "vector": 0}),
                citation=result.get("citation"),
            )
        )

    return SearchResponse(results=search_results, took_ms=took_ms)


@app.get("/healthz")
async def health_check():
    return {"status": "ok"}


@app.get("/readyz")
async def readiness_check():
    # RabbitMQ outages leave events pending; the API remains available.
    try:
        # Simple database check
        db = database.SessionLocal()
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not ready", "reason": str(e)})
    finally:
        if "db" in locals():
            db.close()


@app.get("/metrics")
async def export_metrics():
    from .metrics import get_metrics_response

    return get_metrics_response()
