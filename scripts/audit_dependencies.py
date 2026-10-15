"""Audit the complete installed snapshot, normalizing CPU wheel local-version tags."""

import argparse
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

from packaging.version import Version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".test-results/dependency-audit.json")
    parser.add_argument("--project")
    parser.add_argument("--service", choices=["api", "worker", "outbox"])
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    requirements = output.with_suffix(".requirements.txt")
    if args.service:
        if not args.project:
            parser.error("--service requires an explicit disposable --project")
        code = "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"
        runtime = subprocess.check_output(
            [
                "docker",
                "compose",
                "-p",
                args.project,
                "exec",
                "-T",
                args.service,
                "python",
                "-c",
                code,
            ],
            text=True,
        )
        installed = json.loads(runtime)
    else:
        installed = {
            distribution.metadata["Name"]: distribution.version
            for distribution in importlib.metadata.distributions()
        }
    snapshot = {}
    for name, installed_version in installed.items():
        if name.lower().replace("_", "-") == "enterprise-rag":
            continue  # This repository is the subject of tests/lint, not a third-party distribution.
        snapshot[name] = Version(installed_version).public.split("+")[0]
    requirements.write_text(
        "\n".join(f"{name}=={version}" for name, version in sorted(snapshot.items())) + "\n",
        encoding="utf-8",
    )
    # Every installed transitive dependency is explicitly listed; no resolver or
    # alternate CUDA wheel download is needed. Un-auditable packages fail --strict.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "--no-deps",
            "--disable-pip",
            "--strict",
            "--format",
            "json",
            "--progress-spinner",
            "off",
            "-r",
            str(requirements),
            "--output",
            str(output),
        ],
        check=False,
    )
    if not output.is_file():
        raise SystemExit(result.returncode or 1)
    report = json.loads(output.read_text(encoding="utf-8"))
    skipped = [item["name"] for item in report["dependencies"] if "skip_reason" in item]
    vulnerabilities = sum(len(item.get("vulns", [])) for item in report["dependencies"])
    print(
        json.dumps(
            {
                "packages": len(snapshot),
                "vulnerabilities": vulnerabilities,
                "skipped": skipped,
                "cpu_wheel_normalization": "Audit upstream base version of +cpu wheels",
            }
        )
    )
    raise SystemExit(result.returncode or (1 if skipped or vulnerabilities else 0))


if __name__ == "__main__":
    main()
