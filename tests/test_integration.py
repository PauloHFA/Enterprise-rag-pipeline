import pytest
import os
import sys
import time
from unittest.mock import patch, MagicMock

# Add project paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))


class TestFullPipeline:
    """Integration tests for the full document processing pipeline."""
    
    @pytest.fixture
    def mock_services(self):
        """Mock all external services for integration testing."""
        with patch('api.main.minio_client') as mock_minio, \
             patch('api.main.rabbitmq_client') as mock_rabbitmq, \
             patch('api.main.get_db') as mock_get_db, \
             patch('worker.worker.minio_client') as mock_worker_minio, \
             patch('worker.worker.get_rabbitmq_connection') as mock_rabbitmq_conn, \
             patch('worker.worker.SessionLocal') as mock_session_local, \
             patch('worker.worker.embedding_model') as mock_embedding:
            
            # Setup API mocks
            mock_db = MagicMock()
            mock_get_db.return_value = iter([mock_db])
            
            # Setup Worker mocks
            mock_worker_db = MagicMock()
            mock_session_local.return_value = mock_worker_db
            
            mock_embedding.encode.return_value = [0.1] * 384
            
            yield {
                'minio': mock_minio,
                'rabbitmq': mock_rabbitmq,
                'api_db': mock_db,
                'worker_db': mock_worker_db,
                'embedding': mock_embedding,
                'worker_minio': mock_worker_minio,
                'rabbitmq_conn': mock_rabbitmq_conn
            }
    
    def test_document_upload_to_search_flow(self, mock_services):
        """Test the complete flow: upload -> process -> search."""
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        
        # Setup mocks for upload
        mock_doc = MagicMock()
        mock_doc.id = "test-doc-id"
        mock_doc.tenant_id = "default"
        mock_doc.filename = "test.txt"
        mock_doc.mime_type = "text/plain"
        mock_doc.sha256 = "abc123"
        mock_doc.storage_key = "default/test.txt"
        mock_doc.status = "queued"
        mock_doc.metadata = {}
        mock_doc.created_at = None
        mock_doc.updated_at = None
        mock_doc.error_detail = None
        
        mock_services['api_db'].query.return_value.filter.return_value.first.return_value = None
        mock_services['api_db'].add = MagicMock()
        mock_services['api_db'].commit = MagicMock()
        mock_services['api_db'].refresh = MagicMock(
            side_effect=lambda x: setattr(x, 'id', 'test-doc-id')
        )
        
        mock_services['minio'].upload_file = MagicMock()
        mock_services['rabbitmq'].publish_message = MagicMock()
        
        # 1. Upload document
        response = client.post(
            "/v1/documents",
            files={"file": ("test.txt", b"This is a test document about enterprise RAG systems.")},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 202
        upload_data = response.json()
        assert upload_data["id"] == "test-doc-id"
        assert upload_data["status"] == "queued"
        
        # Verify message was published to RabbitMQ
        mock_services['rabbitmq'].publish_message.assert_called_once()
        call_args = mock_services['rabbitmq'].publish_message.call_args
        assert call_args[0][0] == "document.upload"
        assert call_args[0][1]["document_id"] == "test-doc-id"
        
        # 2. Simulate worker processing
        from worker.worker import process_document_message
        import json
        
        # Mock MinIO get_object for worker
        mock_response = MagicMock()
        mock_response.read.return_value = b"This is a test document about enterprise RAG systems."
        mock_response.close = MagicMock()
        mock_response.release_conn = MagicMock()
        mock_services['worker_minio'].get_object.return_value = mock_response
        
        # Mock document in worker DB
        mock_worker_doc = MagicMock()
        mock_worker_doc.id = "test-doc-id"
        mock_worker_doc.tenant_id = "default"
        mock_worker_doc.filename = "test.txt"
        mock_worker_doc.mime_type = "text/plain"
        mock_worker_doc.storage_key = "default/test.txt"
        mock_worker_doc.status = "queued"
        mock_worker_doc.metadata = {}
        
        mock_services['worker_db'].query.return_value.filter.return_value.first.return_value = mock_worker_doc
        mock_services['worker_db'].commit = MagicMock()
        
        # Create message body
        message_body = json.dumps({
            "document_id": "test-doc-id",
            "tenant_id": "default",
            "storage_key": "default/test.txt",
            "filename": "test.txt",
            "mime_type": "text/plain",
            "metadata": {}
        }).encode()
        
        # Mock channel and method
        mock_channel = MagicMock()
        mock_method = MagicMock()
        mock_method.delivery_tag = 1
        mock_properties = MagicMock()
        mock_properties.headers = {}
        
        # Process the message
        process_document_message(mock_channel, mock_method, mock_properties, message_body)
        
        # Verify document status updated to completed
        assert mock_worker_doc.status == "completed"
        assert mock_services['worker_db'].commit.called
        
        # Verify chunks were saved
        assert mock_services['worker_db'].add.called
        
        # 3. Search for the document
        with patch('api.search.hybrid_search_sql') as mock_search:
            mock_search.return_value = [
                {
                    "id": "chunk-1",
                    "document_id": "test-doc-id",
                    "page": None,
                    "snippet": "This is a test document about enterprise RAG systems.",
                    "score": 0.95,
                    "ranks": {"lexical": 1, "vector": 1}
                }
            ]
            
            response = client.post(
                "/v1/search",
                json={"query": "enterprise RAG", "top_k": 5},
                headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
            )
            
            assert response.status_code == 200
            search_data = response.json()
            assert len(search_data["results"]) == 1
            assert search_data["results"][0]["document_id"] == "test-doc-id"
            assert "RAG" in search_data["results"][0]["snippet"]


class TestIdempotency:
    """Test idempotent document upload (SHA-256 deduplication)."""
    
    @patch('api.main.minio_client')
    @patch('api.main.rabbitmq_client')
    @patch('api.main.get_db')
    def test_duplicate_upload_returns_existing(self, mock_get_db, mock_rabbitmq, mock_minio):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        
        # Setup existing document
        mock_existing_doc = MagicMock()
        mock_existing_doc.id = "existing-doc-id"
        mock_existing_doc.tenant_id = "default"
        mock_existing_doc.filename = "test.txt"
        mock_existing_doc.mime_type = "text/plain"
        mock_existing_doc.sha256 = "abc123"  # Same hash
        mock_existing_doc.storage_key = "default/existing.txt"
        mock_existing_doc.status = "completed"
        mock_existing_doc.metadata = {}
        mock_existing_doc.created_at = None
        mock_existing_doc.updated_at = None
        mock_existing_doc.error_detail = None
        
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        mock_db.query.return_value.filter.return_value.first.return_value = mock_existing_doc
        
        # Upload same content twice
        content = b"Duplicate content"
        
        response1 = client.post(
            "/v1/documents",
            files={"file": ("test1.txt", content)},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        response2 = client.post(
            "/v1/documents",
            files={"file": ("test2.txt", content)},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response1.status_code == 202
        assert response2.status_code == 202
        
        # Both should return the same existing document
        assert response1.json()["id"] == "existing-doc-id"
        assert response2.json()["id"] == "existing-doc-id"
        
        # MinIO upload should only be called once (for first upload)
        # Actually, the current implementation checks DB first, so MinIO not called for duplicates
        mock_minio.upload_file.assert_not_called()


class TestSearchModes:
    """Test different search modes (lexical, vector, hybrid)."""
    
    @patch('api.main.get_db')
    @patch('api.search.hybrid_search_sql')
    def test_search_mode_lexical(self, mock_hybrid_search, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_hybrid_search.return_value = [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "page": 1,
                "snippet": "lexical result",
                "score": 0.8,
                "ranks": {"lexical": 1, "vector": 0}
            }
        ]
        
        response = client.post(
            "/v1/search",
            json={"query": "test", "top_k": 5, "mode": "lexical"},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 200
        # Verify hybrid_search_sql was called with mode="lexical"
        mock_hybrid_search.assert_called_once()
        call_kwargs = mock_hybrid_search.call_args[1]
        assert call_kwargs["mode"] == "lexical"
    
    @patch('api.main.get_db')
    @patch('api.search.hybrid_search_sql')
    def test_search_mode_vector(self, mock_hybrid_search, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_hybrid_search.return_value = [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "page": 1,
                "snippet": "vector result",
                "score": 0.9,
                "ranks": {"lexical": 0, "vector": 1}
            }
        ]
        
        response = client.post(
            "/v1/search",
            json={"query": "test", "top_k": 5, "mode": "vector"},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 200
        call_kwargs = mock_hybrid_search.call_args[1]
        assert call_kwargs["mode"] == "vector"
    
    @patch('api.main.get_db')
    @patch('api.search.hybrid_search_sql')
    def test_search_mode_hybrid_default(self, mock_hybrid_search, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_hybrid_search.return_value = [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "page": 1,
                "snippet": "hybrid result",
                "score": 0.95,
                "ranks": {"lexical": 1, "vector": 2}
            }
        ]
        
        response = client.post(
            "/v1/search",
            json={"query": "test", "top_k": 5},  # No mode specified
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 200
        call_kwargs = mock_hybrid_search.call_args[1]
        assert call_kwargs["mode"] == "hybrid"


class TestSearchFilters:
    """Test search with filters."""
    
    @patch('api.main.get_db')
    @patch('api.search.hybrid_search_sql')
    def test_search_with_tenant_filter(self, mock_hybrid_search, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_hybrid_search.return_value = []
        
        response = client.post(
            "/v1/search",
            json={
                "query": "test",
                "top_k": 5,
                "filters": {"tenant_id": "tenant-123"}
            },
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 200
        call_kwargs = mock_hybrid_search.call_args[1]
        assert call_kwargs["filters"] == {"tenant_id": "tenant-123"}


class TestReindexEndpoint:
    """Test document reindex endpoint."""
    
    @patch('api.main.get_db')
    @patch('api.main.rabbitmq_client')
    def test_reindex_document(self, mock_rabbitmq, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        
        mock_doc = MagicMock()
        mock_doc.id = "doc-to-reindex"
        mock_doc.tenant_id = "default"
        mock_doc.storage_key = "default/doc.txt"
        mock_doc.filename = "doc.txt"
        mock_doc.mime_type = "text/plain"
        mock_doc.metadata = {}
        mock_doc.status = "completed"
        mock_doc.error_detail = None
        
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc
        mock_db.commit = MagicMock()
        
        mock_rabbitmq.publish_message = MagicMock()
        
        response = client.post(
            "/v1/documents/doc-to-reindex/reindex",
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 202
        assert response.json()["message"] == "Reindexing started"
        
        # Verify document status reset to queued
        assert mock_doc.status == "queued"
        assert mock_doc.error_detail is None
        mock_db.commit.assert_called()
        
        # Verify message published
        mock_rabbitmq.publish_message.assert_called_once()
        call_args = mock_rabbitmq.publish_message.call_args
        assert call_args[0][0] == "document.reindex"


class TestDeleteEndpoint:
    """Test document delete endpoint."""
    
    @patch('api.main.get_db')
    @patch('api.main.minio_client')
    def test_delete_document(self, mock_minio, mock_get_db):
        from fastapi.testclient import TestClient
        from api.main import app
        
        client = TestClient(app)
        
        mock_doc = MagicMock()
        mock_doc.id = "doc-to-delete"
        mock_doc.storage_key = "default/doc.txt"
        
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc
        mock_db.delete = MagicMock()
        mock_db.commit = MagicMock()
        
        mock_minio.delete_file = MagicMock()
        
        response = client.delete(
            "/v1/documents/doc-to-delete",
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 204
        
        # Verify MinIO delete called
        mock_minio.delete_file.assert_called_once_with(
            bucket_name="documents",
            object_name="default/doc.txt"
        )
        
        # Verify DB delete called
        mock_db.delete.assert_called_once_with(mock_doc)
        mock_db.commit.assert_called_once()