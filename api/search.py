import threading
from typing import Any

import numpy as np
from pgvector.sqlalchemy import Vector
from sentence_transformers import SentenceTransformer
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .config import settings

# Global embedding model (loaded once)
_embedding_model = None
_model_load_lock = threading.Lock()
_inference_lock = threading.Lock()


def get_embedding_model():
    """Lazy load the embedding model."""
    global _embedding_model
    if _embedding_model is None:
        with _model_load_lock:
            if _embedding_model is None:
                _embedding_model = SentenceTransformer(
                    settings.EMBEDDING_MODEL_NAME,
                    revision=settings.EMBEDDING_MODEL_REVISION or None,
                )
    return _embedding_model


def generate_query_embedding(query_text: str) -> list[float]:
    """Generate embedding for a query text."""
    model = get_embedding_model()
    # Tokenizer/model configuration is shared between FastAPI worker threads.
    # Serialize encode while HTTP handling and SQL remain off the event loop.
    with _inference_lock:
        embedding = model.encode(query_text)
    embedding = np.asarray(embedding, dtype=float)
    if embedding.shape != (settings.EMBEDDING_DIMENSION,) or not np.isfinite(embedding).all():
        raise ValueError("Query embedding dimension or values do not match database configuration")
    return [float(value) for value in embedding]


def hybrid_search(
    db: Session,
    query_text: str,
    top_k: int = 10,
    mode: str = "hybrid",
    filters: dict[str, Any] | None = None,
    rrf_k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
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

    query_embedding = generate_query_embedding(query_text) if mode != "lexical" else None

    # Build filter conditions for chunks table
    filter_conditions = []
    filter_params = {
        "qtext": query_text,
        "top_k": top_k,
        "candidate_limit": max(50, top_k * 3),
        "rrf_k": rrf_k,
        "lexical_weight": weights.get("lexical", 1.0),
        "vector_weight": weights.get("vector", 1.0),
    }
    if query_embedding is not None:
        filter_params["qvec"] = query_embedding

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
                filter_conditions.append("d.metadata -> 'tags' ?& CAST(:tags AS text[])")
                filter_params["tags"] = value if isinstance(value, list) else [value]

    filter_clause = " AND ".join(filter_conditions) if filter_conditions else "1=1"

    # Build the SQL query based on mode
    if mode == "lexical":
        return _lexical_search(db, filter_clause, filter_params)
    elif mode == "vector":
        return _vector_search(db, filter_clause, filter_params)
    else:  # hybrid
        return _hybrid_search_rrf(db, filter_clause, filter_params)


def _lexical_search(
    db: Session, filter_clause: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Perform lexical search using PostgreSQL full-text search."""
    sql = text(f"""
        SELECT
            c.id,
            c.document_id,
            c.page, c.section, c.section_idx, c.offset_start, c.offset_end,
            d.filename, COALESCE(d.title, d.filename) AS title,
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

    return [_format_result(row, {"lexical": idx + 1, "vector": 0}) for idx, row in enumerate(rows)]


def _vector_search(db: Session, filter_clause: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Perform vector similarity search using pgvector."""
    sql = text(f"""
        SELECT
            c.id,
            c.document_id,
            c.page, c.section, c.section_idx, c.offset_start, c.offset_end,
            d.filename, COALESCE(d.title, d.filename) AS title,
            c.content,
            1 - (c.embedding <=> :qvec) as similarity,
            1.0 / (:rrf_k + ROW_NUMBER() OVER (ORDER BY c.embedding <=> :qvec)) as score
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.embedding IS NOT NULL AND {filter_clause}
        ORDER BY c.embedding <=> :qvec
        LIMIT :top_k
    """).bindparams(bindparam("qvec", type_=Vector(settings.EMBEDDING_DIMENSION)))

    result = db.execute(sql, params)
    rows = result.fetchall()

    return [_format_result(row, {"lexical": 0, "vector": idx + 1}) for idx, row in enumerate(rows)]


def _hybrid_search_rrf(
    db: Session, filter_clause: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Perform hybrid search with Reciprocal Rank Fusion."""
    sql = text(f"""
        WITH sem AS (
            SELECT
                c.id,
                c.document_id,
                c.page, c.section, c.section_idx, c.offset_start, c.offset_end,
                d.filename, COALESCE(d.title, d.filename) AS title,
                c.content,
                ROW_NUMBER() OVER (ORDER BY c.embedding <=> :qvec) AS rnk
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL AND {filter_clause}
            ORDER BY c.embedding <=> :qvec
            LIMIT :candidate_limit
        ), lex AS (
            SELECT
                c.id,
                c.document_id,
                c.page, c.section, c.section_idx, c.offset_start, c.offset_end,
                d.filename, COALESCE(d.title, d.filename) AS title,
                c.content,
                ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC) AS rnk
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.tsv @@ websearch_to_tsquery('portuguese', :qtext)
            AND {filter_clause}
            ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('portuguese', :qtext)) DESC
            LIMIT :candidate_limit
        )
        SELECT
            COALESCE(sem.id, lex.id) AS id,
            COALESCE(sem.document_id, lex.document_id) AS document_id,
            COALESCE(sem.page, lex.page) AS page,
            COALESCE(sem.section, lex.section) AS section,
            COALESCE(sem.section_idx, lex.section_idx) AS section_idx,
            COALESCE(sem.offset_start, lex.offset_start) AS offset_start,
            COALESCE(sem.offset_end, lex.offset_end) AS offset_end,
            COALESCE(sem.filename, lex.filename) AS filename,
            COALESCE(sem.title, lex.title) AS title,
            COALESCE(sem.content, lex.content) AS content,
            COALESCE(:lexical_weight * 1.0 / (:rrf_k + lex.rnk), 0) +
            COALESCE(:vector_weight * 1.0 / (:rrf_k + sem.rnk), 0) AS score,
            lex.rnk AS lexical_rank,
            sem.rnk AS vector_rank
        FROM sem
        FULL OUTER JOIN lex ON sem.id = lex.id
        ORDER BY score DESC
        LIMIT :top_k
    """).bindparams(bindparam("qvec", type_=Vector(settings.EMBEDDING_DIMENSION)))

    result = db.execute(sql, params)
    rows = result.fetchall()

    return [
        _format_result(row, {"lexical": row.lexical_rank or 0, "vector": row.vector_rank or 0})
        for row in rows
    ]


def _format_result(row, ranks):
    excerpt = row.content[:200]
    start = row.offset_start
    return {
        "id": row.id,
        "document_id": row.document_id,
        "page": row.page,
        "snippet": excerpt + ("..." if len(row.content) > 200 else ""),
        "score": float(row.score),
        "ranks": ranks,
        "citation": {
            "document_id": row.document_id,
            "filename": row.filename,
            "title": row.title,
            "page": row.page,
            "section": row.section,
            "section_idx": row.section_idx,
            "offset_start": start,
            "offset_end": start + len(excerpt) if start is not None else None,
            "excerpt": excerpt,
        },
    }
