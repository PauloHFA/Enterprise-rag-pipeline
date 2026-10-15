"""Destructive exercises restricted to explicitly named disposable test resources."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psycopg2
import pytest
from psycopg2 import sql

PROJECT = os.getenv("RAG_TEST_COMPOSE_PROJECT")
URL = os.getenv("RAG_E2E_URL")
KEY = os.getenv("RAG_E2E_API_KEY")
DB = os.getenv("RAG_E2E_DATABASE_URL")
TENANT_KEY = os.getenv("RAG_E2E_TENANT_KEY")
PROMETHEUS = os.getenv("RAG_MONITORING_URL")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not all((PROJECT, URL, KEY, DB)), reason="Disposable test stack required"
)


def compose(*args, check=True):
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
        timeout=180,
    )


def run_tool(*args, environment=None, volume=None, check=True):
    command = ["run", "--rm", "--no-deps"]
    if volume:
        command += ["--user", "0:0", "-v", volume + ":/backups"]
    for key, value in (environment or {}).items():
        command += ["-e", key + "=" + value]
    return compose(*command, "api", "python", *args, check=check)


def wait(predicate, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(1)
    pytest.fail("Operation did not reach expected state")


def query(statement, params=(), database=None):
    with (
        psycopg2.connect(DB, **({"dbname": database} if database else {})) as db,
        db.cursor() as cursor,
    ):
        cursor.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def create_database(name):
    db = psycopg2.connect(DB)
    try:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    finally:
        db.close()


def drop_database(name):
    db = psycopg2.connect(DB)
    try:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    finally:
        db.close()


def container_db(name):
    return "postgresql://raguser:ragpass@postgres:5432/" + name


def upload(client, marker):
    response = client.post(
        "/v1/documents", files={"file": (marker + ".txt", marker.encode(), "text/plain")}
    )
    assert response.status_code == 202, response.text
    document = response.json()
    document["_traceparent"] = response.headers["traceparent"]
    wait(lambda: client.get("/v1/documents/" + document["id"]).json().get("status") == "indexed")
    return document


def test_tenants_upload_validation_metrics_and_trace():
    if not TENANT_KEY:
        pytest.skip("Set a second configured tenant API key")
    with (
        httpx.Client(base_url=URL, headers={"X-API-Key": KEY}, timeout=180) as owner,
        httpx.Client(base_url=URL, headers={"X-API-Key": TENANT_KEY}, timeout=180) as other,
    ):
        from prometheus_client.parser import text_string_to_metric_families

        def upload_count():
            return sum(
                sample.value
                for family in text_string_to_metric_families(owner.get("/metrics").text)
                for sample in family.samples
                if sample.name == "enterprise_rag_documents_uploaded_total"
            )

        before = upload_count()
        marker = "isolation" + uuid.uuid4().hex
        document = upload(owner, marker)
        foreign = None
        try:
            foreign = upload(other, marker)
            assert foreign["id"] != document["id"] and foreign["tenant_id"] == "acme"
            for method, suffix in [("get", ""), ("delete", ""), ("post", "/reindex")]:
                assert (
                    getattr(other, method)("/v1/documents/" + document["id"] + suffix).status_code
                    == 404
                )
            for mode in ("lexical", "vector", "hybrid"):
                hits = other.post("/v1/search", json={"query": marker, "mode": mode}).json()[
                    "results"
                ]
                assert all(hit["document_id"] != document["id"] for hit in hits)
                assert any(hit["document_id"] == foreign["id"] for hit in hits)
            for name, content, mime, expected in [
                ("fake.pdf", b"text", "application/pdf", 415),
                ("binary.txt", b"\x00binary", "text/plain", 415),
                ("huge.txt", b"x" * (20 * 1024 * 1024 + 1), "text/plain", 413),
            ]:
                assert (
                    owner.post("/v1/documents", files={"file": (name, content, mime)}).status_code
                    == expected
                )
            trace = query(
                "SELECT payload->>'traceparent' FROM outbox_events WHERE document_id=%s",
                (document["id"],),
            )[0][0]
            assert (
                len(trace) == 55 and trace.split("-")[1] == document["_traceparent"].split("-")[1]
            )
            assert (
                trace.split("-")[1]
                in compose("logs", "--no-color", "--tail", "300", "worker").stdout
            )
            assert upload_count() == before + 2
            metrics = owner.get("/metrics").text
            assert 'enterprise_rag_documents_uploaded_total{status="accepted"}' in metrics
            assert "enterprise_rag_search_requests_total" in metrics
        finally:
            owner.delete("/v1/documents/" + document["id"])
            if foreign:
                other.delete("/v1/documents/" + foreign["id"])


def test_migrations_fresh_downgrade_and_legacy_adoption():
    name = "migration_" + uuid.uuid4().hex
    create_database(name)
    env = {"DATABASE_URL": container_db(name)}
    try:
        run_tool("-m", "alembic", "upgrade", "head", environment=env)
        assert query("SELECT version_num FROM alembic_version", database=name) == [("0002",)]
        run_tool("-m", "alembic", "check", environment=env)
        run_tool("-m", "alembic", "downgrade", "0001", environment=env)
        doc_id = str(uuid.uuid4())
        query(
            "INSERT INTO documents(id,tenant_id,filename,mime_type,sha256,storage_key,status,metadata) VALUES (%s,'default','legacy.txt','text/plain',%s,'legacy','completed','{}')",
            (doc_id, "a" * 64),
            name,
        )
        query("DROP TABLE alembic_version", database=name)
        run_tool("-m", "alembic", "upgrade", "head", environment=env)
        assert query(
            "SELECT status,generation,indexed_generation FROM documents WHERE id=%s",
            (doc_id,),
            name,
        ) == [("indexed", 1, 1)]
    finally:
        drop_database(name)


def test_nonroot_and_readonly_application():
    for service in ("api", "worker", "outbox"):
        assert compose("exec", "-T", service, "id", "-u").stdout.strip() == "10001"
        assert (
            compose("exec", "-T", service, "touch", "/app/forbidden", check=False).returncode != 0
        )


def test_tls_proxy_and_internal_metrics(tmp_path):
    import datetime
    import socket
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "fullchain.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "privkey.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    name = PROJECT + "-tls-" + uuid.uuid4().hex[:8]
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                PROJECT + "_default",
                "-p",
                f"127.0.0.1:{port}:443",
                "-v",
                str(ROOT / "nginx/nginx.conf") + ":/etc/nginx/nginx.conf:ro",
                "-v",
                str(tmp_path) + ":/etc/nginx/certs:ro",
                "nginx:1.28-alpine",
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        context = ssl.create_default_context(cafile=str(tmp_path / "fullchain.pem"))
        with httpx.Client(
            base_url=f"https://localhost:{port}", verify=context, timeout=10
        ) as client:
            wait(lambda: client.get("/healthz").status_code == 200, 60)
            assert client.get("/metrics").status_code == 403
            assert client.get("/v1/documents/" + str(uuid.uuid4())).status_code == 401
            assert (
                client.get(
                    "/v1/documents/" + str(uuid.uuid4()), headers={"X-API-Key": KEY}
                ).status_code
                == 404
            )
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        (tmp_path / "privkey.pem").unlink(missing_ok=True)
        (tmp_path / "fullchain.pem").unlink(missing_ok=True)


def test_worker_alert_fires_and_recovers():
    if not PROMETHEUS:
        pytest.skip("Set RAG_MONITORING_URL")

    def alerts():
        return httpx.get(PROMETHEUS + "/api/v1/alerts", timeout=5).json()["data"]["alerts"]

    wait(
        lambda: httpx.get(
            PROMETHEUS + "/api/v1/query",
            params={"query": "enterprise_rag_worker_active"},
            timeout=5,
        ).json()["data"]["result"]
    )
    compose("stop", "worker")
    try:
        wait(
            lambda: any(
                item["labels"]["alertname"] == "RagWorkerUnavailable" and item["state"] == "firing"
                for item in alerts()
            ),
            120,
        )
    finally:
        compose("start", "worker")
    wait(
        lambda: not any(item["labels"]["alertname"] == "RagWorkerUnavailable" for item in alerts()),
        120,
    )


def test_backup_restore_and_fail_closed():
    import socket

    name = "restore_" + uuid.uuid4().hex
    bucket = "restore-" + uuid.uuid4().hex
    volume = PROJECT + "-snapshot-" + uuid.uuid4().hex
    create_database(name)
    document = None
    pending = None
    vhost = "restore-" + uuid.uuid4().hex
    temporary = []
    try:
        with httpx.Client(base_url=URL, headers={"X-API-Key": KEY}, timeout=180) as client:
            document = upload(client, "restore" + uuid.uuid4().hex)
            compose("stop", "worker")
            marker = "pending" + uuid.uuid4().hex
            response = client.post(
                "/v1/documents", files={"file": (marker + ".txt", marker.encode(), "text/plain")}
            )
            assert response.status_code == 202
            pending = response.json()
            wait(
                lambda: (
                    query(
                        "SELECT published_at IS NOT NULL FROM outbox_events WHERE document_id=%s",
                        (pending["id"],),
                    )
                    == [(True,)]
                )
            )
        compose("stop", "api", "worker", "outbox")
        # A missing bucket must fail, and cannot produce a successful manifest.
        failed = run_tool(
            "-m",
            "api.backup",
            "backup",
            "/backups/incomplete",
            "--quiesced",
            volume=volume,
            environment={"MINIO_BUCKET": "missing-" + uuid.uuid4().hex},
            check=False,
        )
        assert failed.returncode != 0
        assert (
            run_tool(
                "-m", "api.backup", "verify", "/backups/incomplete", volume=volume, check=False
            ).returncode
            != 0
        )
        result = run_tool(
            "-m", "api.backup", "backup", "/backups/snapshot", "--quiesced", volume=volume
        )
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["objects"] >= 1 and report["counts"]["documents"] >= 1
        target = {"DATABASE_URL": container_db(name), "MINIO_BUCKET": bucket}
        restored = run_tool(
            "-m",
            "api.backup",
            "restore",
            "/backups/snapshot",
            "--isolated",
            volume=volume,
            environment=target,
        )
        assert json.loads(restored.stdout.strip().splitlines()[-1]) == report
        assert query("SELECT status FROM documents WHERE id=%s", (document["id"],), name) == [
            ("indexed",)
        ]
        assert (
            query(
                "SELECT count(*) FROM chunks WHERE document_id=%s AND embedding IS NOT NULL",
                (document["id"],),
                name,
            )[0][0]
            > 0
        )
        assert query(
            "SELECT published_at IS NULL FROM outbox_events WHERE document_id=%s",
            (pending["id"],),
            name,
        ) == [(True,)]
        assert (
            run_tool(
                "-m",
                "api.backup",
                "restore",
                "/backups/snapshot",
                "--isolated",
                volume=volume,
                environment=target,
                check=False,
            ).returncode
            != 0
        )
        # Exercise restored searches and unfinished jobs using a separate broker vhost.
        compose("exec", "-T", "rabbitmq", "rabbitmqctl", "add_vhost", vhost)
        compose(
            "exec",
            "-T",
            "rabbitmq",
            "rabbitmqctl",
            "set_permissions",
            "-p",
            vhost,
            "raguser",
            ".*",
            ".*",
            ".*",
        )
        target["RABBITMQ_URL"] = "amqp://raguser:ragpass@rabbitmq:5672/" + vhost
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        for service in ("api", "worker", "outbox"):
            container = PROJECT + "-restored-" + service
            temporary.append(container)
            command = ["run", "--rm", "--no-deps", "-d", "--name", container]
            if service == "api":
                command += ["-p", f"127.0.0.1:{port}:8000"]
            for key, value in target.items():
                command += ["-e", key + "=" + value]
            compose(*command, service)
        with httpx.Client(
            base_url=f"http://localhost:{port}", headers={"X-API-Key": KEY}, timeout=180
        ) as restored_api:
            wait(lambda: restored_api.get("/readyz").status_code == 200)
            wait(
                lambda: (
                    restored_api.get("/v1/documents/" + pending["id"]).json().get("status")
                    == "indexed"
                )
            )
            for mode in ("lexical", "vector", "hybrid"):
                hits = restored_api.post(
                    "/v1/search",
                    json={"query": document["filename"].removesuffix(".txt"), "mode": mode},
                ).json()["results"]
                assert any(hit["document_id"] == document["id"] for hit in hits)
        Path(".test-results").mkdir(exist_ok=True)
        Path(".test-results/backup-restore.json").write_text(json.dumps(report, indent=2))
    finally:
        for container in temporary:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        compose("exec", "-T", "rabbitmq", "rabbitmqctl", "delete_vhost", vhost, check=False)
        compose("start", "api", "worker", "outbox")
        wait(lambda: httpx.get(URL + "/readyz", timeout=5).status_code == 200)
        if document:
            httpx.delete(
                URL + "/v1/documents/" + document["id"], headers={"X-API-Key": KEY}, timeout=60
            )
        if pending:
            httpx.delete(
                URL + "/v1/documents/" + pending["id"], headers={"X-API-Key": KEY}, timeout=60
            )
        from minio import Minio

        storage = Minio(
            os.getenv("RAG_E2E_MINIO_ENDPOINT", "localhost:9000"),
            access_key="minioadmin",
            secret_key="minioadmin",
            secure=False,
        )
        if storage.bucket_exists(bucket):
            for item in storage.list_objects(bucket, recursive=True):
                storage.remove_object(bucket, item.object_name)
            storage.remove_bucket(bucket)
        drop_database(name)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=False)


def test_retention_deletes_only_expired_documents():
    documents = []
    with httpx.Client(base_url=URL, headers={"X-API-Key": KEY}, timeout=180) as client:
        try:
            documents = [upload(client, "retention" + uuid.uuid4().hex) for _ in range(2)]
            query(
                "UPDATE documents SET created_at=now()-interval '40 days' WHERE id=%s",
                (documents[0]["id"],),
            )
            run_tool("-m", "api.retention", environment={"RETENTION_DAYS": "30"})
            assert client.get("/v1/documents/" + documents[0]["id"]).status_code == 404
            assert client.get("/v1/documents/" + documents[1]["id"]).status_code == 200
            assert (
                query(
                    "SELECT count(*) FROM outbox_events WHERE routing_key='storage.delete' AND payload->>'storage_key'=%s",
                    (documents[0]["storage_key"],),
                )[0][0]
                == 1
            )
        finally:
            for document in documents:
                client.delete("/v1/documents/" + document["id"])
