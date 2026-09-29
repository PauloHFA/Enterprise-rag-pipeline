import os
import json
import logging
import uuid
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
from models import Base, Document, Chunk  # We'll need to adjust the import based on our structure

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://raguser:ragpass@localhost:5432/ragdb")

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "384"))  # Default for the above model

# Initialize MinIO client
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE
)

# Initialize RabbitMQ connection and channel
def get_rabbitmq_connection():
    return pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))

# Initialize database engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Initialize embedding model
embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)

# Ensure the bucket exists
BUCKET_NAME = "documents"
if not minio_client.bucket_exists(BUCKET_NAME):
    minio_client.make_bucket(BUCKET_NAME)
    logger.info(f"Created bucket {BUCKET_NAME}")

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
        start += chunk_size - overlap  # Move start forward by (chunk_size - overlap)
    
    return chunks

# Embedding generation
def generate_embedding(text: str) -> List[float]:
    """Generate embedding for a given text using the sentence transformer model."""
    embedding = embedding_model.encode(text)
    # Ensure it's a list of floats
    return embedding.tolist()

# Database operations
def get_document_by_id(db_session, document_id: uuid.UUID) -> Optional[Document]:
    return db_session.query(Document).filter(Document.id == document_id).first()

def update_document_status(db_session, document_id: uuid.UUID, status: str, error_detail: Optional[str] = None):
    document = get_document_by_id(db_session, document_id)
    if document:
        document.status = status
        document.error_detail = error_detail
        db_session.commit()
    else:
        logger.error(f"Document with id {document_id} not found.")

def save_chunk(db_session, document_id: uuid.UUID, chunk_idx: int, page: Optional[int], content: str,
               chunker_version: str, embedding_model: str, embedding: List[float]):
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
        embedding=embedding  # This will be stored as a vector
    )
    db_session.add(chunk)
    db_session.commit()

# Message processing
def process_document_message(ch, method, properties, body):
    """Callback function to process a message from RabbitMQ."""
    try:
        message = json.loads(body)
        logger.info(f"Received message: {message}")
        
        document_id = uuid.UUID(message["document_id"])
        tenant_id = message["tenant_id"]
        storage_key = message["storage_key"]
        filename = message["filename"]
        mime_type = message["mime_type"]
        metadata = message.get("metadata", {})
        
        # Update status to processing
        db_session = SessionLocal()
        update_document_status(db_session, document_id, "processing")
        
        # Download file from MinIO
        try:
            response = minio_client.get_object(BUCKET_NAME, storage_key)
            file_content = response.read()
            response.close()
            response.release_conn()
        except S3Error as e:
            logger.error(f"Error downloading file from MinIO: {e}")
            update_document_status(db_session, document_id, "failed", str(e))
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        
        # Extract text
        text = extract_text(file_content, mime_type)
        if not text:
            logger.warning(f"No text extracted from document {document_id}")
            update_document_status(db_session, document_id, "failed", "No text extracted")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        
        # Chunk text
        chunks = chunk_text(text)
        logger.info(f"Document {document_id} split into {len(chunks)} chunks")
        
        # Process each chunk
        for idx, chunk_text in enumerate(chunks):
            # Generate embedding
            embedding = generate_embedding(chunk_text)
            
            # Save chunk to database
            # Note: We are not storing page numbers in this simple chunker.
            # We could enhance the chunker to track page numbers if needed.
            save_chunk(
                db_session=db_session,
                document_id=document_id,
                chunk_idx=idx,
                page=None,  # We don't have page information in this simple chunker
                content=chunk_text,
                chunker_version="simple-character-chunker-v1",
                embedding_model=EMBEDDING_MODEL_NAME,
                embedding=embedding
            )
        
        # Update document status to completed
        update_document_status(db_session, document_id, "completed")
        logger.info(f"Document {document_id} processed successfully")
        
        # Acknowledge the message
        ch.basic_ack(delivery_tag=method.delivery_tag)
        
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        # Update document status to failed if we have a document_id
        if 'document_id' in locals():
            db_session = SessionLocal()
            update_document_status(db_session, document_id, "failed", str(e))
        # We still acknowledge the message to avoid requeueing the same problematic message
        # In a production system, we might want to reject and requeue or send to a DLQ.
        ch.basic_ack(delivery_tag=method.delivery_tag)

def main():
    """Main function to start the worker."""
    logger.info("Starting Enterprise RAG Worker")
    
    # Connect to RabbitMQ
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare the queues (they should already be declared by the API, but we do it here for safety)
    channel.queue_declare(queue="document.upload.queue", durable=True)
    channel.queue_declare(queue="document.reindex.queue", durable=True)
    
    # Set up consumer for both queues
    channel.basic_consume(queue="document.upload.queue", on_message_callback=process_document_message)
    channel.basic_consume(queue="document.reindex.queue", on_message_callback=process_document_message)
    
    logger.info("Waiting for messages. To exit press CTRL+C")
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Interrupted")
        channel.stop_consuming()
        connection.close()

if __name__ == "__main__":
    main()