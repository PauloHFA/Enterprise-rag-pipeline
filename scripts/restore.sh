#!/bin/bash
# Enterprise RAG - Restore Script
# Usage: ./scripts/restore.sh <backup_file.tar.gz>

set -e

BACKUP_FILE="$1"

if [ -z "${BACKUP_FILE}" ]; then
    echo "Usage: $0 <backup_file.tar.gz>"
    echo "Example: $0 /backups/20260929_120000.tar.gz"
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "Error: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_USER=${POSTGRES_USER:-raguser}
POSTGRES_DB=${POSTGRES_DB:-ragdb}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-ragpass}

MINIO_ENDPOINT=${MINIO_ENDPOINT:-localhost:9000}
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-minioadmin}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-minioadmin}
MINIO_BUCKET=${MINIO_BUCKET:-documents}

echo "=========================================="
echo "Enterprise RAG Restore"
echo "Backup file: ${BACKUP_FILE}"
echo "=========================================="

# Create temporary directory
TEMP_DIR=$(mktemp -d)
trap "rm -rf ${TEMP_DIR}" EXIT

# Extract backup
echo "Extracting backup..."
tar -xzf "${BACKUP_FILE}" -C "${TEMP_DIR}"

# Find the extracted directory (should be timestamp-named)
EXTRACTED_DIR=$(find "${TEMP_DIR}" -maxdepth 1 -type d -name "20*" | head -1)

if [ -z "${EXTRACTED_DIR}" ]; then
    echo "Error: Could not find extracted backup directory"
    exit 1
fi

echo "Extracted to: ${EXTRACTED_DIR}"

# Verify manifest
if [ -f "${EXTRACTED_DIR}/manifest.json" ]; then
    echo "Backup manifest found:"
    cat "${EXTRACTED_DIR}/manifest.json" | jq . 2>/dev/null || cat "${EXTRACTED_DIR}/manifest.json"
else
    echo "Warning: No manifest.json found in backup"
fi

# 1. Restore PostgreSQL
echo "[1/3] Restoring PostgreSQL database..."
if [ -f "${EXTRACTED_DIR}/postgres.dump" ]; then
    echo "  Dropping existing connections..."
    PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" \
        -U "${POSTGRES_USER}" \
        -d postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" 2>/dev/null || true
    
    echo "  Restoring database..."
    PGPASSWORD="${POSTGRES_PASSWORD}" pg_restore \
        -h "${POSTGRES_HOST}" \
        -U "${POSTGRES_USER}" \
        -d "${POSTGRES_DB}" \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        "${EXTRACTED_DIR}/postgres.dump"
    
    if [ $? -eq 0 ]; then
        echo "  ✓ PostgreSQL restore completed"
    else
        echo "  ✗ PostgreSQL restore failed"
        exit 1
    fi
else
    echo "  ⚠ No postgres.dump found, skipping database restore"
fi

# 2. Restore MinIO
echo "[2/3] Restoring MinIO objects..."
if [ -d "${EXTRACTED_DIR}/minio" ]; then
    if command -v mc &> /dev/null; then
        mc alias set local "http://${MINIO_ENDPOINT}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --api S3v4 2>/dev/null || true
        
        # Remove existing objects (optional - comment out to keep existing)
        # mc rm --recursive --force "local/${MINIO_BUCKET}" 2>/dev/null || true
        
        mc mirror --overwrite "${EXTRACTED_DIR}/minio/" "local/${MINIO_BUCKET}" 2>/dev/null || {
            echo "  ⚠ mc mirror failed, trying aws cli..."
            if command -v aws &> /dev/null; then
                aws s3 sync "${EXTRACTED_DIR}/minio/" "s3://${MINIO_BUCKET}" \
                    --endpoint-url "http://${MINIO_ENDPOINT}" \
                    --no-verify-ssl 2>/dev/null || echo "  ✗ MinIO restore failed"
            else
                echo "  ✗ MinIO restore failed (no mc or aws cli available)"
            fi
        }
        echo "  ✓ MinIO restore completed"
    else
        echo "  ⚠ MinIO client (mc) not installed, skipping MinIO restore"
        echo "  Install with: curl https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc && chmod +x /usr/local/bin/mc"
    fi
else
    echo "  ⚠ No minio directory found, skipping MinIO restore"
fi

# 3. Verify restore
echo "[3/3] Verifying restore..."
if [ -f "${EXTRACTED_DIR}/postgres.dump" ]; then
    TABLE_COUNT=$(PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" \
        -U "${POSTGRES_USER}" \
        -d "${POSTGRES_DB}" \
        -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | xargs)
    echo "  Tables restored: ${TABLE_COUNT}"
fi

if [ -d "${EXTRACTED_DIR}/minio" ]; then
    OBJECT_COUNT=$(find "${EXTRACTED_DIR}/minio" -type f | wc -l)
    echo "  MinIO objects restored: ${OBJECT_COUNT}"
fi

echo "=========================================="
echo "Restore completed successfully!"
echo "=========================================="