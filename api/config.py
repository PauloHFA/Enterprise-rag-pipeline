from pydantic import BaseSettings, Field
from typing import Optional, Dict, Any
import os

class Settings(BaseSettings):
    # API Settings
    API_V1_STR: str = "/v1"
    PROJECT_NAME: str = "Enterprise RAG Pipeline"
    VERSION: str = "0.1.0"
    
    # Security
    API_KEY: str = Field(default="", env="API_KEY")
    
    # Database
    DATABASE_URL: str = Field(default="postgresql://raguser:ragpass@localhost:5432/ragdb", env="DATABASE_URL")
    
    # RabbitMQ
    RABBITMQ_URL: str = Field(default="amqp://guest:guest@localhost:5672", env="RABBITMQ_URL")
    
    # MinIO
    MINIO_ENDPOINT: str = Field(default="localhost:9000", env="MINIO_ENDPOINT")
    MINIO_ACCESS_KEY: str = Field(default="minioadmin", env="MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY: str = Field(default="minioadmin", env="MINIO_SECRET_KEY")
    MINIO_SECURE: bool = Field(default=False, env="MINIO_SECURE")
    MINIO_BUCKET: str = Field(default="documents", env="MINIO_BUCKET")
    
    # Embedding Model
    EMBEDDING_MODEL_NAME: str = Field(default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", env="EMBEDDING_MODEL_NAME")
    EMBEDDING_DIMENSION: int = Field(default=384, env="EMBEDDING_DIMENSION")
    
    # Worker Settings
    CHUNK_SIZE: int = Field(default=500, env="CHUNK_SIZE")
    CHUNK_OVERLAP: int = Field(default=50, env="CHUNK_OVERLAP")
    
    # Search Settings
    SEARCH_DEFAULT_TOP_K: int = Field(default=10, env="SEARCH_DEFAULT_TOP_K")
    SEARCH_MAX_TOP_K: int = Field(default=100, env="SEARCH_MAX_TOP_K")
    RRF_K: int = Field(default=60, env="RRF_K")
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, env="RATE_LIMIT_PER_MINUTE")
    
    # Logging
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()