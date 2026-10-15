"""Recoverable at-least-once publisher. Run separately: python -m api.outbox."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from shared.telemetry import configure_logging, new_trace, trace_context

from .config import settings
from .database import SessionLocal
from .metrics import OUTBOX_OLDEST, OUTBOX_PENDING, record_queue_publish
from .minio_client import MinioClient
from .models import OutboxEvent
from .rabbitmq_client import RabbitMQClient

logger = logging.getLogger(__name__)


def enqueue_document(db, document, routing_key):
    event_id = uuid.uuid4()
    event = OutboxEvent(
        id=event_id,
        document_id=document.id,
        routing_key=routing_key,
        payload={
            "event_id": str(event_id),
            "document_id": str(document.id),
            "tenant_id": document.tenant_id,
            "generation": document.generation,
            "storage_key": document.storage_key,
            "filename": document.filename,
            "mime_type": document.mime_type,
            "metadata": document.metadata_,
            "traceparent": trace_context.get(),
        },
    )
    db.add(event)
    return event


def enqueue_storage_delete(db, storage_key):
    event = OutboxEvent(routing_key="storage.delete", payload={"storage_key": storage_key})
    db.add(event)
    return event


async def publish_pending(broker, storage, session_factory=SessionLocal, batch_size=None):
    """Lock each event until confirm + commit; competing publishers skip locked rows."""
    processed = 0
    for _ in range(batch_size or settings.OUTBOX_BATCH_SIZE):
        with session_factory() as db:
            event = (
                db.query(OutboxEvent)
                .filter(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(OutboxEvent.next_attempt_at, OutboxEvent.created_at, OutboxEvent.id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if event is None:
                break
            trace_token = trace_context.set(new_trace(event.payload.get("traceparent")))
            try:
                try:
                    if event.routing_key == "storage.delete":
                        await asyncio.to_thread(
                            storage.delete_file, settings.MINIO_BUCKET, event.payload["storage_key"]
                        )
                    else:
                        await asyncio.wait_for(
                            broker.publish_message(event.routing_key, event.payload), timeout=15
                        )
                except Exception as exc:
                    event.attempts += 1
                    delay = min(2 ** min(event.attempts, 16), settings.OUTBOX_MAX_BACKOFF_SECONDS)
                    event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
                    # Do not persist credentials that may occur in connection exception text.
                    event.last_error = type(exc).__name__
                    logger.warning(
                        "Outbox event %s attempt %s failed (%s)",
                        event.id,
                        event.attempts,
                        event.last_error,
                    )
                else:
                    event.attempts += 1
                    event.published_at = datetime.now(UTC)
                    event.last_error = None
                    record_queue_publish(event.routing_key)
                # If commit fails after publish, delivery may repeat. Consumers must be idempotent.
                db.commit()
                processed += 1
            finally:
                trace_context.reset(trace_token)
    return processed


async def run():
    from prometheus_client import start_http_server

    start_http_server(9102)
    broker, storage = RabbitMQClient(), MinioClient()
    try:
        while True:
            try:
                await publish_pending(broker, storage)
                with SessionLocal() as db:
                    count, age = db.execute(
                        text("""SELECT count(*), COALESCE(EXTRACT(EPOCH FROM now()-min(created_at)),0)
                                                   FROM outbox_events WHERE published_at IS NULL""")
                    ).one()
                    OUTBOX_PENDING.set(count)
                    OUTBOX_OLDEST.set(age)
            except Exception as exc:
                logger.warning("Outbox loop will retry (%s)", type(exc).__name__)
            await asyncio.sleep(settings.OUTBOX_POLL_SECONDS)
    finally:
        await broker.close()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run())
