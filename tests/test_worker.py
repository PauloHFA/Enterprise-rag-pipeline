import pytest
import sys
import os
from unittest.mock import patch, MagicMock, mock_open

# Add worker to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'worker'))

from worker.worker import (
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_html,
    extract_text,
    chunk_text,
    generate_embedding
)


class TestTextExtraction:
    """Test text extraction functions."""
    
    def test_extract_text_from_html(self):
        html_content = b"""
        <html>
            <head><title>Test</title></head>
            <body>
                <h1>Header</h1>
                <p>Paragraph 1</p>
                <p>Paragraph 2</p>
                <script>alert('test');</script>
                <style>body { color: red; }</style>
            </body>
        </html>
        """
        result = extract_text_from_html(html_content)
        assert "Header" in result
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result
        assert "alert" not in result  # script removed
        assert "color: red" not in result  # style removed
    
    def test_extract_text_plain(self):
        text_content = b"Plain text content\nWith multiple lines"
        result = extract_text(text_content, "text/plain")
        assert result == "Plain text content\nWith multiple lines"
    
    def test_extract_text_unsupported(self):
        result = extract_text(b"content", "application/unknown")
        assert result == ""
    
    @patch('worker.worker.fitz.open')
    def test_extract_text_from_pdf(self, mock_fitz_open):
        # Mock PyMuPDF
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Page 2 content"
        
        mock_doc = MagicMock()
        mock_doc.__iter__.return_value = [mock_page, mock_page2]
        mock_doc.__enter__.return_value = mock_doc
        mock_doc.__exit__.return_value = None
        
        mock_fitz_open.return_value = mock_doc
        
        result = extract_text_from_pdf(b"fake pdf content")
        assert "Page 1 content" in result
        assert "Page 2 content" in result
    
    @patch('worker.worker.docx.Document')
    def test_extract_text_from_docx(self, mock_docx_document):
        mock_para1 = MagicMock()
        mock_para1.text = "Paragraph 1"
        mock_para2 = MagicMock()
        mock_para2.text = "Paragraph 2"
        
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2]
        
        mock_docx_document.return_value = mock_doc
        
        result = extract_text_from_docx(b"fake docx content")
        assert "Paragraph 1" in result
        assert "Paragraph 2" in result


class TestChunking:
    """Test text chunking function."""
    
    def test_chunk_text_basic(self):
        text = "A" * 1000  # 1000 characters
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        
        # Should create 3 chunks: 500 + 450 + 50 (with overlap)
        assert len(chunks) == 3
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        assert len(chunks[2]) == 50  # remainder
        
        # Check overlap
        assert chunks[0][-50:] == chunks[1][:50]
        assert chunks[1][-50:] == chunks[2][:50]
    
    def test_chunk_text_exact_fit(self):
        text = "A" * 500
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) == 1
        assert len(chunks[0]) == 500
    
    def test_chunk_text_smaller_than_chunk(self):
        text = "Short text"
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) == 1
        assert chunks[0] == "Short text"
    
    def test_chunk_text_empty(self):
        chunks = chunk_text("", chunk_size=500, overlap=50)
        assert chunks == []
    
    def test_chunk_text_overlap_zero(self):
        text = "A" * 1000
        chunks = chunk_text(text, chunk_size=500, overlap=0)
        assert len(chunks) == 2
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        # No overlap
        assert chunks[0][-1] != chunks[1][0]


class TestEmbeddingGeneration:
    """Test embedding generation."""
    
    @patch('worker.worker.embedding_model')
    def test_generate_embedding(self, mock_embedding_model):
        # Mock the sentence transformer model
        mock_embedding_model.encode.return_value = [0.1] * 384
        
        embedding = generate_embedding("test query")
        
        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)
        assert all(x == 0.1 for x in embedding)
        mock_embedding_model.encode.assert_called_once_with("test query")
    
    @patch('worker.worker.embedding_model')
    def test_generate_embedding_empty_string(self, mock_embedding_model):
        mock_embedding_model.encode.return_value = [0.0] * 384
        
        embedding = generate_embedding("")
        
        assert len(embedding) == 384
        mock_embedding_model.encode.assert_called_once_with("")


class TestDispatchExtractText:
    """Test the dispatch function for text extraction."""
    
    @patch('worker.worker.extract_text_from_pdf')
    def test_extract_text_pdf_dispatch(self, mock_pdf):
        mock_pdf.return_value = "PDF content"
        result = extract_text(b"content", "application/pdf")
        assert result == "PDF content"
        mock_pdf.assert_called_once_with(b"content")
    
    @patch('worker.worker.extract_text_from_docx')
    def test_extract_text_docx_dispatch(self, mock_docx):
        mock_docx.return_value = "DOCX content"
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        result = extract_text(b"content", mime)
        assert result == "DOCX content"
        mock_docx.assert_called_once_with(b"content")
    
    @patch('worker.worker.extract_text_from_html')
    def test_extract_text_html_dispatch(self, mock_html):
        mock_html.return_value = "HTML content"
        result = extract_text(b"content", "text/html")
        assert result == "HTML content"
        mock_html.assert_called_once_with(b"content")
    
    def test_extract_text_plain_dispatch(self):
        result = extract_text(b"Plain text", "text/plain")
        assert result == "Plain text"