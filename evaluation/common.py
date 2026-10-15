import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def client_from_environment():
    url, key = os.environ.get("RAG_E2E_URL"), os.environ.get("RAG_E2E_API_KEY")
    if not url or not key or urlsplit(url).username:
        raise ValueError("Set RAG_E2E_URL without embedded credentials and RAG_E2E_API_KEY")
    return httpx.Client(base_url=url, headers={"X-API-Key": key}, timeout=180)


def load_dataset(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [item["id"] for item in data["documents"]]
    query_ids = [item["id"] for item in data["queries"]]
    if (
        len(set(ids)) != len(ids)
        or len(set(query_ids)) != len(query_ids)
        or not 50 <= len(query_ids) <= 200
    ):
        raise ValueError("Dataset requires unique document/query IDs and 50–200 queries")
    for query in data["queries"]:
        if (
            not query["query"].strip()
            or not query["relevance"]
            or not set(query["relevance"]).issubset(ids)
        ):
            raise ValueError("Invalid query or relevance references")
        if any(
            type(value) is not int or not 1 <= value <= 3 for value in query["relevance"].values()
        ):
            raise ValueError("Relevance grades must be integers from 1 to 3")
    return data


def wait_indexed(client, document_ids, timeout=600):
    pending, documents = set(document_ids), {}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for document_id in list(pending):
            response = client.get("/v1/documents/" + document_id)
            response.raise_for_status()
            document = response.json()
            if document["status"] == "failed":
                raise RuntimeError(f"Indexing failed for document {document_id}")
            if document["status"] == "indexed":
                documents[document_id] = document
                pending.remove(document_id)
        if pending:
            time.sleep(0.2)
    if pending:
        raise TimeoutError(f"{len(pending)} documents did not reach indexed")
    return documents


def cleanup(client, document_ids):
    failures = []
    for document_id in document_ids:
        try:
            response = client.delete("/v1/documents/" + document_id)
            if response.status_code not in (204, 404):
                failures.append(document_id)
        except httpx.HTTPError:
            failures.append(document_id)
    if failures:
        raise RuntimeError(f"Cleanup failed for {len(failures)} documents; inspect the test tenant")


def environment_evidence():
    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    files = [
        "api/search.py",
        "worker/chunking.py",
        "api/requirements.txt",
        "worker/requirements.txt",
    ]
    evidence = {
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(git("status", "--porcelain")),
        "client_python": platform.python_version(),
        "client_platform": platform.platform(),
        "source_sha256": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files
        },
    }
    project = os.environ.get("RAG_TEST_COMPOSE_PROJECT")
    if project:
        code = (
            "import importlib.metadata as m,importlib.util,json,os,platform; "
            "print(json.dumps({'platform':platform.platform(),'versions':{n:m.version(n) for n in "
            "['torch','sentence-transformers','transformers','huggingface-hub','numpy']},"
            "'settings':{n:os.getenv(n) for n in ['CHUNK_TARGET_TOKENS','CHUNK_OVERLAP_TOKENS',"
            "'EMBEDDING_BATCH_SIZE','OCR_ENABLED','TORCH_NUM_THREADS','EMBEDDING_MODEL_REVISION','COVERAGE_PROCESS_START']},"
            "'coverage_installed':importlib.util.find_spec('coverage') is not None}))"
        )
        result = subprocess.run(
            ["docker", "compose", "-p", project, "exec", "-T", "worker", "python", "-c", code],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        evidence["runtime"] = json.loads(result.stdout)
        host = subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
        host_info = json.loads(host)
        evidence["docker_resources"] = {
            name: host_info[name]
            for name in ("NCPU", "MemTotal", "Architecture", "OSType", "ServerVersion")
        }
    return evidence


def write_report(path, report):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
