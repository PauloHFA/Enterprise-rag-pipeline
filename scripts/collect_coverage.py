"""Stop disposable app services, merge their coverage with unit data, enforce module gates."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def command(*args, env=None):
    subprocess.run(args, check=True, env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--unit-data", default=".test-results/unit.coverage")
    args = parser.parse_args()
    parts = Path(".test-results/coverage-parts")
    parts.mkdir(parents=True, exist_ok=True)
    unit = Path(args.unit_data)
    if not unit.is_file():
        parser.error(
            "Unit coverage data is required; do not count service-only coverage as combined coverage"
        )
    command("docker", "compose", "-p", args.project, "stop", "api", "worker", "outbox")
    code = (
        "from coverage import Coverage; c=Coverage(data_file='/coverage/.coverage',data_suffix=False,config_file=False); "
        "c.combine(data_paths=['/coverage'],strict=True,keep=True); c.save()"
    )
    command(
        "docker",
        "compose",
        "-p",
        args.project,
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "COVERAGE_PROCESS_START=",
        "api",
        "python",
        "-c",
        code,
    )
    command(
        "docker",
        "compose",
        "-p",
        args.project,
        "cp",
        "api:/coverage/.coverage",
        str(parts / ".coverage.container"),
    )
    shutil.copyfile(unit, parts / ".coverage.unit")
    # COVERAGE_PROCESS_START is only meaningful in instrumented Linux containers.
    environment = {
        key: value for key, value in os.environ.items() if key != "COVERAGE_PROCESS_START"
    }
    command(sys.executable, "-m", "coverage", "combine", "--keep", str(parts), env=environment)
    command(
        sys.executable,
        "-m",
        "coverage",
        "json",
        "-o",
        ".test-results/combined-coverage.json",
        env=environment,
    )
    command(
        sys.executable,
        "-m",
        "coverage",
        "xml",
        "-o",
        ".test-results/combined-coverage.xml",
        env=environment,
    )
    command(
        sys.executable,
        "scripts/check_coverage.py",
        ".test-results/combined-coverage.json",
        env=environment,
    )


if __name__ == "__main__":
    main()
