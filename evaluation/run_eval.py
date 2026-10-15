"""Upload a uniquely tagged corpus, evaluate all modes, then remove only that corpus."""

import argparse
import hashlib
import io
import json
import time
import uuid
from pathlib import Path

import docx

from .common import (
    cleanup,
    client_from_environment,
    environment_evidence,
    load_dataset,
    wait_indexed,
    write_report,
)
from .metrics import score_ranking


def evaluate(client, dataset, run_id):
    uploaded, ids = [], {}
    try:
        for document in dataset["documents"]:
            # A package property changes the file hash without introducing random tokens into extracted text.
            package = docx.Document()
            package.core_properties.title = document["title"]
            package.core_properties.identifier = run_id
            package.add_paragraph(document["text"])
            buffer = io.BytesIO()
            package.save(buffer)
            response = client.post(
                "/v1/documents",
                files={
                    "file": (
                        document["id"] + ".docx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"metadata": json.dumps({"title": document["title"], "tags": [run_id]})},
            )
            response.raise_for_status()
            if response.status_code != 202:
                raise RuntimeError("Upload did not return 202")
            document_id = response.json()["id"]
            uploaded.append(document_id)
            ids[document_id] = document["id"]
        indexed = wait_indexed(client, uploaded)
        per_mode = {}
        for mode in ("lexical", "vector", "hybrid"):
            rows = []
            for query in dataset["queries"]:
                start = time.perf_counter()
                response = client.post(
                    "/v1/search",
                    json={
                        "query": query["query"],
                        "mode": mode,
                        "top_k": 10,
                        "filters": {"tags": [run_id]},
                    },
                )
                response.raise_for_status()
                hits = response.json()["results"]
                ranking = [ids[item["document_id"]] for item in hits]
                rows.append(
                    {
                        "query_id": query["id"],
                        "ranking": list(dict.fromkeys(ranking)),
                        "scores": score_ranking(ranking, query["relevance"]),
                        "latency_ms": (time.perf_counter() - start) * 1000,
                    }
                )
            aggregate = {
                metric: sum(row["scores"][metric] for row in rows) / len(rows)
                for metric in rows[0]["scores"]
            }
            per_mode[mode] = {"aggregate": aggregate, "queries": rows}
        return {
            "status": "measured",
            "run_id": run_id,
            "documents": len(uploaded),
            "queries": len(dataset["queries"]),
            "chunks": sum(
                doc["extraction_metadata"]["embedding"]["chunks"] for doc in indexed.values()
            ),
            "top_k_chunks": 10,
            "ranking_unit": "unique documents in first 10 returned chunks",
            "dataset_provenance": dataset["provenance"],
            "modes": per_mode,
        }
    finally:
        cleanup(client, uploaded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="evaluation/dataset.json")
    parser.add_argument("--output", default=".test-results/retrieval.json")
    parser.add_argument(
        "--check", action="store_true", help="Enforce the documented synthetic regression floor"
    )
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    with client_from_environment() as client:
        report = evaluate(client, dataset, "eval-" + uuid.uuid4().hex)
    report["environment"] = environment_evidence()
    report["dataset_sha256"] = hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest()
    scores = report["modes"]["hybrid"]["aggregate"]
    report["regression_floor"] = {"recall@10": 0.9, "mrr@10": 0.7, "ndcg@10": 0.75}
    report["quality_gate_passed"] = all(
        scores[key] >= value for key, value in report["regression_floor"].items()
    )
    write_report(args.output, report)
    print(
        json.dumps(
            {mode: result["aggregate"] for mode, result in report["modes"].items()}, indent=2
        )
    )
    if args.check and not report["quality_gate_passed"]:
        raise SystemExit("Synthetic retrieval regression floor not met; see report")


if __name__ == "__main__":
    main()
