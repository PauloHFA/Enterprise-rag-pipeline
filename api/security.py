import asyncio
import hmac
import json
import re
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader

from .config import settings

# API Key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def validate_auth_configuration():
    if any((settings.OIDC_ISSUER, settings.OIDC_AUDIENCE, settings.OIDC_JWKS_URL)):
        if not all(
            (
                settings.OIDC_ISSUER.startswith("https://"),
                settings.OIDC_JWKS_URL.startswith("https://"),
                settings.OIDC_AUDIENCE,
            )
        ):
            raise ValueError("OIDC requires HTTPS issuer/JWKS and audience")
    keys = json.loads(settings.TENANT_API_KEYS or "{}")
    if not isinstance(keys, dict):
        raise ValueError("TENANT_API_KEYS must be a JSON object")
    if not (settings.API_KEY or keys or settings.OIDC_ISSUER):
        raise ValueError("Configure API_KEY, TENANT_API_KEYS or OIDC before starting")
    for tenant, key in keys.items():
        valid_tenant(tenant)
        if not isinstance(key, str) or not key:
            raise ValueError("Tenant API keys must be nonempty strings")
    if (
        len(set(keys.values())) != len(keys)
        or settings.API_KEY
        and settings.API_KEY in keys.values()
    ):
        raise ValueError("API keys must be unique across tenants")


@lru_cache(maxsize=4)
def jwks_client(url):
    return jwt.PyJWKClient(url, timeout=5, lifespan=300)


def valid_tenant(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise HTTPException(401, "Invalid tenant identity")
    return value


def verify_bearer(token: str) -> str:
    if len(token) > 8192:
        raise HTTPException(401, "Invalid bearer token")
    if not all((settings.OIDC_ISSUER, settings.OIDC_AUDIENCE, settings.OIDC_JWKS_URL)):
        raise HTTPException(401, "Bearer authentication is not configured")
    if not settings.OIDC_JWKS_URL.startswith("https://") or not settings.OIDC_ISSUER.startswith(
        "https://"
    ):
        raise HTTPException(503, "OIDC requires HTTPS issuer and JWKS")
    try:
        key = jwks_client(settings.OIDC_JWKS_URL).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.OIDC_ISSUER,
            audience=settings.OIDC_AUDIENCE,
            options={"require": ["exp", "iat", "sub", settings.OIDC_TENANT_CLAIM]},
        )
        return valid_tenant(claims[settings.OIDC_TENANT_CLAIM])
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(503, "Identity provider unavailable") from exc
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Invalid bearer token") from exc


async def get_tenant_id(
    api_key: str = Security(api_key_header), authorization: str | None = Header(default=None)
) -> str:
    if isinstance(authorization, str):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or api_key:
            raise HTTPException(401, "Use one authentication method")
        return await asyncio.to_thread(verify_bearer, token)
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
            if hmac.compare_digest(api_key.encode(), configured_key.encode()):
                return valid_tenant(tenant_id)

    if settings.API_KEY and hmac.compare_digest(api_key.encode(), settings.API_KEY.encode()):
        return "default"

    raise HTTPException(status_code=401, detail="Could not validate credentials")
