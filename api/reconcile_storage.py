"""Inspect old unreferenced objects; optionally enqueue durable cleanup."""

import argparse
from datetime import UTC, datetime, timedelta

from .config import settings
from .database import SessionLocal
from .minio_client import MinioClient
from .models import Document, OutboxEvent
from .outbox import enqueue_storage_delete


def reconcile(storage, cutoff, limit=100, execute=False, session_factory=SessionLocal):
    candidates = []
    with session_factory() as db:
        for obj in storage.client.list_objects(settings.MINIO_BUCKET, recursive=True):
            if obj.last_modified is None or obj.last_modified >= cutoff:
                continue
            if (
                db.query(Document).filter(Document.storage_key == obj.object_name).first()
                is not None
            ):
                continue
            if (
                db.query(OutboxEvent)
                .filter(
                    OutboxEvent.routing_key == "storage.delete",
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.payload["storage_key"].astext == obj.object_name,
                )
                .first()
                is not None
            ):
                continue
            candidates.append(obj.object_name)
            if execute:
                enqueue_storage_delete(db, obj.object_name)
            if len(candidates) >= limit:
                break
        if execute:
            db.commit()
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-age-hours", type=float, default=24)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.min_age_hours < 1 or args.limit <= 0:
        parser.error("Require minimum age >= 1 hour and positive limit")
    cutoff = datetime.now(UTC) - timedelta(hours=args.min_age_hours)
    for key in reconcile(MinioClient(), cutoff, args.limit, args.execute):
        print(("Cleanup queued: " if args.execute else "Unreferenced: ") + key)


if __name__ == "__main__":
    main()
