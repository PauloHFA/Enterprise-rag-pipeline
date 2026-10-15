import asyncio
import io
import json
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from api import security
from api.config import settings
from api.uploads import UploadLimitMiddleware, validate_upload


@pytest.mark.parametrize(
    "name,content,mime",
    [
        ("fake.pdf", b"text", "application/pdf"),
        ("fake.txt", b"%PDF-1.7", "text/plain"),
        ("fake.txt", b"<html><body>content</body></html>", "text/plain"),
        ("binary.txt", b"\x00\x01", "text/plain"),
        ("binary.txt", b"\xff", "text/plain"),
        ("file.exe", b"MZ123", "application/octet-stream"),
        ("empty.txt", b"", "text/plain"),
        ("file.txt", b"text", "application/pdf"),
        ("broken.docx", b"PK123", "application/octet-stream"),
    ],
)
def test_invalid_uploads(name, content, mime):
    with pytest.raises(HTTPException) as error:
        validate_upload(content, name, mime)
    assert error.value.status_code in (400, 415)


def test_filename_and_limit(monkeypatch):
    assert validate_upload(b"hello", "../../safe.txt", "text/plain") == ("safe.txt", "text/plain")
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(HTTPException) as error:
        validate_upload(b"hello", "a.txt", "text/plain")
    assert error.value.status_code == 413


def test_archive_expansion_limit(monkeypatch):
    value = io.BytesIO()
    with zipfile.ZipFile(value, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "x")
        archive.writestr("word/document.xml", "x" * 10000)
    monkeypatch.setattr(settings, "MAX_DOCX_EXPANDED_BYTES", 100)
    with pytest.raises(HTTPException) as error:
        validate_upload(value.getvalue(), "a.docx", None)
    assert error.value.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True])
async def test_body_limit_before_parser(monkeypatch, declared):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 4)
    monkeypatch.setattr(settings, "MAX_MULTIPART_OVERHEAD_BYTES", 2)
    app = MagicMock()
    middleware = UploadLimitMiddleware(app)
    messages = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"abcd", "more_body": True},
            {"type": "http.request", "body": b"efg", "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "headers": [(b"content-length", b"7")] if declared else []}
    await middleware(scope, receive, send)
    assert messages[0]["status"] == 413
    app.assert_not_called()


@pytest.fixture
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "OIDC_ISSUER", "https://id.example")
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", "rag")
    monkeypatch.setattr(settings, "OIDC_JWKS_URL", "https://id.example/jwks")
    monkeypatch.setattr(
        security,
        "jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        ),
    )
    claims = {
        "iss": "https://id.example",
        "aud": "rag",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "sub": "subject",
        "tenant_id": "acme",
    }
    return key, claims


def test_signed_jwt_identity(signing):
    key, claims = signing
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})
    assert security.verify_bearer(token) == "acme"
    assert asyncio.run(security.get_tenant_id(None, "Bearer " + token)) == "acme"


@pytest.mark.parametrize(
    "change",
    [
        {"exp": 1},
        {"aud": "other"},
        {"iss": "https://other"},
        {"tenant_id": "../escape"},
        {"tenant_id": ["acme"]},
        {"sub": None},
    ],
)
def test_jwt_claims_rejected(signing, change):
    key, claims = signing
    with pytest.raises(HTTPException) as error:
        security.verify_bearer(jwt.encode({**claims, **change}, key, algorithm="RS256"))
    assert error.value.status_code == 401


def test_jwt_bad_signature_missing_exp_and_algorithm(signing):
    key, claims = signing
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    missing = {k: v for k, v in claims.items() if k != "exp"}
    tokens = [
        jwt.encode(claims, other_key, algorithm="RS256"),
        jwt.encode(missing, key, algorithm="RS256"),
        jwt.encode(claims, "a" * 32, algorithm="HS256"),
    ]
    for token in tokens:
        with pytest.raises(HTTPException) as error:
            security.verify_bearer(token)
        assert error.value.status_code == 401


def test_snapshot_corruption_rejected(tmp_path):
    from api.backup import digest, validate_snapshot

    (tmp_path / "postgres.dump").write_bytes(b"dump")
    (tmp_path / "objects").mkdir()
    item = tmp_path / "objects/1"
    item.write_bytes(b"original")
    manifest = {
        "version": 1,
        "database_sha256": digest(tmp_path / "postgres.dump"),
        "objects": [{"path": "objects/1", "size": 8, "sha256": digest(item)}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    validate_snapshot(tmp_path)
    item.write_bytes(b"corrupt!")
    with pytest.raises(ValueError):
        validate_snapshot(tmp_path)


def test_shared_schema():
    from api.models import Chunk as ApiChunk
    from api.models import Document as ApiDocument
    from worker.models import Chunk as WorkerChunk
    from worker.models import Document as WorkerDocument

    assert ApiDocument is WorkerDocument and ApiChunk is WorkerChunk


@pytest.mark.parametrize("keys", ["{}", "[]", '{"a":"same","b":"same"}', '{"a":""}'])
def test_startup_rejects_missing_or_ambiguous_credentials(monkeypatch, keys):
    for name in ("API_KEY", "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "TENANT_API_KEYS", keys)
    with pytest.raises(ValueError):
        security.validate_auth_configuration()


def test_default_key_cannot_identify_a_different_tenant(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "same")
    monkeypatch.setattr(settings, "TENANT_API_KEYS", '{"acme":"same"}')
    with pytest.raises(ValueError):
        security.validate_auth_configuration()


@pytest.mark.parametrize(
    "field,value",
    [
        ("OIDC_ISSUER", "http://id.example"),
        ("OIDC_AUDIENCE", "rag"),
        ("OIDC_JWKS_URL", "https://id.example/jwks"),
    ],
)
def test_partial_oidc_config_fails_closed(monkeypatch, field, value):
    for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(ValueError):
        security.validate_auth_configuration()


def test_identity_provider_outage_returns_unavailable(signing, monkeypatch):
    def unavailable(_):
        raise jwt.PyJWKClientConnectionError("unavailable")

    monkeypatch.setattr(security, "jwks_client", unavailable)
    key, claims = signing
    with pytest.raises(HTTPException) as error:
        security.verify_bearer(jwt.encode(claims, key, algorithm="RS256"))
    assert error.value.status_code == 503


def test_mixed_authentication_methods_rejected(signing):
    key, claims = signing
    token = jwt.encode(claims, key, algorithm="RS256")
    with pytest.raises(HTTPException) as error:
        asyncio.run(security.get_tenant_id("key", "Bearer " + token))
    assert error.value.status_code == 401
