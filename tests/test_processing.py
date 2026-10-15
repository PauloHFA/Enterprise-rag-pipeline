"""Consumer contract: generation checks, atomic publication boundary and lock release."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from worker import worker
from worker.chunking import StructuredChunk
from worker.extraction import ExtractedDocument, TextSection
from worker.models import Document


@pytest.fixture
def processing(monkeypatch):
    document = Document(
        id=uuid.uuid4(),
        tenant_id="acme",
        filename="policy.txt",
        mime_type="text/plain",
        storage_key="acme/policy",
        generation=2,
        indexed_generation=1,
        processing_history=[],
        extraction_metadata={},
        metadata_={},
    )
    db, connection, channel = MagicMock(), MagicMock(), MagicMock()
    db.query.return_value.filter.return_value.first.return_value = document
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker.engine, "connect", lambda: connection)
    storage = MagicMock()
    storage.get_object.return_value.read.return_value = b"Pagamento em trinta dias."
    monkeypatch.setattr(worker, "minio_client", storage)
    monkeypatch.setattr(
        worker,
        "extract_document",
        lambda *a, **kw: ExtractedDocument("Policy", [TextSection("Pagamento.")], {}),
    )
    monkeypatch.setattr(
        worker,
        "get_embedding_model",
        lambda: SimpleNamespace(tokenizer=object(), max_seq_length=128),
    )
    chunk = StructuredChunk("Pagamento.", None, None, 0, 0, 10, 3, "Policy Pagamento.")
    monkeypatch.setattr(worker, "chunk_document", lambda *a: [chunk])
    monkeypatch.setattr(worker, "generate_embeddings", lambda texts: [[0.1] * 384 for _ in texts])
    message = {"document_id": str(document.id), "tenant_id": "acme", "generation": 2}
    method = SimpleNamespace(delivery_tag=1, routing_key="document.upload")
    properties = SimpleNamespace(headers={}, message_id="event-1")
    return SimpleNamespace(
        document=document,
        db=db,
        connection=connection,
        channel=channel,
        storage=storage,
        message=message,
        method=method,
        properties=properties,
    )


def consume(state):
    worker.process_document_message(
        state.channel, state.method, state.properties, json.dumps(state.message).encode()
    )


def test_success_commits_chunks_and_generation_before_ack(processing):
    state = processing

    def ack(**kwargs):
        assert state.document.indexed_generation == 2
        assert state.document.status == "indexed"
        assert state.db.commit.call_count == 4

    state.channel.basic_ack.side_effect = ack
    consume(state)
    assert [item["status"] for item in state.document.processing_history] == [
        "extracting",
        "chunking",
        "embedding",
        "indexed",
    ]
    saved = state.db.add.call_args.args[0]
    assert saved.document_id == state.document.id and len(saved.embedding) == 384
    assert saved.content == "Pagamento." and saved.offset_start == 0
    state.storage.get_object.return_value.close.assert_called_once()
    state.storage.get_object.return_value.release_conn.assert_called_once()
    state.connection.close.assert_called_once()
    state.db.close.assert_called_once()


@pytest.mark.parametrize("case", ["missing", "stale", "already_indexed"])
def test_obsolete_deliveries_ack_without_reading_storage(processing, case):
    state = processing
    if case == "missing":
        state.db.query.return_value.filter.return_value.first.return_value = None
    elif case == "stale":
        state.message["generation"] = 1
    else:
        state.document.indexed_generation = 2
    consume(state)
    state.channel.basic_ack.assert_called_once()
    state.storage.get_object.assert_not_called()
    state.db.add.assert_not_called()
    state.connection.close.assert_called_once()


@pytest.mark.parametrize("case", ["tenant", "storage", "empty", "oversized", "embedding"])
def test_failures_persist_failure_and_retry_before_ack(processing, monkeypatch, case):
    state = processing
    if case == "tenant":
        state.message["tenant_id"] = "foreign"
    elif case == "storage":
        state.storage.get_object.side_effect = ConnectionError("temporary")
    elif case == "empty":
        monkeypatch.setattr(worker, "chunk_document", lambda *args: [])
    elif case == "oversized":
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "1")
    else:
        monkeypatch.setattr(
            worker, "generate_embeddings", MagicMock(side_effect=ValueError("dimension"))
        )
    consume(state)
    assert state.document.status == "failed"
    assert state.document.indexed_generation == 1
    state.channel.basic_publish.assert_called_once()
    assert state.channel.basic_publish.call_args.kwargs["routing_key"] == "document.upload.retry.1"
    state.channel.basic_ack.assert_called_once()
    state.db.rollback.assert_called_once()


def test_final_commit_failure_never_acknowledges_index_success(processing):
    state = processing
    state.db.commit.side_effect = [None, None, None, ConnectionError("commit"), None]

    # A real rollback reloads old durable state; emulate that reload rather than
    # retaining the ORM object's uncommitted indexed_generation.
    def rollback():
        state.document.indexed_generation = 1

    state.db.rollback.side_effect = rollback
    consume(state)
    assert state.document.status == "failed"
    state.channel.basic_publish.assert_called_once()
    state.channel.basic_ack.assert_called_once()


def test_lock_release_failure_invalidates_connection(processing):
    state = processing
    state.connection.execute.side_effect = [None, ConnectionError("unlock")]
    consume(state)
    state.connection.invalidate.assert_called_once()
    state.connection.close.assert_called_once()


def test_invalid_json_retries_without_database_access(processing):
    state = processing
    worker.process_document_message(state.channel, state.method, state.properties, b"not-json")
    state.connection.execute.assert_not_called()
    state.channel.basic_publish.assert_called_once()


def test_failure_status_cannot_be_persisted_still_retries(processing):
    state = processing
    state.storage.get_object.side_effect = ConnectionError()
    state.db.rollback.side_effect = ConnectionError()
    consume(state)
    state.channel.basic_publish.assert_called_once()


def test_unknown_retry_destination_is_rejected(processing):
    state = processing
    state.method.routing_key = "unknown"
    worker._handle_retry_or_dlq(state.channel, state.method, state.properties, b"{}", 0, 3)
    state.channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
