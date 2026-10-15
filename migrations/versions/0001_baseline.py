"""Baseline, compatible with pre-Alembic installations."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute("""CREATE TABLE IF NOT EXISTS documents (
        id UUID PRIMARY KEY, tenant_id VARCHAR NOT NULL, filename VARCHAR NOT NULL,
        mime_type VARCHAR NOT NULL, sha256 VARCHAR(64) NOT NULL, storage_key VARCHAR NOT NULL,
        status VARCHAR NOT NULL, error_detail TEXT, metadata JSONB NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT uq_documents_tenant_sha256 UNIQUE (tenant_id, sha256))""")
    op.execute("""CREATE TABLE IF NOT EXISTS chunks (
        id SERIAL PRIMARY KEY, document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_idx INTEGER NOT NULL, page INTEGER, content TEXT NOT NULL,
        chunker_version VARCHAR NOT NULL, embedding_model VARCHAR, embedding vector(384),
        tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('portuguese'::regconfig, content)) STORED NOT NULL)""")
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_documents_tenant_id ON documents (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_documents_status ON documents (status)",
        "CREATE INDEX IF NOT EXISTS documents_tenant_status ON documents (tenant_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_chunks_document_id ON chunks (document_id)",
        "CREATE INDEX IF NOT EXISTS chunks_document_id ON chunks (document_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS chunks_chunk_idx ON chunks (document_id, chunk_idx)",
        "CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv)",
        "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)",
    ):
        op.execute(sql)


def downgrade():
    op.execute("DROP TABLE chunks")
    op.execute("DROP TABLE documents")
