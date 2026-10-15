"""Complete PostgreSQL + object snapshot. Requires a quiesced source/empty target."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .config import settings
from .minio_client import MinioClient
from .models import Document, OutboxEvent
from .outbox import enqueue_document


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def pg_environment():
    url = make_url(settings.DATABASE_URL)
    return {
        **os.environ,
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGDATABASE": url.database,
        "PGUSER": url.username,
        "PGPASSWORD": url.password or "",
    }


def database_counts(engine):
    with engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
            for name in ("documents", "chunks", "outbox_events")
        }


def backup(target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    storage = MinioClient().client
    engine = create_engine(settings.DATABASE_URL)
    # No manifest is produced unless every required component succeeds.
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(target / "postgres.dump"),
        ],
        env=pg_environment(),
        check=True,
    )
    objects = []
    (target / "objects").mkdir()
    for index, item in enumerate(storage.list_objects(settings.MINIO_BUCKET, recursive=True)):
        relative = f"objects/{index:08d}"
        path = target / relative
        stat = storage.stat_object(settings.MINIO_BUCKET, item.object_name)
        storage.fget_object(settings.MINIO_BUCKET, item.object_name, str(path))
        if path.stat().st_size != stat.size:
            raise ValueError("Incomplete object download")
        objects.append(
            {
                "key": item.object_name,
                "path": relative,
                "size": stat.size,
                "content_type": stat.content_type,
                "sha256": digest(path),
            }
        )
    counts = database_counts(engine)
    with engine.connect() as connection:
        keys = set(connection.execute(text("SELECT storage_key FROM documents")).scalars())
    if not keys.issubset({item["key"] for item in objects}):
        raise ValueError("A referenced object is missing from storage")
    manifest = {
        "version": 1,
        "database_sha256": digest(target / "postgres.dump"),
        "counts": counts,
        "objects": objects,
        "quiesced": True,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def validate_snapshot(target):
    target = Path(target).resolve()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest["version"] != 1 or digest(target / "postgres.dump") != manifest["database_sha256"]:
        raise ValueError("Invalid database snapshot")
    for item in manifest["objects"]:
        path = (target / item["path"]).resolve()
        if (
            not path.is_relative_to(target / "objects")
            or path.stat().st_size != item["size"]
            or digest(path) != item["sha256"]
        ):
            raise ValueError("Invalid object snapshot")
    return manifest


def restore(target):
    target = Path(target)
    manifest = validate_snapshot(target)
    storage = MinioClient().client
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as connection:
        if connection.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        ).scalar_one():
            raise ValueError("Restore requires an empty database")
    if storage.bucket_exists(settings.MINIO_BUCKET) and next(
        iter(storage.list_objects(settings.MINIO_BUCKET, recursive=True)), None
    ):
        raise ValueError("Restore requires an empty bucket")
    subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            pg_environment()["PGDATABASE"],
            str(target / "postgres.dump"),
        ],
        env=pg_environment(),
        check=True,
    )
    if not storage.bucket_exists(settings.MINIO_BUCKET):
        storage.make_bucket(settings.MINIO_BUCKET)
    for item in manifest["objects"]:
        storage.fput_object(
            settings.MINIO_BUCKET,
            item["key"],
            str(target / item["path"]),
            content_type=item["content_type"] or "application/octet-stream",
        )
        response = storage.get_object(settings.MINIO_BUCKET, item["key"])
        try:
            checksum = hashlib.sha256()
            for block in response.stream(1024 * 1024):
                checksum.update(block)
            if checksum.hexdigest() != item["sha256"]:
                raise ValueError("Restored object checksum mismatch")
        finally:
            response.close()
            response.release_conn()
    if database_counts(engine) != manifest["counts"]:
        raise ValueError("Restored database counts mismatch")
    # Broker queues are not part of the snapshot. Replay the current unfinished
    # generation, including retries that existed only in RabbitMQ at backup time.
    with Session(engine) as db:
        for document in db.query(Document).filter(
            Document.indexed_generation < Document.generation
        ):
            event = (
                db.query(OutboxEvent)
                .filter(
                    OutboxEvent.document_id == document.id,
                    OutboxEvent.routing_key.in_(["document.upload", "document.reindex"]),
                    OutboxEvent.payload["generation"].as_integer() == document.generation,
                )
                .order_by(OutboxEvent.created_at.desc())
                .first()
            )
            if event is None:
                enqueue_document(db, document, "document.reindex")
            else:
                event.published_at = None
                event.next_attempt_at = datetime.now(UTC)
                event.attempts, event.last_error = 0, None
        db.commit()
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("directory")
    parser.add_argument("--quiesced", action="store_true", help="Source writers have been stopped")
    parser.add_argument("--isolated", action="store_true", help="Target is isolated and empty")
    args = parser.parse_args()
    if args.action == "backup" and not args.quiesced:
        parser.error("Stop API, worker and outbox writers, then pass --quiesced")
    if args.action == "restore" and not args.isolated:
        parser.error("Use an isolated empty target and pass --isolated")
    result = {"backup": backup, "restore": restore, "verify": validate_snapshot}[args.action](
        args.directory
    )
    print(
        json.dumps(
            {"result": "verified", "counts": result["counts"], "objects": len(result["objects"])}
        )
    )


if __name__ == "__main__":
    main()
