from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader
from .config import settings

# API Key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def get_api_key(api_key: str = Security(api_key_header)):
    # If no API key configured, skip validation (for development)
    if not settings.API_KEY:
        return True
    if api_key == settings.API_KEY:
        return api_key
    else:
        raise HTTPException(
            status_code=403,
            detail="Could not validate credentials",
        )