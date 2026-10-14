"""Additive upgrade for existing development databases; Alembic follows in stage 4."""
from sqlalchemy import text

from . import database, models


def initialize_schema():
    with database.engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
        # Serializes startup from multiple API replicas.
        connection.execute(text("SELECT pg_advisory_xact_lock(71402002)"))
        models.Base.metadata.create_all(bind=connection)
        connection.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1"))
        connection.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS indexed_generation INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text("UPDATE documents SET indexed_generation = generation WHERE status = 'completed' AND indexed_generation = 0"))
