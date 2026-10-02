# Scripts

This directory contains operational scripts for the Enterprise RAG pipeline.

## Backup & Restore

### `backup.sh`
Creates a complete backup of the PostgreSQL database and MinIO objects.

**Usage:**
```bash
# Default backup directory (/backups)
./scripts/backup.sh

# Custom backup directory
./scripts/backup.sh /mnt/backups
```

**What it backs up:**
- PostgreSQL database (custom format, compressed)
- MinIO objects (using `mc mirror`)
- Manifest file with metadata

**Requirements:**
- `pg_dump` / `pg_restore` (PostgreSQL client tools)
- `mc` (MinIO Client) - optional but recommended
- `aws` CLI - alternative for MinIO backup

**Install MinIO Client:**
```bash
# Linux
curl https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc
chmod +x /usr/local/bin/mc

# macOS
brew install minio/stable/mc
```

### `restore.sh`
Restores from a backup created by `backup.sh`.

**Usage:**
```bash
./scripts/restore.sh /backups/20260929_120000.tar.gz
```

**What it restores:**
- PostgreSQL database (drops existing connections, clean restore)
- MinIO objects (mirrors to bucket)

**Safety features:**
- Creates temporary directory (cleaned up on exit)
- Verifies manifest before restore
- Shows table/object counts after restore

## Environment Variables

Both scripts use the following environment variables (from `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | localhost | PostgreSQL host |
| `POSTGRES_USER` | raguser | PostgreSQL user |
| `POSTGRES_DB` | ragdb | PostgreSQL database |
| `POSTGRES_PASSWORD` | ragpass | PostgreSQL password |
| `MINIO_ENDPOINT` | localhost:9000 | MinIO endpoint |
| `MINIO_ACCESS_KEY` | minioadmin | MinIO access key |
| `MINIO_SECRET_KEY` | minioadmin | MinIO secret key |
| `MINIO_BUCKET` | documents | MinIO bucket name |

## Scheduling Backups

### Cron (Linux/macOS)
```bash
# Daily at 2 AM
0 2 * * * /path/to/enterprise-rag/scripts/backup.sh /mnt/backups >> /var/log/rag-backup.log 2>&1
```

### systemd Timer
Create `/etc/systemd/system/rag-backup.service`:
```ini
[Unit]
Description=Enterprise RAG Backup
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/enterprise-rag
ExecStart=/path/to/enterprise-rag/scripts/backup.sh /mnt/backups
EnvironmentFile=/path/to/enterprise-rag/.env
```

Create `/etc/systemd/system/rag-backup.timer`:
```ini
[Unit]
Description=Daily Enterprise RAG Backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable --now rag-backup.timer
```

## Testing Restores

**Important:** Regularly test your restores!

```bash
# 1. Create a test database
createdb -h localhost -U raguser ragdb_test

# 2. Restore to test database
POSTGRES_DB=ragdb_test ./scripts/restore.sh /backups/latest.tar.gz

# 3. Verify data
psql -h localhost -U raguser -d ragdb_test -c "SELECT count(*) FROM documents;"
```

## Disaster Recovery Checklist

- [ ] Backups run automatically (cron/systemd)
- [ ] Backups stored off-site (S3, Azure Blob, GCS)
- [ ] Restore tested monthly
- [ ] Backup retention policy configured (30 days default)
- [ ] Monitoring alerts on backup failures
- [ ] Documentation accessible to team
- [ ] RTO/RPO defined and documented