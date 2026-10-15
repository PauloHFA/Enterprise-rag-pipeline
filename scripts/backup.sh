#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root with explicit environment settings.
exec python -m api.backup backup "$@"
