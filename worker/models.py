from sqlalchemy import Column, Computed, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PG_UUID
from sqlalchemy.sql import func
from .database import Base
import os
import uuid
from pgvector.sqlalchemy import Vector

class Document(Base):
    __tablename__ = "documents"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    sha256 = Column(String(64), nullable=False)
    storage_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default='queued', index=True)
    error_detail = Column(Text, nullable=True)
    metadata_ = Column('metadata', JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('documents_tenant_status', 'tenant_id', 'status'),
        UniqueConstraint('tenant_id', 'sha256', name='uq_documents_tenant_sha256'),
    )

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey('documents.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    chunk_idx = Column(Integer, nullable=False)
    page = Column(Integer, nullable=True)
    content = Column(Text, nullable=False)
    chunker_version = Column(String, nullable=False)
    embedding_model = Column(String, nullable=True)
    embedding = Column(Vector(int(os.getenv("EMBEDDING_DIMENSION", "384"))), nullable=True)
    tsv = Column(
        TSVECTOR,
        Computed("to_tsvector('portuguese'::regconfig, content)", persisted=True),
        nullable=False,
    )

    __table_args__ = (
        Index('chunks_document_id', 'document_id'),
        Index('chunks_chunk_idx', 'document_id', 'chunk_idx', unique=True),
        Index(
            'chunks_embedding_hnsw',
            'embedding',
            postgresql_using='hnsw',
            postgresql_ops={'embedding': 'vector_cosine_ops'},
            postgresql_with={'m': 16, 'ef_construction': 64},
        ),
        Index('chunks_tsv_gin', 'tsv', postgresql_using='gin'),
    )
