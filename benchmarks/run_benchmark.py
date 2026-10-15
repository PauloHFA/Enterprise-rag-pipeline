"""Latency/load/throughput benchmark against an explicit disposable Compose project."""

import argparse
import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from evaluation.common import (
    cleanup,
    client_from_environment,
    environment_evidence,
    wait_indexed,
    write_report,
)
from evaluation.metrics import percentile


def summary(samples):
    values = [sample["latency_ms"] for sample in samples]
    return {
        "samples": len(samples),
        "errors": sum(sample["status"] != 200 for sample in samples),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
    }


def measure_search(client, query, mode, tag):
    start = time.perf_counter()
    try:
        response = client.post(
            "/v1/search",
            json={"query": query, "mode": mode, "top_k": 10, "filters": {"tags": [tag]}},
        )
        status = response.status_code
        if status == 200 and not response.json()["results"]:
            status = 599
    except Exception:
        status = 599
    return {"latency_ms": (time.perf_counter() - start) * 1000, "status": status}


def upload(client, tag, index, size):
    prefix = f"{tag} documento {index}. "
    body = (
        prefix + "Contrato de suporte empresarial. Pagamento em trinta dias. " * (size // 40 + 1)
    ).encode()[:size]
    start = time.perf_counter()
    response = client.post(
        "/v1/documents",
        files={"file": ("benchmark.txt", body, "text/plain")},
        data={"metadata": json.dumps({"tags": [tag]})},
    )
    elapsed = (time.perf_counter() - start) * 1000
    response.raise_for_status()
    if response.status_code != 202:
        raise RuntimeError("Upload must return 202")
    return response.json()["id"], elapsed


def compose(project, action):
    subprocess.run(
        ["docker", "compose", "-p", project, action, "worker"],
        check=True,
        capture_output=True,
        timeout=60,
    )


def run(client, args):
    tag, uploaded = "benchmark-" + uuid.uuid4().hex, []
    report = {
        "status": "measured",
        "samples_per_mode_and_concurrency": args.samples,
        "search": {},
        "upload_profiles": {},
    }
    try:
        # Keep cold start separate from warm request percentiles.
        start = time.perf_counter()
        first, first_latency = upload(client, tag, 0, 4096)
        uploaded.append(first)
        wait_indexed(client, [first])
        report["first_document_end_to_end_seconds"] = time.perf_counter() - start
        ingestion_start = time.perf_counter()
        for index in range(1, args.documents + 1):
            document_id, _ = upload(client, tag, index, 4096)
            uploaded.append(document_id)
        documents = wait_indexed(client, uploaded[1:])
        duration = time.perf_counter() - ingestion_start
        chunks = sum(
            item["extraction_metadata"]["embedding"]["chunks"] for item in documents.values()
        )
        report["ingestion"] = {
            "documents": args.documents,
            "file_bytes": 4096,
            "chunks": chunks,
            "seconds": duration,
            "docs_per_minute": args.documents * 60 / duration,
            "chunks_per_second": chunks / duration,
            "includes_upload_and_queue_wait": True,
        }
        report["corpus"] = {
            "documents": len(uploaded),
            "chunks": chunks
            + client.get("/v1/documents/" + first).json()["extraction_metadata"]["embedding"][
                "chunks"
            ],
        }
        report["search_cold_or_first_request"] = measure_search(
            client, "prazo de pagamento do contrato", "hybrid", tag
        )
        for mode in ("lexical", "vector", "hybrid"):
            for _ in range(5):
                result = measure_search(client, "contrato pagamento", mode, tag)
                if result["status"] != 200:
                    raise RuntimeError("Search warmup failed")
            for concurrency in (1, 4):
                queries = [
                    "contrato pagamento",
                    "pagamento dias",
                    "suporte empresarial",
                    "pagamento trinta dias",
                ]
                start = time.perf_counter()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    samples = list(
                        pool.map(
                            lambda i, queries=queries, mode=mode: measure_search(
                                client, queries[i % len(queries)], mode, tag
                            ),
                            range(args.samples),
                        )
                    )
                result = summary(samples)
                result.update(
                    {"elapsed_seconds": time.perf_counter() - start, "raw_samples": samples}
                )
                report["search"][f"{mode}_concurrency_{concurrency}"] = result
        # Upload-only size profiles deliberately run with workers stopped. This
        # isolates acceptance latency from extraction, then DELETE cancels these jobs.
        compose(args.compose_project, "stop")
        try:
            for size in (4096, 262144, 1048576):
                samples = []
                for index in range(args.upload_samples):
                    document_id, latency = upload(client, tag, f"{size}-{index}", size)
                    uploaded.append(document_id)
                    samples.append({"latency_ms": latency, "status": 200})
                    response = client.delete("/v1/documents/" + document_id)
                    response.raise_for_status()
                report["upload_profiles"][str(size)] = summary(samples)
        finally:
            compose(args.compose_project, "start")
        report["first_upload_ms"] = first_latency
        report["targets"] = {"upload_p95_ms_lt": 300, "hybrid_p95_ms_lt": 500}
        report["latency_targets_passed"] = (
            all(
                value["p95_ms"] < 300 and value["errors"] == 0
                for value in report["upload_profiles"].values()
            )
            and all(
                report["search"][f"hybrid_concurrency_{c}"]["p95_ms"] < 500
                and report["search"][f"hybrid_concurrency_{c}"]["errors"] == 0
                for c in (1, 4)
            )
            and all(result["errors"] == 0 for result in report["search"].values())
        )
        return report
    finally:
        cleanup(client, uploaded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--upload-samples", type=int, default=20)
    parser.add_argument("--documents", type=int, default=30)
    parser.add_argument("--compose-project", default=os.environ.get("RAG_TEST_COMPOSE_PROJECT"))
    parser.add_argument("--output", default=".test-results/benchmark.json")
    parser.add_argument("--check-latency", action="store_true")
    args = parser.parse_args()
    if not args.compose_project or min(args.samples, args.upload_samples, args.documents) < 1:
        parser.error(
            "Explicit disposable Compose project and positive sample/document counts required"
        )
    with client_from_environment() as client:
        report = run(client, args)
    report["environment"] = environment_evidence()
    report["limitations"] = [
        "Local synthetic corpus, not one million chunks",
        "No TLS in direct API measurements",
        "Upload profiles up to 1 MiB, not the full 20 MiB limit",
        "Upload-only size profiles cancel jobs with workers stopped",
        "Development Compose resource allocation, not production CPU/memory limits",
        "No saturation/stability guarantee; repeat on production reference hardware",
    ]
    write_report(args.output, report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in ("environment", "search")},
            indent=2,
        )
    )
    if args.check_latency and not report["latency_targets_passed"]:
        raise SystemExit(
            "Latency targets not met in this scenario; see raw samples and documented deviation"
        )


if __name__ == "__main__":
    main()
