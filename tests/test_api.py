import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import main
from api.config import settings
from api.minio_client import MinioClient
from api.security import get_tenant_id


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "test-key")
    monkeypatch.setattr(settings, "TENANT_API_KEYS", '{"acme":"acme-key"}')
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    def refresh(doc):
        doc.id = doc.id or uuid.uuid4()
        doc.created_at = datetime.now(UTC)
        doc.updated_at = doc.created_at

    db.refresh.side_effect = refresh
    main.app.dependency_overrides[main.get_db] = lambda: db
    storage = MagicMock()
    broker = MagicMock()
    broker.publish_message = AsyncMock()
    monkeypatch.setattr(main, "minio_client", storage)
    monkeypatch.setattr(main, "rabbitmq_client", broker)
    yield db, storage, broker
    main.app.dependency_overrides.clear()


@pytest.fixture
def client(services):
    return TestClient(main.app)


def test_health_and_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "enterprise_rag_search_latency_seconds" in response.text


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "invalid"}])
def test_upload_requires_authentication(client, headers):
    assert (
        client.post(
            "/v1/documents", files={"file": ("test.txt", b"text")}, headers=headers
        ).status_code
        == 401
    )


def test_upload_response_storage_and_event(client, services):
    db, storage, broker = services
    response = client.post(
        "/v1/documents",
        files={"file": ("test.txt", b"conteudo", "text/plain")},
        data={"metadata": '{"title":"Teste"}'},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 202, response.text
    result = response.json()
    uuid.UUID(result["id"])
    assert result["metadata"] == {"title": "Teste"}
    assert result["status"] == "queued"
    assert result["updated_at"] is not None
    assert storage.upload_file.call_args.kwargs["data"] == b"conteudo"
    broker.publish_message.assert_not_awaited()
    from api.models import OutboxEvent

    events = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], OutboxEvent)
    ]
    assert len(events) == 1
    assert events[0].payload["document_id"] == result["id"]
    assert events[0].payload["generation"] == 1
    db.commit.assert_called_once()


@pytest.mark.parametrize("metadata", ["{", "[]", "null", "12"])
def test_invalid_metadata_does_not_write(client, services, metadata):
    response = client.post(
        "/v1/documents",
        files={"file": ("test.txt", b"text")},
        data={"metadata": metadata},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 400
    services[1].upload_file.assert_not_called()


def test_tenant_isolation(client, services):
    response = client.post(
        "/v1/documents",
        files={"file": ("test.txt", b"text")},
        data={"tenant_id": "other"},
        headers={"X-API-Key": "acme-key"},
    )
    assert response.status_code == 403
    response = client.post(
        "/v1/search",
        json={"query": "test", "filters": {"tenant_id": "other"}},
        headers={"X-API-Key": "acme-key"},
    )
    assert response.status_code == 403
    assert asyncio.run(get_tenant_id("acme-key")) == "acme"


def test_unconfigured_key_rejected(services, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_tenant_id("unknown"))
    assert exc.value.status_code == 401


def test_document_not_found(client):
    doc_id = str(uuid.uuid4())
    for method, suffix in [("get", ""), ("delete", ""), ("post", "/reindex")]:
        response = getattr(client, method)(
            f"/v1/documents/{doc_id}{suffix}", headers={"X-API-Key": "test-key"}
        )
        assert response.status_code == 404


def test_search_contract_and_authenticated_filter(client, services, monkeypatch):
    from api import search

    doc_id = uuid.uuid4()
    execute = MagicMock(
        return_value=[
            {
                "id": 1,
                "document_id": doc_id,
                "score": 0.1,
                "snippet": "Contrato",
                "ranks": {"lexical": 1, "vector": 1},
            }
        ]
    )
    monkeypatch.setattr(search, "hybrid_search", execute)
    response = client.post(
        "/v1/search", json={"query": "contrato"}, headers={"X-API-Key": "acme-key"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["chunk_id"] == 1
    assert execute.call_args.kwargs["filters"] == {"tenant_id": "acme"}


def test_minio_upload_uses_stream():
    storage = MinioClient()
    storage.client = MagicMock()
    storage.upload_file("documents", "test.txt", b"content", 7, "text/plain")
    assert storage.client.put_object.call_args.kwargs["data"].read() == b"content"


def test_legacy_document_nullable_timestamp():
    from api.schemas import DocumentResponse

    doc = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="default",
        filename="old.txt",
        mime_type="text/plain",
        sha256="a" * 64,
        storage_key="old.txt",
        status="queued",
        error_detail=None,
        metadata_={},
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    assert DocumentResponse.model_validate(doc).updated_at is None


def test_failed_upload_commit_compensates_storage(client, services):
    from sqlalchemy.exc import SQLAlchemyError

    db, storage, broker = services
    db.commit.side_effect = SQLAlchemyError("failed transaction")
    response = client.post(
        "/v1/documents",
        files={"file": ("failed.txt", b"content", "text/plain")},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 503
    db.rollback.assert_called_once()
    storage.delete_file.assert_called_once()
    broker.publish_message.assert_not_awaited()


def test_reindex_version_and_event_commit_together(client, services):
    from api.models import Document, OutboxEvent

    db, storage, broker = services
    document = Document(
        id=uuid.uuid4(),
        generation=1,
        indexed_generation=1,
        tenant_id="default",
        status="completed",
        storage_key="original.txt",
        filename="original.txt",
        mime_type="text/plain",
        metadata_={},
    )
    db.query.return_value.filter.return_value.first.return_value = document
    response = client.post(
        f"/v1/documents/{document.id}/reindex", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 202
    assert document.generation == 2
    event = db.add.call_args.args[0]
    assert isinstance(event, OutboxEvent)
    assert event.payload["generation"] == 2
    assert event.routing_key == "document.reindex"
    db.commit.assert_called_once()
    broker.publish_message.assert_not_awaited()


def test_delete_schedules_cleanup_without_storage_dependency(client, services):
    from api.models import Document

    db, storage, broker = services
    document = Document(id=uuid.uuid4(), tenant_id="default", storage_key="old.txt")
    db.query.return_value.filter.return_value.first.return_value = document
    response = client.delete(f"/v1/documents/{document.id}", headers={"X-API-Key": "test-key"})
    assert response.status_code == 204
    event = db.add.call_args.args[0]
    assert event.routing_key == "storage.delete"
    assert event.document_id is None
    assert event.payload == {"storage_key": "old.txt"}
    storage.delete_file.assert_not_called()
    db.delete.assert_called_once_with(document)
    db.commit.assert_called_once()


def test_ambiguous_commit_never_deletes_confirmed_original(client, services):
    from sqlalchemy.exc import SQLAlchemyError

    from api.models import Document

    db, storage, broker = services

    def interrupted_commit():
        document = next(
            call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], Document)
        )
        document.created_at = datetime.now(UTC)
        document.updated_at = document.created_at
        db.query.return_value.filter.return_value.first.return_value = document
        raise SQLAlchemyError("commit response lost")

    db.commit.side_effect = interrupted_commit
    response = client.post(
        "/v1/documents",
        files={"file": ("confirmed.txt", b"content", "text/plain")},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 202, response.text
    storage.delete_file.assert_not_called()
    broker.publish_message.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {"query": " "},
        {"query": "x", "rrf": {"k": 0}},
        {"query": "x", "rrf": {"k": True}},
        {"query": "x", "rrf": {"weights": {"lexical": -1}}},
        {"query": "x", "rrf": {"weights": {"lexical": 0, "vector": 0}}},
        {"query": "x", "filters": {"unknown": "ignored previously"}},
        {"query": "x", "filters": {"tags": [""]}},
        {
            "query": "x",
            "filters": {
                "created_after": "2026-10-15T00:00:00Z",
                "created_before": "2026-10-14T00:00:00Z",
            },
        },
    ],
)
def test_invalid_search_parameters_return_422(client, body):
    assert (
        client.post("/v1/search", json=body, headers={"X-API-Key": "test-key"}).status_code == 422
    )
