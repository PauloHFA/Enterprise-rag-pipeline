"""Real services and real embeddings; explicitly opt in against a test stack."""
import os
import time
import uuid

import httpx
import pytest

URL = os.getenv('RAG_E2E_URL')
KEY = os.getenv('RAG_E2E_API_KEY')
pytestmark = pytest.mark.skipif(not URL or not KEY, reason='Set RAG_E2E_URL and RAG_E2E_API_KEY for a real test stack')


def wait_for_completion(client, doc_id):
    deadline = time.monotonic() + int(os.getenv('RAG_E2E_TIMEOUT', '300'))
    while time.monotonic() < deadline:
        response = client.get(f'/v1/documents/{doc_id}')
        response.raise_for_status()
        document = response.json()
        assert document['status'] != 'failed', document.get('error_detail')
        if document['status'] == 'indexed':
            return document
        time.sleep(1)
    pytest.fail(f'Document {doc_id} did not complete within the configured timeout')


def test_upload_process_search_reindex_delete():
    import psycopg2
    from minio import Minio
    from minio.error import S3Error

    storage = Minio(os.getenv('RAG_E2E_MINIO_ENDPOINT', 'localhost:9000'),
                    access_key=os.getenv('RAG_E2E_MINIO_ACCESS_KEY', 'minioadmin'),
                    secret_key=os.getenv('RAG_E2E_MINIO_SECRET_KEY', 'minioadmin'), secure=False)
    database_url = os.getenv('RAG_E2E_DATABASE_URL', 'postgresql://raguser:ragpass@localhost:5432/ragdb')
    def count_rows(table, column, document_id):
        # Identifiers are constants from this test, never request input.
        with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM {table} WHERE {column} = %s', (document_id,))
            return cursor.fetchone()[0]

    marker = f'protocolo{uuid.uuid4().hex}'
    content = f'{marker} Contrato empresarial de notificações. O prazo de pagamento é trinta dias.'.encode()
    doc_id = None
    with httpx.Client(base_url=URL, headers={'X-API-Key': KEY}, timeout=180) as client:
        try:
            assert client.get('/readyz').status_code == 200
            upload = client.post('/v1/documents', files={'file': (f'{marker}.txt', content, 'text/plain')},
                                 data={'metadata': '{"source":"e2e"}'})
            assert upload.status_code == 202, upload.text
            doc_id = upload.json()['id']
            uuid.UUID(doc_id)
            assert upload.json()['metadata'] == {'source': 'e2e'}
            storage_key = upload.json()['storage_key']
            assert storage.stat_object('documents', storage_key).size == len(content)
            wait_for_completion(client, doc_id)
            chunk_count = count_rows('chunks', 'document_id', doc_id)
            assert chunk_count > 0
            duplicate = client.post('/v1/documents', files={'file': ('duplicate.txt', content, 'text/plain')})
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()['id'] == doc_id
            for mode in ('lexical', 'vector', 'hybrid'):
                response = client.post('/v1/search', json={'query': marker, 'mode': mode, 'top_k': 100})
                assert response.status_code == 200, response.text
                hits = [hit for hit in response.json()['results'] if hit['document_id'] == doc_id]
                assert hits, f'{mode}: {response.text}'
                assert marker in hits[0]['snippet']
                assert isinstance(hits[0]['chunk_id'], int)
            before = client.post('/v1/search', json={'query': marker, 'mode': 'lexical'}).json()['results']
            response = client.post(f'/v1/documents/{doc_id}/reindex')
            assert response.status_code == 202, response.text
            wait_for_completion(client, doc_id)
            assert count_rows('chunks', 'document_id', doc_id) == chunk_count
            after = client.post('/v1/search', json={'query': marker, 'mode': 'lexical'}).json()['results']
            assert len([hit for hit in before if hit['document_id'] == doc_id]) == len([hit for hit in after if hit['document_id'] == doc_id])
            assert client.delete(f'/v1/documents/{doc_id}').status_code == 204
            assert client.get(f'/v1/documents/{doc_id}').status_code == 404
            assert count_rows('documents', 'id', doc_id) == 0
            assert count_rows('chunks', 'document_id', doc_id) == 0
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    storage.stat_object('documents', storage_key)
                except S3Error as deleted_object:
                    assert deleted_object.code == 'NoSuchKey'
                    break
                time.sleep(0.5)
            else:
                pytest.fail('Durable storage cleanup did not complete')
            results = client.post('/v1/search', json={'query': marker, 'mode': 'lexical'}).json()['results']
            assert all(hit['document_id'] != doc_id for hit in results)
        finally:
            if doc_id:
                cleanup = client.delete(f'/v1/documents/{doc_id}')
                assert cleanup.status_code in (204, 404), cleanup.text
