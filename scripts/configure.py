"""Create private production credentials or report missing configuration without exposing values."""

import argparse
import json
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values

REQUIRED = (
    "POSTGRES_PASSWORD",
    "RABBITMQ_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "API_KEY",
    "GRAFANA_PASSWORD",
)
DEFAULTS = {
    "EMBEDDING_MODEL_REVISION": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
    "TENANT_API_KEYS": "{}",
    "OIDC_ISSUER": "",
    "OIDC_AUDIENCE": "",
    "OIDC_JWKS_URL": "",
    "OIDC_TENANT_CLAIM": "tenant_id",
    "RETENTION_DAYS": "0",
    "OUTBOX_RETENTION_DAYS": "7",
    "MAX_UPLOAD_BYTES": "20971520",
    "OCR_ENABLED": "false",
    "OCR_LANGUAGES": "por+eng",
    "CHUNK_TARGET_TOKENS": "96",
    "CHUNK_OVERLAP_TOKENS": "12",
    "EMBEDDING_BATCH_SIZE": "32",
    "RATE_LIMIT_PER_MINUTE": "60",
    "EXTRACTION_TIMEOUT_SECONDS": "120",
}


def initialized_file(path):
    target = Path(path)
    # Exclusive create preserves existing passwords and prevents accidental rotation.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("# Private production configuration. Never commit this file.\n")
        for name in REQUIRED:
            output.write(f"{name}={secrets.token_urlsafe(32)}\n")
        for name, value in DEFAULTS.items():
            output.write(f"{name}={value}\n")
    return target


def configuration_report(path, certificate_dir="certs"):
    values = dotenv_values(path)
    statuses = {}
    for key in REQUIRED:
        value = values.get(key) or ""
        placeholder = not value or any(
            word in value.upper() for word in ("CHANGE_ME", "YOUR-", "YOUR_", "CHANGE-IN")
        )
        statuses[key] = {"configured": not placeholder, "length": len(value)}
    try:
        tenants = json.loads(values.get("TENANT_API_KEYS") or "{}")
        if not isinstance(tenants, dict):
            raise ValueError("Expected tenant-to-key object")
        tenant_ids = list(tenants)
    except (ValueError, TypeError):
        tenant_ids = []
        statuses["TENANT_API_KEYS"] = {"configured": False}
    oidc = [bool(values.get(key)) for key in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL")]
    oidc_status = (
        "optional_not_configured"
        if not any(oidc)
        else ("configured" if all(oidc) else "incomplete")
    )
    certificate_dir = Path(certificate_dir)
    certificates = {
        name: (certificate_dir / name).is_file() for name in ("fullchain.pem", "privkey.pem")
    }
    # Domain/DNS, public CA trust and deployment readiness require environment-specific checks.
    ready = (
        all(item["configured"] for item in statuses.values())
        and all(certificates.values())
        and oidc_status != "incomplete"
    )
    return {
        "file": str(path),
        "credentials": statuses,
        "tenants": tenant_ids,
        "oidc": oidc_status,
        "certificate_files": certificates,
        "files_present_for_deployment": ready,
        "manual_checks": [
            "DNS/domain matches certificate SAN",
            "TLS trust, expiry and key match",
            "Persistent storage and firewall",
            "Backup/retention schedules and restore drill",
            "Optional OIDC provider claims and trusted HTTPS JWKS",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "check"])
    parser.add_argument("--file", default=".env.production")
    parser.add_argument("--certificates", default="certs")
    args = parser.parse_args()
    if args.action == "init":
        initialized_file(args.file)
        print(
            f"Created {args.file}; existing files are never overwritten. Values remain in the private file."
        )
    report = configuration_report(args.file, args.certificates)
    print(json.dumps(report, indent=2))
    if args.action == "check" and not report["files_present_for_deployment"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
