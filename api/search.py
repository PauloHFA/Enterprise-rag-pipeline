from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import json
import time
import numpy as np
from sentence_transformers import SentenceTransformer
from .config import settings

# Global embedding model (loaded once)
_embedding_model = None

def get_embedding_model():
    """Lazy load the embedding model."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedding_model

def generate_query_embedding(query_text: str) -> List[float]:
    """Generate embedding for a query text."""
    model = get_embedding_model()
    embedding = model.encode(query_text)
    return embedding.tolist()

def hybrid_search(
    db: Session,
    query_text: str,
    top_k: int = 10,
    mode: str = "hybrid",
    filters: Optional[Dict[str, Any]] = None,
    rrf_k: int = 60,
    weights: Optional[Dict[str, float]] = None
) -> List[Dict[str, Any]]:
    """
    Perform hybrid search (lexical + vector) with RRF fusion.
    
    Args:
        db: Database session
        query_text: Search query
        top_k: Number of results to return
        mode: Search mode - "hybrid", "lexical", or "vector"
        filters: Dictionary of filters to apply (tenant_id, tags, date ranges, etc.)
        rrf_k: RRF constant (default 60)
        weights: Weights for each search type (default {"lexical": 1.0, "vector": 1.0})
    
    Returns:
        List of search results with scores and metadata
    """
    if weights is None:
        weights = {"lexical": 1.0, "vector": 1.0}
    
    # Generate query embedding
    query_embedding = generate_query_embedding(query_text)
    
    # Build filter conditions for chunks table
    filter_conditions = []
    filter_params = {
        "qtext": query_text,
        "qvec": query_embedding,
        "top_k": top_k,
        "rrf_k": rrf_k,
        "lexical_weight": weights.get("lexical", 1.0),
        "vector_weight": weights.get("vector", 1.0)
    }
    
    if filters:
        for key, value in filters.items():
            if key == "tenant_id":
                filter_conditions.append("d.tenant_id = :tenant_id")
                filter_params["tenant_id"] = value
            elif key == "created_after":
                filter_conditions.append("d.created_at >= :created_after")
                filter_params["created_after"] = value
            elif key == "created_before":
                filter_conditions.append("d.created_at <= :created_before")
                filter_params["created_before"] = value
            elif key == "tags":
                # Assuming tags are stored in metadata as JSONB array
                filter_conditions.append("d.metadata -> 'tags' ? :tags")
                filter_params["tags"] = value
    
    filter_clause = " AND ".join(filter_conditions) if filter_conditions else "1=1"
    
    # Build the SQL query based on mode
    if mode == "lexical":
        return _lexical_search(db, filter_clause, filter_params)
    elif mode == "vector":
        return _vector_search(db, filter_clause, filter_params)
    else:  # hybrid
        return _hybrid_search_rrf(db, filter_clause, filter_params)

def _lexical_search(db: Session, filter_clause: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Perform lexical search using PostgreSQL full-text search."""
    sql = text(f"""
        SELECT 
            c.id,
            c.document_id,
            c.page,
            c.content,
            ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) as rank,
            1.0 / (:rrf_k + ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.tsv @@ websearch_to_tsquery('portuguese', :qtext)
        AND {filter_clause}
        ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC
        LIMIT :top_k
    """)
    
    result = db.execute(sql, params)
    rows = result.fetchall()
    
    return [
        {
            "id": row.id,
            "document_id": row.document_id,
            "page": row.page,
            "snippet": row.content[:200] + "..." if len(row.content) > 200 else row.content,
            "score": float(row.score),
            "ranks": {"lexical": idx + 1, "vector": 0}
        }
        for idx, row in enumerate(rows)
    ]

def _vector_search(db: Session, filter_clause: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Perform vector similarity search using pgvector."""
    sql = text(f"""
        SELECT 
            c.id,
            c.document_id,
            c.page,
            c.content,
            1 - (c.embedding <=> :qvec) as similarity,
            1.0 / (:rrf_k + ROW_NUMBER() OVER (ORDER BY c.embedding <=> :qvec)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE {filter_clause}
        ORDER BY c.embedding <=> :qvec
        LIMIT :top_k
    """)
    
    result = db.execute(sql, params)
    rows = result.fetchall()
    
    return [
        {
            "id": row.id,
            "document_id": row.document_id,
            "page": row.page,
            "snippet": row.content[:200] + "..." if len(row.content) > 200 else row.content,
            "score": float(row.score),
            "ranks": {"lexical": 0, "vector": idx + 1}
        }
        for idx, row in enumerate(rows)
    ]

def _hybrid_search_rrf(db: Session, filter_clause: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Perform hybrid search with Reciprocal Rank Fusion."""
    sql = text(f"""
        WITH sem AS (
            SELECT 
                c.id,
                c.document_id,
                c.page,
                c.content,
                ROW_NUMBER() OVER (ORDER BY c.embedding <=> :qvec) AS rnk
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE {filter_clause}
            ORDER BY c.embedding <=> :qvec
            LIMIT 50
        ), lex AS (
            SELECT 
                c.id,
                c.document_id,
                c.page,
                c.content,
                ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC) AS rnk
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.tsv @@ websearch_to_tsquery('portuguese', :qtext)
            AND {filter_clause}
            ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC
            LIMIT 50
        )
        SELECT 
            COALESCE(sem.id, lex.id) AS id,
            COALESCE(sem.document_id, lex.document_id) AS document_id,
            COALESCE(sem.page, lex.page) AS page,
            COALESCE(sem.content, lex.content) AS content,
            COALESCE(:lexical_weight * 1.0 / (:rrf_k + sem.rnk), 0) + 
            COALESCE(:vector_weight * 1.0 / (:rrf_k + lex.rnk), 0) AS score,
            sem.rnk AS lexical_rank,
            lex.rnk AS vector_rank
        FROM sem
        FULL OUTER JOIN lex ON sem.id = lex.id
        ORDER BY score DESC
        LIMIT :top_k
    """)
    
    result = db.execute(sql, params)
    rows = result.fetchall()
    
    return [
        {
            "id": row.id,
            "document_id": row.document_id,
            "page": row.page,
            "snippet": row.content[:200] + "..." if len(row.content) > 200 else row.content,
            "score": float(row.score),
            "ranks": {
                "lexical": row.lexical_rank if row.lexical_rank else 0,
                "vector": row.vector_rank if row.vector_rank else 0
            }
        }
        for row in rows
    ]