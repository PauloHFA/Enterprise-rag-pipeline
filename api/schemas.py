import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class DocumentBase(BaseModel):
    tenant_id: str
    filename: str
    mime_type: str
    sha256: str
    storage_key: str
    status: str
    error_detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")


class DocumentCreate(DocumentBase):
    pass


class DocumentResponse(DocumentBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime | None = None
    title: str | None = None
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    processing_history: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ChunkBase(BaseModel):
    document_id: uuid.UUID
    chunk_idx: int
    page: int | None = None
    content: str
    chunker_version: str
    embedding_model: str | None = None
    # We are not exposing the embedding vector in the response for security and size reasons.
    # The tsv is also internal.


class ChunkResponse(ChunkBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str | None = Field(default=None, min_length=1, strict=True)
    created_after: AwareDatetime | None = None
    created_before: AwareDatetime | None = None
    tags: list[str] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        return [value] if isinstance(value, str) else value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        if value is not None and (not value or any(not tag.strip() for tag in value)):
            raise ValueError("Tags must contain nonempty strings")
        return value

    @model_validator(mode="after")
    def date_order(self):
        if self.created_after and self.created_before and self.created_after > self.created_before:
            raise ValueError("created_after must precede created_before")
        return self


class SearchWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lexical: float = Field(default=1.0, ge=0, strict=True)
    vector: float = Field(default=1.0, ge=0, strict=True)

    @model_validator(mode="after")
    def positive_weight(self):
        if self.lexical + self.vector <= 0:
            raise ValueError("At least one weight must be positive")
        return self


class RRFConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int = Field(default=60, ge=1, le=10000, strict=True)
    weights: SearchWeights = Field(default_factory=SearchWeights)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000, strict=True)
    top_k: int = Field(default=10, gt=0, le=100, strict=True)
    mode: Literal["hybrid", "lexical", "vector"] = "hybrid"
    filters: SearchFilters | None = None
    rrf: RRFConfig | None = Field(default_factory=RRFConfig)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Query must not be blank")
        return value


class Citation(BaseModel):
    document_id: uuid.UUID
    filename: str
    title: str
    page: int | None = None
    section: str | None = None
    section_idx: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    excerpt: str


class SearchResult(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    score: float
    page: int | None = None
    snippet: str
    ranks: dict[str, int]  # e.g., {"lexical": 3, "vector": 1}
    citation: Citation | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    took_ms: int
