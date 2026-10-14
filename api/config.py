from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/v1"
    PROJECT_NAME: str = "Enterprise RAG Pipeline"
    VERSION: str = "0.1.0"
    
    # Security
    API_KEY: str = ""
    TENANT_API_KEYS: str = ""
    
    # Database
    DATABASE_URL: str = "postgresql://raguser:ragpass@localhost:5432/ragdb"
    
    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672"
    OUTBOX_POLL_SECONDS: float = Field(default=1.0, gt=0)
    OUTBOX_BATCH_SIZE: int = Field(default=20, gt=0)
    OUTBOX_MAX_BACKOFF_SECONDS: int = Field(default=60, gt=0)
    MESSAGE_RETRY_DELAYS_MS: str = '1000,5000,30000'
    
    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_SECURE: bool = False
    MINIO_BUCKET: str = "documents"
    
    # Embedding Model
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_DIMENSION: int = 384
    
    # Worker Settings
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    
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
