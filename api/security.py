import hmac
import json

from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader
from .config import settings

# API Key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def get_tenant_id(api_key: str = Security(api_key_header)) -> str:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    try:
        tenant_keys = json.loads(settings.TENANT_API_KEYS or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=503, detail="Tenant API keys are misconfigured") from exc

    if not isinstance(tenant_keys, dict):
        raise HTTPException(status_code=503, detail="Tenant API keys are misconfigured")

    for tenant_id, configured_key in tenant_keys.items():
        if isinstance(tenant_id, str) and isinstance(configured_key, str):
            if hmac.compare_digest(api_key, configured_key):
                return tenant_id

    if settings.API_KEY and hmac.compare_digest(api_key, settings.API_KEY):
        return "default"

    raise HTTPException(status_code=401, detail="Could not validate credentials")