#!/usr/bin/env bash
set -euo pipefail
RAG_BASE_URL="${1:?Usage: test-deploy.sh https://your-domain}"
: "${API_KEY:?Set API_KEY}"
[[ "$RAG_BASE_URL" == https://* ]]
for endpoint in healthz readyz; do
    curl --fail --silent --show-error "$RAG_BASE_URL/$endpoint" >/dev/null
done
RAG_UNKNOWN_ID=00000000-0000-0000-0000-000000000000
RAG_AUTH_STATUS=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$RAG_BASE_URL/v1/documents/$RAG_UNKNOWN_ID")
test "$RAG_AUTH_STATUS" = 401
RAG_TENANT_STATUS=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' -H "X-API-Key: $API_KEY" "$RAG_BASE_URL/v1/documents/$RAG_UNKNOWN_ID")
test "$RAG_TENANT_STATUS" = 404
RAG_METRICS_STATUS=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$RAG_BASE_URL/metrics")
test "$RAG_METRICS_STATUS" = 403
echo 'HTTPS, readiness, authentication and metrics isolation verified.'
