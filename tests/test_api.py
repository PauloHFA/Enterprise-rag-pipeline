import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
import os

# Add api to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from api.main import app

client = TestClient(app)


class TestHealthEndpoints:
    """Test health check endpoints."""
    
    def test_healthz(self):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_readyz(self):
        response = client.get("/readyz")
        # May return 503 if dependencies not available, but should not crash
        assert response.status_code in [200, 503]


class TestAuthentication:
    """Test API key authentication."""
    
    def test_missing_api_key(self):
        response = client.post("/v1/documents", files={"file": ("test.txt", b"content")})
        assert response.status_code == 401
    
    def test_invalid_api_key(self):
        response = client.post(
            "/v1/documents",
            files={"file": ("test.txt", b"content")},
            headers={"X-API-Key": "invalid-key"}
        )
        assert response.status_code == 401
    
    def test_valid_api_key_format(self):
        # This will fail due to missing dependencies, but should not be 401
        response = client.post(
            "/v1/documents",
            files={"file": ("test.txt", b"content")},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        # Should not be 401 (auth passed), may be 500 due to missing deps
        assert response.status_code != 401


class TestDocumentEndpoints:
    """Test document CRUD endpoints."""
    
    @patch('api.main.minio_client')
    @patch('api.main.rabbitmq_client')
    @patch('api.main.get_db')
    def test_upload_document_success(self, mock_get_db, mock_rabbitmq, mock_minio):
        # Setup mocks
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_doc = MagicMock()
        mock_doc.id = "test-id"
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
        
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock(side_effect=lambda x: setattr(x, 'id', 'test-id'))
        
        mock_minio.upload_file = MagicMock()
        mock_rabbitmq.publish_message = MagicMock()
        
        response = client.post(
            "/v1/documents",
            files={"file": ("test.txt", b"test content")},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["filename"] == "test.txt"
        assert data["status"] == "queued"
    
    @patch('api.main.get_db')
    def test_get_document_not_found(self, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        response = client.get(
            "/v1/documents/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        assert response.status_code == 404
    
    @patch('api.main.get_db')
    @patch('api.main.minio_client')
    def test_delete_document_not_found(self, mock_minio, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        response = client.delete(
            "/v1/documents/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        assert response.status_code == 404


class TestSearchEndpoint:
    """Test search endpoint."""
    
    @patch('api.main.get_db')
    @patch('api.search.hybrid_search_sql')
    def test_search_success(self, mock_hybrid_search, mock_get_db):
        mock_db = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        
        mock_hybrid_search.return_value = [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "page": 1,
                "snippet": "test content",
                "score": 0.95,
                "ranks": {"lexical": 1, "vector": 2}
            }
        ]
        
        response = client.post(
            "/v1/search",
            json={"query": "test query", "top_k": 5},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "took_ms" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["chunk_id"] == "chunk-1"
    
    def test_search_missing_query(self):
        response = client.post(
            "/v1/search",
            json={"top_k": 5},
            headers={"X-API-Key": "your-super-secret-api-key-here-change-in-production"}
        )
        assert response.status_code == 422  # Validation error


class TestMetricsEndpoint:
    """Test metrics endpoint."""
    
    def test_metrics_format(self):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "enterprise_rag_info" in response.text
        assert response.headers["content-type"] == "text/plain; charset=utf-8"