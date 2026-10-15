from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/v1"
    PROJECT_NAME: str = "Enterprise RAG Pipeline"
    VERSION: str = "0.1.0"

    # Security
    API_KEY: str = ""
    TENANT_API_KEYS: str = ""
    OIDC_ISSUER: str = ""
    OIDC_AUDIENCE: str = ""
    OIDC_JWKS_URL: str = ""
    OIDC_TENANT_CLAIM: str = "tenant_id"
    MAX_UPLOAD_BYTES: int = Field(default=20 * 1024 * 1024, gt=0)
    MAX_MULTIPART_OVERHEAD_BYTES: int = Field(default=64 * 1024, gt=0)
    MAX_DOCX_EXPANDED_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    RETENTION_DAYS: int = Field(default=0, ge=0)
    OUTBOX_RETENTION_DAYS: int = Field(default=7, ge=1)

    # Database
    DATABASE_URL: str = "postgresql://raguser:ragpass@localhost:5432/ragdb"

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672"
    OUTBOX_POLL_SECONDS: float = Field(default=1.0, gt=0)
    OUTBOX_BATCH_SIZE: int = Field(default=20, gt=0)
    OUTBOX_MAX_BACKOFF_SECONDS: int = Field(default=60, gt=0)
    MESSAGE_RETRY_DELAYS_MS: str = "1000,5000,30000"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET: str = "documents"

    # Embedding Model
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIMENSION: int = Field(default=384, ge=384, le=384)
    EMBEDDING_MODEL_REVISION: str = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"

    # Worker Settings
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    CHUNK_TARGET_TOKENS: int = Field(default=96, gt=0)
    CHUNK_OVERLAP_TOKENS: int = Field(default=12, ge=0)
    EMBEDDING_BATCH_SIZE: int = Field(default=32, gt=0, le=256)
    OCR_ENABLED: bool = False
    OCR_LANGUAGES: str = "por+eng"

    # Search Settings
    SEARCH_DEFAULT_TOP_K: int = 10
    SEARCH_MAX_TOP_K: int = 100
    RRF_K: int = 60

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    # Logging
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


settings = Settings()
