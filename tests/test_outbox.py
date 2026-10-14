import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from api.models import OutboxEvent
from api.outbox import publish_pending


def session_for(event):
    db = MagicMock()
    db.__enter__.return_value = db
    query = db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value
    query.first.side_effect = [event, None]
    return db, lambda: db


def test_broker_failure_leaves_event_pending_and_schedules_retry():
    event = OutboxEvent(id=uuid.uuid4(), routing_key='document.upload', payload={}, attempts=0)
    db, factory = session_for(event)
    broker = MagicMock(publish_message=AsyncMock(side_effect=ConnectionError('do not store secrets')))
    asyncio.run(publish_pending(broker, MagicMock(), factory))
    assert event.published_at is None
    assert event.attempts == 1
    assert event.last_error == 'ConnectionError'
    assert event.next_attempt_at > datetime.now(timezone.utc)
    db.commit.assert_called_once()


def test_confirmed_delivery_marks_event_published():
    event = OutboxEvent(id=uuid.uuid4(), routing_key='document.upload', payload={'event_id': 'e'}, attempts=1)
    db, factory = session_for(event)
    broker = MagicMock(publish_message=AsyncMock())
    asyncio.run(publish_pending(broker, MagicMock(), factory))
    assert event.published_at is not None
    assert event.attempts == 2
    broker.publish_message.assert_awaited_once_with('document.upload', event.payload)


def test_storage_cleanup_does_not_need_broker():
    event = OutboxEvent(id=uuid.uuid4(), routing_key='storage.delete', payload={'storage_key': 'old.txt'}, attempts=0)
    db, factory = session_for(event)
    broker = MagicMock(publish_message=AsyncMock())
    storage = MagicMock()
    asyncio.run(publish_pending(broker, storage, factory))
    assert event.published_at is not None
    storage.delete_file.assert_called_once_with('documents', 'old.txt')
    broker.publish_message.assert_not_awaited()


def test_commit_failure_is_propagated_for_transaction_rollback():
    event = OutboxEvent(id=uuid.uuid4(), routing_key='document.upload', payload={}, attempts=0)
    db, factory = session_for(event)
    db.commit.side_effect = SQLAlchemyError('commit failed')
    with pytest.raises(SQLAlchemyError):
        asyncio.run(publish_pending(MagicMock(publish_message=AsyncMock()), MagicMock(), factory))
    db.__exit__.assert_called_once()


def test_reconcile_preserves_referenced_and_recent_objects():
    from types import SimpleNamespace
    from datetime import timedelta
    from api.reconcile_storage import reconcile
    now = datetime.now(timezone.utc)
    storage = MagicMock()
    storage.client.list_objects.return_value = [
        SimpleNamespace(object_name='recent', last_modified=now),
        SimpleNamespace(object_name='referenced', last_modified=now - timedelta(days=2)),
        SimpleNamespace(object_name='orphan', last_modified=now - timedelta(days=2)),
    ]
    db = MagicMock()
    db.__enter__.return_value = db
    db.query.return_value.filter.return_value.first.side_effect = [object(), None, None]
    assert reconcile(storage, now - timedelta(days=1), execute=True, session_factory=lambda: db) == ['orphan']
    assert db.add.call_args.args[0].payload == {'storage_key': 'orphan'}
    db.commit.assert_called_once()
