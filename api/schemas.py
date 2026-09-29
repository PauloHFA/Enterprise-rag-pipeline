from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime

class DocumentBase(BaseModel):
    tenant_id: str
    filename: str
    mime_type: str
    sha256: str
    storage_key: str
    status: str
    error_detail: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DocumentCreate(DocumentBase):
    pass

class DocumentResponse(DocumentBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class ChunkBase(BaseModel):
    document_id: uuid.UUID
    chunk_idx: int
    page: Optional[int] = None
    content: str
    chunker_version: str
    embedding_model: Optional[str] = None
    # We are not exposing the embedding vector in the response for security and size reasons.
    # The tsv is also internal.

class ChunkResponse(ChunkBase):
    id: int

    class Config:
        orm_mode = True

class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, gt=0)
    mode: str = Field(default="hybrid", regex="^(hybrid|lexical|vector)$")
    filters: Optional[Dict[str, Any]] = None
    rrf: Optional[Dict[str, Any]] = Field(default={"k": 60, "weights": {"lexical": 1.0, "vector": 1.0}})

class SearchResult(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    score: float
    page: Optional[int] = None
    snippet: str
    ranks: Dict[str, int]  # e.g., {"lexical": 3, "vector": 1}

class SearchResponse(BaseModel):
    results: List[SearchResult]
    took_ms: int