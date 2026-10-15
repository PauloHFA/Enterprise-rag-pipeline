"""Generation, outbox and provenance (stages 2/3)."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    for definition in (
        "generation INTEGER NOT NULL DEFAULT 1",
        "indexed_generation INTEGER NOT NULL DEFAULT 0",
        "title TEXT",
        "extraction_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
        "processing_history JSONB NOT NULL DEFAULT '[]'::jsonb",
    ):
        op.execute(f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {definition}")
    # Both TEXT (additive upgrades) and VARCHAR (create_all) existed in stage 3.
    op.execute("ALTER TABLE documents ALTER COLUMN title TYPE TEXT")
    for definition in (
        "section TEXT",
        "section_idx INTEGER",
        "offset_start INTEGER",
        "offset_end INTEGER",
        "token_count INTEGER",
    ):
        op.execute(f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS {definition}")
    op.execute(
        "UPDATE documents SET indexed_generation=generation WHERE status='completed' AND indexed_generation=0"
    )
    op.execute("UPDATE documents SET status='indexed' WHERE status='completed'")
    op.execute("""CREATE TABLE IF NOT EXISTS outbox_events (
        id UUID PRIMARY KEY, document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
        routing_key VARCHAR NOT NULL, payload JSONB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        published_at TIMESTAMPTZ, last_error TEXT)""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_pending ON outbox_events (next_attempt_at) WHERE published_at IS NULL"
    )


def downgrade():
    op.execute("DROP TABLE outbox_events")
    op.execute("UPDATE documents SET status='completed' WHERE status='indexed'")
    for column in ("section", "section_idx", "offset_start", "offset_end", "token_count"):
        op.drop_column("chunks", column)
    for column in (
        "generation",
        "indexed_generation",
        "title",
        "extraction_metadata",
        "processing_history",
    ):
        op.drop_column("documents", column)
