import asyncio
import uuid
from datetime import datetime, timezone
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
    monkeypatch.setattr(settings, 'API_KEY', 'test-key')
    monkeypatch.setattr(settings, 'TENANT_API_KEYS', '{"acme":"acme-key"}')
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    def refresh(doc):
        doc.id = uuid.uuid4()
        doc.created_at = datetime.now(timezone.utc)
        doc.updated_at = doc.created_at
    db.refresh.side_effect = refresh
    main.app.dependency_overrides[main.get_db] = lambda: db
    storage = MagicMock()
    broker = MagicMock()
    broker.publish_message = AsyncMock()
    monkeypatch.setattr(main, 'minio_client', storage)
    monkeypatch.setattr(main, 'rabbitmq_client', broker)
    yield db, storage, broker
    main.app.dependency_overrides.clear()


@pytest.fixture
def client(services):
    return TestClient(main.app)


def test_health_and_metrics(client):
    assert client.get('/healthz').json() == {'status': 'ok'}
    response = client.get('/metrics')
    assert response.status_code == 200
    assert 'enterprise_rag_search_latency_seconds' in response.text


@pytest.mark.parametrize('headers', [{}, {'X-API-Key': 'invalid'}])
def test_upload_requires_authentication(client, headers):
    assert client.post('/v1/documents', files={'file': ('test.txt', b'text')}, headers=headers).status_code == 401


def test_upload_response_storage_and_event(client, services):
    db, storage, broker = services
    response = client.post('/v1/documents', files={'file': ('test.txt', b'conteudo', 'text/plain')},
                           data={'metadata': '{"title":"Teste"}'}, headers={'X-API-Key': 'test-key'})
    assert response.status_code == 202, response.text
    result = response.json()
    uuid.UUID(result['id'])
    assert result['metadata'] == {'title': 'Teste'}
    assert result['status'] == 'queued'
    assert result['updated_at'] is not None
    assert storage.upload_file.call_args.kwargs['data'] == b'conteudo'
    broker.publish_message.assert_awaited_once()
    assert broker.publish_message.call_args.args[1]['document_id'] == result['id']


@pytest.mark.parametrize('metadata', ['{', '[]', 'null', '12'])
def test_invalid_metadata_does_not_write(client, services, metadata):
    response = client.post('/v1/documents', files={'file': ('test.txt', b'text')},
                           data={'metadata': metadata}, headers={'X-API-Key': 'test-key'})
    assert response.status_code == 400
    services[1].upload_file.assert_not_called()


def test_tenant_isolation(client, services):
    response = client.post('/v1/documents', files={'file': ('test.txt', b'text')},
                           data={'tenant_id': 'other'}, headers={'X-API-Key': 'acme-key'})
    assert response.status_code == 403
    response = client.post('/v1/search', json={'query': 'test', 'filters': {'tenant_id': 'other'}},
                           headers={'X-API-Key': 'acme-key'})
    assert response.status_code == 403
    assert asyncio.run(get_tenant_id('acme-key')) == 'acme'


def test_unconfigured_key_rejected(services, monkeypatch):
    monkeypatch.setattr(settings, 'API_KEY', '')
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_tenant_id('unknown'))
    assert exc.value.status_code == 401


def test_document_not_found(client):
    doc_id = str(uuid.uuid4())
    for method, suffix in [('get', ''), ('delete', ''), ('post', '/reindex')]:
        response = getattr(client, method)(f'/v1/documents/{doc_id}{suffix}', headers={'X-API-Key': 'test-key'})
        assert response.status_code == 404


def test_search_contract_and_authenticated_filter(client, services, monkeypatch):
    from api import search
    doc_id = uuid.uuid4()
    execute = MagicMock(return_value=[{'id': 1, 'document_id': doc_id, 'score': 0.1,
                                      'snippet': 'Contrato', 'ranks': {'lexical': 1, 'vector': 1}}])
    monkeypatch.setattr(search, 'hybrid_search', execute)
    response = client.post('/v1/search', json={'query': 'contrato'}, headers={'X-API-Key': 'acme-key'})
    assert response.status_code == 200, response.text
    assert response.json()['results'][0]['chunk_id'] == 1
    assert execute.call_args.kwargs['filters'] == {'tenant_id': 'acme'}


def test_minio_upload_uses_stream():
    storage = MinioClient()
    storage.client = MagicMock()
    storage.upload_file('documents', 'test.txt', b'content', 7, 'text/plain')
    assert storage.client.put_object.call_args.kwargs['data'].read() == b'content'


def test_legacy_document_nullable_timestamp():
    from api.schemas import DocumentResponse
    doc = SimpleNamespace(id=uuid.uuid4(), tenant_id='default', filename='old.txt',
                          mime_type='text/plain', sha256='a'*64, storage_key='old.txt', status='queued',
                          error_detail=None, metadata_={}, created_at=datetime.now(timezone.utc), updated_at=None)
    assert DocumentResponse.model_validate(doc).updated_at is None
