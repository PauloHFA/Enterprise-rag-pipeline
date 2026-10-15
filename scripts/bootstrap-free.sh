#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root. This script changes only this Compose project.
RAG_ENV_FILE="${1:-.env.production}"
RAG_PROJECT="${RAG_COMPOSE_PROJECT:-enterprise-rag-free}"
for tool in docker openssl sha256sum; do command -v "$tool" >/dev/null; done
docker compose version >/dev/null
test -f "$RAG_ENV_FILE"
if grep -q 'CHANGE_ME' "$RAG_ENV_FILE"; then
    echo 'Replace all CHANGE_ME values before deployment.' >&2
    exit 2
fi
test -s certs/fullchain.pem
test -s certs/privkey.pem
openssl x509 -in certs/fullchain.pem -checkend 86400 -noout
openssl verify -untrusted certs/fullchain.pem certs/fullchain.pem
RAG_CERT_KEY=$(openssl x509 -in certs/fullchain.pem -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
RAG_PRIVATE_KEY=$(openssl pkey -in certs/privkey.pem -pubout -outform DER | sha256sum)
test "$RAG_CERT_KEY" = "$RAG_PRIVATE_KEY"
docker compose -p "$RAG_PROJECT" --env-file "$RAG_ENV_FILE" -f docker-compose.free.yml config --quiet
docker compose -p "$RAG_PROJECT" --env-file "$RAG_ENV_FILE" -f docker-compose.free.yml up -d --build --wait --wait-timeout 300
