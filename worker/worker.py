import os
import json
import io
import logging
import uuid
import time
from datetime import datetime, timezone
from .extract_process import extract_isolated as extract_document
from .metrics import MESSAGES, STAGE_TIME, CHUNKS, ACTIVE
from shared.telemetry import configure_logging, trace_context, new_trace
from .chunking import chunk_document, CHUNKER_VERSION
from typing import List, Dict, Any, Optional
import pika
from minio import Minio
from minio.error import S3Error
import fitz  # PyMuPDF
import docx
from bs4 import BeautifulSoup
import hashlib
from sentence_transformers import SentenceTransformer
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from pgvector.sqlalchemy import Vector
from .models import Base, Document, Chunk

# Configure logging
configure_logging()
logger = logging.getLogger(__name__)

# Configuration from environment variables
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672")
RETRY_DELAYS_MS = [int(value) for value in os.getenv('MESSAGE_RETRY_DELAYS_MS', '1000,5000,30000').split(',')]
if not RETRY_DELAYS_MS or any(delay <= 0 for delay in RETRY_DELAYS_MS):
    raise ValueError('Retry delays must be positive milliseconds')

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://raguser:ragpass@localhost:5432/ragdb")

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "384"))  # Default for the above model
if EMBEDDING_DIMENSION != 384:
    raise ValueError('The versioned schema requires 384 dimensions; migrate before changing the model')
CHUNK_TARGET_TOKENS = int(os.getenv('CHUNK_TARGET_TOKENS', '96'))
CHUNK_OVERLAP_TOKENS = int(os.getenv('CHUNK_OVERLAP_TOKENS', '12'))
EMBEDDING_BATCH_SIZE = int(os.getenv('EMBEDDING_BATCH_SIZE', '32'))
OCR_ENABLED = os.getenv('OCR_ENABLED', 'false').lower() == 'true'
OCR_LANGUAGES = os.getenv('OCR_LANGUAGES', 'por+eng')
if not 0 <= CHUNK_OVERLAP_TOKENS < CHUNK_TARGET_TOKENS or not 1 <= EMBEDDING_BATCH_SIZE <= 256:
    raise ValueError('Invalid chunk token size, overlap or embedding batch size')

# Initialize MinIO client
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE
)

# Initialize RabbitMQ connection and channel
def get_rabbitmq_connection():
    params = pika.URLParameters(RABBITMQ_URL)
    params.heartbeat = 600
    params.blocked_connection_timeout = 30
    params.socket_timeout = 5
    return pika.BlockingConnection(params)

# Initialize database engine and session
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Initialize embedding model
embedding_model = None

# Ensure the bucket exists
BUCKET_NAME = os.getenv('MINIO_BUCKET', 'documents')

# Text extraction functions
def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from PDF using PyMuPDF."""
    text = ""
    with fitz.open(stream=file_content, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()
    return text

def extract_text_from_docx(file_content: bytes) -> str:
    """Extract text from DOCX using python-docx."""
    doc = docx.Document(io.BytesIO(file_content))
    text = []
    for paragraph in doc.paragraphs:
        text.append(paragraph.text)
    return '\n'.join(text)

def extract_text_from_html(file_content: bytes) -> str:
    """Extract text from HTML using BeautifulSoup."""
    soup = BeautifulSoup(file_content, 'html.parser')
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text()
    # Break into lines and remove leading and trailing space on each
    lines = (line.strip() for line in text.splitlines())
    # Break multi-headlines into a line each
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    # Drop blank lines
    text = '\n'.join(chunk for chunk in chunks if chunk)
    return text

def extract_text(file_content: bytes, mime_type: str) -> str:
    """Dispatch to the appropriate extractor based on mime type."""
    if mime_type == "application/pdf":
        return extract_text_from_pdf(file_content)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_text_from_docx(file_content)
    elif mime_type == "text/html":
        return extract_text_from_html(file_content)
    elif mime_type.startswith("text/"):
        # Assume plain text
        return file_content.decode('utf-8')
    else:
        # For unsupported types, we return an empty string or raise an error.
        # We'll log a warning and return empty string.
        logger.warning(f"Unsupported mime type: {mime_type}")
        return ""

# Chunking function
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Split text into chunks of approximately `chunk_size` characters with `overlap` overlap.
    This is a simple character-based chunker. For a more sophisticated chunker, we might use
    sentence or paragraph boundaries.
    """
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size > 0 and 0 <= overlap < chunk_size")
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
        if end == text_length:
            break
        start += chunk_size - overlap  # Move start forward by (chunk_size - overlap)
    
    return chunks

# Embedding generation
def generate_embedding(text: str) -> List[float]:
    """Generate embedding for a given text using the sentence transformer model."""
    global embedding_model
    if embedding_model is None:
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    embedding = embedding_model.encode(text)
    # Ensure it's a list of floats
    return embedding.tolist()


def get_embedding_model():
    global embedding_model
    if embedding_model is None:
        embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return embedding_model


def generate_embeddings(texts, batch_size=EMBEDDING_BATCH_SIZE):
    if batch_size <= 0:
        raise ValueError('Batch size must be positive')
    model = get_embedding_model()
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        if any(len(model.tokenizer.encode(value, add_special_tokens=True)) > model.max_seq_length for value in batch):
            raise ValueError('Embedding input exceeds model token limit')
        encoded = np.asarray(model.encode(batch, batch_size=batch_size, convert_to_numpy=True,
                                          show_progress_bar=False), dtype=float)
        if encoded.shape != (len(batch), EMBEDDING_DIMENSION) or not np.isfinite(encoded).all():
            raise ValueError('Embedding dimension or values do not match database configuration')
        vectors.extend(encoded.tolist())
    return vectors


def record_transition(document, status):
    document.status = status
    document.processing_history = ((document.processing_history or []) + [
        {'status': status, 'generation': document.generation, 'at': datetime.now(timezone.utc).isoformat()}
    ])[-100:]

# Database operations
def get_document_by_id(db_session, document_id: uuid.UUID) -> Optional[Document]:
    return db_session.query(Document).filter(Document.id == document_id).first()

def update_document_status(db_session, document_id: uuid.UUID, status: str, error_detail: Optional[str] = None):
    document = get_document_by_id(db_session, document_id)
    if document:
        record_transition(document, status)
        document.error_detail = error_detail
        db_session.commit()
    else:
        logger.error(f"Document with id {document_id} not found.")

def save_chunk(db_session, document_id: uuid.UUID, chunk_idx: int, page: Optional[int], content: str,
               chunker_version: str, embedding_model: str, embedding: List[float], **provenance):
    """Save a chunk to the database."""
    # Convert embedding to the format expected by the vector type (list of floats)
    # The Vector type from pgvector.sqlalchemy expects a list of floats.
    chunk = Chunk(
        document_id=document_id,
        chunk_idx=chunk_idx,
        page=page,
        content=content,
        chunker_version=chunker_version,
        embedding_model=embedding_model,
        embedding=embedding, **provenance
    )
    db_session.add(chunk)

# Message processing

def process_document_message(ch, method, properties, body):
    """Serialize document mutations and atomically index one generation."""
    retry_count = 0
    db_session = None
    lock_connection = None
    document_id = None
    document = None
    generation = None
    trace_token = trace_context.set(new_trace())
    processing_started = time.perf_counter()
    try:
        retry_count = int((properties.headers or {}).get("x-retry-count", 0)) if properties else 0
        message = json.loads(body)
        trace_context.set(new_trace(message.get('traceparent')))
        document_id = uuid.UUID(message["document_id"])
        generation = int(message.get("generation", 1))
        lock_connection = engine.connect()
        lock_connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                                {"key": f"document:{document_id}"})
        lock_connection.commit()
        db_session = SessionLocal()
        document = get_document_by_id(db_session, document_id)
        if document is None:
            MESSAGES.labels('missing').inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        if message["tenant_id"] != document.tenant_id:
            raise ValueError("Message tenant does not match document")
        if generation != document.generation or document.indexed_generation >= generation:
            MESSAGES.labels('duplicate').inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        logger.info("Processing document %s generation %s attempt %s", document_id, generation, retry_count + 1)
        update_document_status(db_session, document_id, "extracting")
        response = minio_client.get_object(BUCKET_NAME, document.storage_key)
        try:
            max_bytes = int(os.getenv('MAX_UPLOAD_BYTES', '20971520'))
            file_content = response.read(max_bytes + 1)
            if len(file_content) > max_bytes:
                raise ValueError('Stored document exceeds upload limit')
        finally:
            response.close()
            response.release_conn()
        with STAGE_TIME.labels('extraction').time():
            extracted = extract_document(file_content, document.mime_type, document.filename,
                                     title_override=(document.metadata_ or {}).get('title'),
                                     ocr_enabled=OCR_ENABLED, ocr_languages=OCR_LANGUAGES,
                                     ocr_tessdata=os.getenv('TESSDATA_PREFIX'))
        document.title = extracted.title
        document.extraction_metadata = extracted.metadata
        update_document_status(db_session, document_id, 'chunking')
        model = get_embedding_model()
        with STAGE_TIME.labels('chunking').time():
            chunks = chunk_document(extracted, model.tokenizer, CHUNK_TARGET_TOKENS,
                                    CHUNK_OVERLAP_TOKENS, model.max_seq_length)
        if not chunks:
            raise ValueError('Extracted text contains no indexable tokens')
        update_document_status(db_session, document_id, 'embedding')
        began = time.perf_counter()
        with STAGE_TIME.labels('embedding').time():
            vectors = generate_embeddings([chunk.embedding_text for chunk in chunks])
        document.extraction_metadata = {**document.extraction_metadata, 'embedding': {
            'chunks': len(chunks), 'batch_size': EMBEDDING_BATCH_SIZE,
            'batches': (len(chunks) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE,
            'seconds': time.perf_counter() - began, 'dimension': EMBEDDING_DIMENSION,
            'model_token_limit': model.max_seq_length,
        }}
        db_session.query(Chunk).filter(Chunk.document_id == document_id).delete(synchronize_session=False)
        for idx, (chunk, embedding) in enumerate(zip(chunks, vectors)):
            save_chunk(db_session, document_id, idx, chunk.page, chunk.content,
                       CHUNKER_VERSION, EMBEDDING_MODEL_NAME, embedding,
                       section=chunk.section, section_idx=chunk.section_idx,
                       offset_start=chunk.offset_start, offset_end=chunk.offset_end, token_count=chunk.token_count)
        # Chunks and success marker commit together. A lost ACK only causes a harmless redelivery.
        record_transition(document, 'indexed')
        document.error_detail = None
        document.indexed_generation = generation
        db_session.commit()
        CHUNKS.inc(len(chunks))
        MESSAGES.labels('indexed').inc()
        logger.info('document_indexed document_id=%s generation=%s chunks=%s', document_id, generation, len(chunks))
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        logger.warning("Document %s processing failed (%s)", document_id, type(exc).__name__)
        if db_session is not None:
            try:
                db_session.rollback()
                current = get_document_by_id(db_session, document_id)
                if current is not None and current.generation == generation and current.indexed_generation < generation:
                    record_transition(current, 'failed')
                    current.error_detail = type(exc).__name__ + ": processing failed"
                    db_session.commit()
            except Exception:
                logger.warning("Could not persist failure status for %s", document_id)
        _handle_retry_or_dlq(ch, method, properties, body, retry_count, len(RETRY_DELAYS_MS))
    finally:
        STAGE_TIME.labels('total').observe(time.perf_counter() - processing_started)
        trace_context.reset(trace_token)
        if db_session is not None:
            db_session.close()
        if lock_connection is not None:
            try:
                lock_connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                                        {"key": f"document:{document_id}"})
                lock_connection.commit()
            except Exception:
                # Never return a connection with an unreleased session lock to the pool.
                lock_connection.invalidate()
            finally:
                lock_connection.close()


def _handle_retry_or_dlq(ch, method, properties, body, retry_count: int, max_retries: int):
    """Handle retry logic or send to DLQ."""
    retry_count = max(0, retry_count)
    MESSAGES.labels('retry' if retry_count < max_retries else 'dlq').inc()
    if retry_count < max_retries:
        # Requeue with incremented retry count
        new_headers = dict(properties.headers or {}) if properties else {}
        new_headers["x-retry-count"] = retry_count + 1

        queue_names = {
            "document.upload": "document.upload.queue",
            "document.reindex": "document.reindex.queue",
            "document.upload.queue": "document.upload.queue",
            "document.reindex.queue": "document.reindex.queue",
        }
        queue_name = queue_names.get(method.routing_key)
        if queue_name is None:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        
        try:
            ch.basic_publish(
                exchange="", routing_key=f'{queue_name.removesuffix(".queue")}.retry.{retry_count + 1}',
                body=body, mandatory=True,
                properties=pika.BasicProperties(
                    delivery_mode=2, headers=new_headers,
                    message_id=properties.message_id if properties else None,
                    content_type='application/json',
                ),
            )
        except Exception:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            raise
        logger.info(f"Requeued message for retry {retry_count + 1}/{max_retries}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
    else:
        # Max retries exceeded - reject and let RabbitMQ route to DLQ
        logger.error(f"Max retries ({max_retries}) exceeded, sending to DLQ")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

def consume_once():
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
    connection = get_rabbitmq_connection()
    try:
        channel = connection.channel()
        channel.basic_qos(prefetch_count=1)
        channel.confirm_delivery()
        channel.exchange_declare(exchange="document.dlx", exchange_type="direct", durable=True)
        for routing in ('document.upload', 'document.reindex'):
            channel.exchange_declare(exchange=routing, exchange_type='direct', durable=True)
            channel.queue_declare(queue=f'{routing}.dlq', durable=True)
            channel.queue_bind(queue=f'{routing}.dlq', exchange='document.dlx', routing_key=f'{routing}.dlq')
            channel.queue_declare(
                queue=f'{routing}.queue', durable=True,
                arguments={'x-dead-letter-exchange': 'document.dlx',
                           'x-dead-letter-routing-key': f'{routing}.dlq', 'x-message-ttl': 86400000},
            )
            channel.queue_bind(queue=f'{routing}.queue', exchange=routing, routing_key=routing)
            for attempt, delay in enumerate(RETRY_DELAYS_MS, 1):
                channel.queue_declare(
                    queue=f'{routing}.retry.{attempt}', durable=True,
                    arguments={'x-message-ttl': delay, 'x-dead-letter-exchange': routing,
                               'x-dead-letter-routing-key': routing},
                )
            channel.basic_consume(queue=f'{routing}.queue', on_message_callback=process_document_message)
        logger.info('Waiting for document messages')
        ACTIVE.set(1)
        channel.start_consuming()
    finally:
        ACTIVE.set(0)
        if connection.is_open:
            connection.close()


def main():
    from prometheus_client import start_http_server
    from .metrics import REGISTRY
    start_http_server(int(os.getenv('WORKER_METRICS_PORT', '9101')), registry=REGISTRY)
    delay = 1
    while True:
        try:
            consume_once()
            delay = 1
        except KeyboardInterrupt:
            return
        except Exception as exc:
            logger.warning('Worker reconnecting in %ss (%s)', delay, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 30)

if __name__ == "__main__":
    main()
