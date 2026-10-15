"""Fail when a named domain module is missing or below the documented line threshold."""

import argparse
import json
from pathlib import Path

DOMAIN_MODULES = (
    "api/main.py",
    "api/search.py",
    "api/schemas.py",
    "api/security.py",
    "api/uploads.py",
    "api/outbox.py",
    "api/retention.py",
    "api/backup.py",
    "api/reconcile_storage.py",
    "api/rabbitmq_client.py",
    "api/minio_client.py",
    "api/rate_limiter.py",
    "worker/worker.py",
    "worker/chunking.py",
    "worker/extraction.py",
    "worker/extract_process.py",
    "worker/replay_dlq.py",
    "shared/models.py",
    "shared/telemetry.py",
)


def check(report, minimum=80):
    files = {name.replace("\\", "/"): value for name, value in report["files"].items()}
    results = {}
    for name in DOMAIN_MODULES:
        value = files.get(name)
        if value is None:
            results[name] = {"percent": 0.0, "passed": False, "missing_module": True}
            continue
        summary = value["summary"]
        statements = summary["num_statements"]
        percent = 100 * summary["covered_lines"] / statements if statements else 100.0
        results[name] = {
            "percent": round(percent, 2),
            "passed": percent >= minimum,
            "covered_lines": summary["covered_lines"],
            "statements": statements,
        }
    return {
        "minimum_per_module": minimum,
        "passed": all(row["passed"] for row in results.values()),
        "modules": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--output", default=".test-results/domain-coverage.json")
    args = parser.parse_args()
    result = check(json.loads(Path(args.report).read_text(encoding="utf-8")))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for name, row in result["modules"].items():
        print(f"{name}: {row['percent']:.2f}% {'PASS' if row['passed'] else 'FAIL'}")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
