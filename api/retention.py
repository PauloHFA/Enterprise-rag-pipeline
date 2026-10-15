"""Explicit scheduled retention; disabled for documents when RETENTION_DAYS=0."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from .config import settings
from .database import SessionLocal
from .models import Document, OutboxEvent
from .outbox import enqueue_storage_delete


def apply_retention(db, now=None):
    now = now or datetime.now(UTC)
    removed = 0
    if settings.RETENTION_DAYS:
        cutoff = now - timedelta(days=settings.RETENTION_DAYS)
        # Bounded batches and the same lock ordering as DELETE/worker.
        candidates = (
            db.query(Document.id)
            .filter(Document.created_at < cutoff)
            .order_by(Document.id)
            .limit(100)
            .all()
        )
        for (document_id,) in candidates:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"document:{document_id}"},
            )
            document = (
                db.query(Document)
                .filter(Document.id == document_id, Document.created_at < cutoff)
                .first()
            )
            if document:
                enqueue_storage_delete(db, document.storage_key)
                db.delete(document)
                removed += 1
            db.commit()
    db.query(OutboxEvent).filter(
        OutboxEvent.published_at < now - timedelta(days=settings.OUTBOX_RETENTION_DAYS)
    ).delete(synchronize_session=False)
    db.commit()
    return removed


if __name__ == "__main__":
    with SessionLocal() as db:
        print(f"Documents expired: {apply_retention(db)}")
