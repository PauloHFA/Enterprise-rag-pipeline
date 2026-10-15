"""Fault injection against an explicitly named disposable Compose project only."""

import io
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pika
import psycopg2
import pytest
from minio.error import S3Error

from minio import Minio

PROJECT = os.getenv("RAG_TEST_COMPOSE_PROJECT")
URL = os.getenv("RAG_E2E_URL")
KEY = os.getenv("RAG_E2E_API_KEY")
DB_URL = os.getenv("RAG_E2E_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not all((PROJECT, URL, KEY, DB_URL)),
    reason="Explicit disposable Compose project and E2E settings required",
)


def compose(action, *services):
    subprocess.run(
        ["docker", "compose", "-p", PROJECT, action, *services],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=60,
        capture_output=True,
    )


def sql(query, params=()):
    with psycopg2.connect(DB_URL) as db, db.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall() if cursor.description else []


def wait_until(predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    pytest.fail("Condition not reached before timeout")


def completed(client, doc_id):
    return sql("SELECT status FROM documents WHERE id = %s", (doc_id,)) == [("indexed",)]


@pytest.fixture
def client():
    with httpx.Client(base_url=URL, headers={"X-API-Key": KEY}, timeout=120) as client:
        yield client


def upload(client, marker, content=None):
    response = client.post(
        "/v1/documents", files={"file": (marker + ".txt", content or marker.encode(), "text/plain")}
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_broker_outage_and_api_restart_recover_accepted_upload(client):
    doc = None
    compose("stop", "rabbitmq")
    try:
        doc = upload(client, "recovery" + uuid.uuid4().hex)
        assert client.get("/readyz").status_code == 200
        assert client.get(f"/v1/documents/{doc['id']}").json()["status"] == "queued"
        assert sql(
            "SELECT published_at FROM outbox_events WHERE document_id = %s", (doc["id"],)
        ) == [(None,)]
        assert (
            client.post("/v1/search", json={"query": "recovery", "mode": "lexical"}).status_code
            == 200
        )
        compose("restart", "api")

        # Wait on a fresh client while the API restarts.
        def ready():
            try:
                return httpx.get(URL + "/readyz", timeout=3).status_code == 200
            except httpx.HTTPError:
                return False

        wait_until(ready)
        assert client.get(f"/v1/documents/{doc['id']}").status_code == 200
    finally:
        compose("start", "rabbitmq")
    try:
        wait_until(lambda: completed(client, doc["id"]))
        wait_until(
            lambda: sql(
                "SELECT published_at IS NOT NULL FROM outbox_events WHERE document_id = %s",
                (doc["id"],),
            )[0][0]
        )
    finally:
        if doc:
            client.delete(f"/v1/documents/{doc['id']}")


def test_concurrent_uploads_and_reindex_generations(client):
    # Exercise competing workers and publishers against the same PostgreSQL locks.
    for service in ("worker", "outbox"):
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                PROJECT,
                "up",
                "-d",
                "--no-deps",
                "--no-recreate",
                "--scale",
                f"{service}=2",
                service,
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            timeout=60,
            capture_output=True,
        )
    marker = "concurrent" + uuid.uuid4().hex
    documents = []
    try:

        def submit(_):
            with httpx.Client(base_url=URL, headers={"X-API-Key": KEY}, timeout=120) as parallel:
                return upload(parallel, marker)

        with ThreadPoolExecutor(max_workers=4) as pool:
            documents = list(pool.map(submit, range(4)))
        doc_id = documents[0]["id"]
        assert {doc["id"] for doc in documents} == {doc_id}
        assert sql("SELECT count(*) FROM outbox_events WHERE document_id = %s", (doc_id,)) == [(1,)]
        wait_until(lambda: completed(client, doc_id))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: httpx.post(
                        URL + f"/v1/documents/{doc_id}/reindex",
                        headers={"X-API-Key": KEY},
                        timeout=120,
                    ),
                    range(2),
                )
            )
        assert all(response.status_code == 202 for response in results)
        wait_until(
            lambda: sql(
                "SELECT generation = 3 AND indexed_generation = 3 FROM documents WHERE id = %s",
                (doc_id,),
            )[0][0]
        )
        before = sql("SELECT id FROM chunks WHERE document_id = %s ORDER BY id", (doc_id,))
        # Simulates a confirmed publication whose database marker was lost on process crash.
        sql(
            "UPDATE outbox_events SET published_at = NULL, next_attempt_at = now() WHERE document_id = %s",
            (doc_id,),
        )
        wait_until(
            lambda: (
                sql(
                    "SELECT count(*) FROM outbox_events WHERE document_id = %s AND published_at IS NULL",
                    (doc_id,),
                )
                == [(0,)]
            )
        )
        # FIFO barrier: completion of a subsequent upload ensures older upload messages were consumed.
        barrier = upload(client, "barrier" + uuid.uuid4().hex)
        documents.append(barrier)
        wait_until(lambda: completed(client, barrier["id"]))
        time.sleep(1)
        assert sql("SELECT id FROM chunks WHERE document_id = %s ORDER BY id", (doc_id,)) == before
        assert sql("SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,)) == [(1,)]
    finally:
        for doc_id in {doc["id"] for doc in documents}:
            client.delete(f"/v1/documents/{doc_id}")


def test_queued_document_deleted_before_worker_returns(client):
    storage = Minio(
        "localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False
    )
    doc = None
    compose("stop", "worker")
    try:
        doc = upload(client, "deleted" + uuid.uuid4().hex)
        wait_until(
            lambda: sql(
                "SELECT published_at IS NOT NULL FROM outbox_events WHERE document_id = %s",
                (doc["id"],),
            )[0][0]
        )
        assert client.delete(f"/v1/documents/{doc['id']}").status_code == 204
        assert sql("SELECT count(*) FROM outbox_events WHERE document_id = %s", (doc["id"],)) == [
            (0,)
        ]

        def object_gone():
            try:
                storage.stat_object("documents", doc["storage_key"])
            except S3Error as exc:
                return exc.code == "NoSuchKey"
            return False

        wait_until(object_gone)
    finally:
        compose("start", "worker")
    barrier = None
    try:
        barrier = upload(client, "afterdelete" + uuid.uuid4().hex)
        wait_until(lambda: completed(client, barrier["id"]))
        assert sql("SELECT count(*) FROM chunks WHERE document_id = %s", (doc["id"],)) == [(0,)]
    finally:
        for document in (doc, barrier):
            if document:
                client.delete(f"/v1/documents/{document['id']}")


def test_poison_message_delayed_retries_dlq_and_replay(client):
    from worker.replay_dlq import replay

    storage = Minio(
        "localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False
    )
    marker = "poison" + uuid.uuid4().hex
    compose("stop", "worker")
    try:
        doc = upload(client, marker)
        storage.remove_object("documents", doc["storage_key"])
    finally:
        compose("start", "worker")
    connection = pika.BlockingConnection(
        pika.URLParameters("amqp://raguser:ragpass@localhost:5672")
    )
    try:
        channel = connection.channel()
        channel.confirm_delivery()
        received = None

        def dlq_message():
            nonlocal received
            method, properties, body = channel.basic_get("document.upload.dlq", auto_ack=False)
            if method is None:
                return False
            received = (method, properties, body)
            return True

        start = time.monotonic()
        wait_until(dlq_message)
        method, properties, body = received
        assert properties.headers["x-retry-count"] == 3
        assert time.monotonic() - start >= 25  # 1s + 5s + 30s backoff, not immediate retry.
        assert doc["id"].encode() in body
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        # Repair the original file; replay keeps event identity and resets the retry counter.
        repaired = marker.encode()  # Restore the identical original bytes and SHA-256.
        storage.put_object(
            "documents",
            doc["storage_key"],
            io.BytesIO(repaired),
            len(repaired),
            content_type="text/plain",
        )
        assert replay(channel, "document.upload.dlq", limit=1, execute=True) == 1
        wait_until(lambda: completed(client, doc["id"]))
        assert sql("SELECT count(*) FROM chunks WHERE document_id = %s", (doc["id"],)) == [(1,)]
    finally:
        connection.close()
        client.delete(f"/v1/documents/{doc['id']}")


def test_failed_outbox_insert_rolls_back_document_and_compensates_file(client):
    marker = "rollback" + uuid.uuid4().hex
    function = "test_reject_outbox_" + uuid.uuid4().hex[:8]
    trigger = function + "_trigger"
    doc = None
    storage = Minio(
        "localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False
    )
    try:
        sql(
            f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.payload->>'filename' = %s THEN RAISE EXCEPTION 'injected outbox failure'; END IF;
            RETURN NEW; END $$""",
            (marker + ".txt",),
        )
        sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON outbox_events FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        response = client.post(
            "/v1/documents", files={"file": (marker + ".txt", marker.encode(), "text/plain")}
        )
        assert response.status_code == 503, response.text
        assert sql("SELECT count(*) FROM documents WHERE filename = %s", (marker + ".txt",)) == [
            (0,)
        ]
        assert sql(
            "SELECT count(*) FROM outbox_events WHERE payload->>'filename' = %s", (marker + ".txt",)
        ) == [(0,)]
        assert not [
            obj
            for obj in storage.list_objects("documents", recursive=True)
            if obj.object_name.endswith(marker + ".txt")
        ]
    finally:
        sql(f"DROP FUNCTION IF EXISTS {function}() CASCADE")
    try:
        doc = upload(client, marker)
        wait_until(lambda: completed(client, doc["id"]))
    finally:
        if doc:
            client.delete(f"/v1/documents/{doc['id']}")


def test_worker_death_during_chunk_commit_is_recovered(client):
    function = "test_pause_chunk_" + uuid.uuid4().hex[:8]
    trigger = function + "_trigger"
    doc = None
    compose("stop", "worker")
    try:
        doc = upload(client, "workercrash" + uuid.uuid4().hex)
        sql(
            f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.document_id = %s::uuid THEN PERFORM pg_sleep(10); END IF;
            RETURN NEW; END $$""",
            (doc["id"],),
        )
        sql(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON chunks FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        compose("start", "worker")
        wait_until(
            lambda: sql(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event = 'PgSleep' AND query LIKE %s)",
                ("%INSERT INTO chunks%",),
            )[0][0]
        )
        compose("stop", "worker")
        assert sql("SELECT count(*) FROM chunks WHERE document_id = %s", (doc["id"],)) == [(0,)]
    finally:
        sql(f"DROP FUNCTION IF EXISTS {function}() CASCADE")
        compose("start", "worker")
    try:
        wait_until(lambda: completed(client, doc["id"]))
        assert sql("SELECT count(*) FROM chunks WHERE document_id = %s", (doc["id"],)) == [(1,)]
    finally:
        if doc:
            client.delete(f"/v1/documents/{doc['id']}")
