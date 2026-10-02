#!/bin/bash
# Enterprise RAG - Backup Script
# Usage: ./scripts/backup.sh [backup_dir]

set -e

# Configuration
BACKUP_BASE_DIR="${1:-/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_BASE_DIR}/${TIMESTAMP}"

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
echo "Enterprise RAG Backup"
echo "Timestamp: ${TIMESTAMP}"
echo "Backup directory: ${BACKUP_DIR}"
echo "=========================================="

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# 1. PostgreSQL Backup
echo "[1/3] Backing up PostgreSQL database..."
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    --no-owner \
    --no-privileges \
    --format=custom \
    --compress=9 \
    > "${BACKUP_DIR}/postgres.dump"

if [ $? -eq 0 ]; then
    echo "  ✓ PostgreSQL backup completed"
else
    echo "  ✗ PostgreSQL backup failed"
    exit 1
fi

# 2. MinIO Backup
echo "[2/3] Backing up MinIO objects..."
# Check if mc (MinIO client) is available
if command -v mc &> /dev/null; then
    # Configure mc alias if not already configured
    mc alias set local "http://${MINIO_ENDPOINT}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" --api S3v4 2>/dev/null || true
    
    # Mirror bucket to backup directory
    mc mirror --overwrite "local/${MINIO_BUCKET}" "${BACKUP_DIR}/minio/" 2>/dev/null || {
        echo "  ⚠ MinIO backup failed (mc command error), trying alternative..."
        # Alternative: use aws cli if available
        if command -v aws &> /dev/null; then
            aws s3 sync "s3://${MINIO_BUCKET}" "${BACKUP_DIR}/minio/" \
                --endpoint-url "http://${MINIO_ENDPOINT}" \
                --no-verify-ssl 2>/dev/null || echo "  ✗ MinIO backup failed"
        else
            echo "  ✗ MinIO backup failed (no mc or aws cli available)"
        fi
    }
    echo "  ✓ MinIO backup completed"
else
    echo "  ⚠ MinIO client (mc) not installed, skipping MinIO backup"
    echo "  Install with: curl https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc && chmod +x /usr/local/bin/mc"
fi

# 3. Create manifest
echo "[3/3] Creating backup manifest..."
cat > "${BACKUP_DIR}/manifest.json" <<EOF
{
    "timestamp": "${TIMESTAMP}",
    "version": "1.0",
    "components": {
        "postgres": {
            "file": "postgres.dump",
            "host": "${POSTGRES_HOST}",
            "database": "${POSTGRES_DB}"
        },
        "minio": {
            "directory": "minio/",
            "bucket": "${MINIO_BUCKET}",
            "endpoint": "${MINIO_ENDPOINT}"
        }
    },
    "environment": {
        "embedding_model": "${EMBEDDING_MODEL_NAME:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}",
        "embedding_dimension": "${EMBEDDING_DIMENSION:-384}"
    }
}
EOF

# Compress backup
echo "Compressing backup..."
tar -czf "${BACKUP_DIR}.tar.gz" -C "${BACKUP_BASE_DIR}" "${TIMESTAMP}"
rm -rf "${BACKUP_DIR}"

# Cleanup old backups (keep last 30 days)
echo "Cleaning up old backups (older than 30 days)..."
find "${BACKUP_BASE_DIR}" -name "*.tar.gz" -mtime +30 -delete 2>/dev/null || true

echo "=========================================="
echo "Backup completed successfully!"
echo "File: ${BACKUP_DIR}.tar.gz"
echo "Size: $(du -h "${BACKUP_DIR}.tar.gz" | cut -f1)"
echo "=========================================="