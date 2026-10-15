import json

import pytest

from scripts.check_coverage import DOMAIN_MODULES, check
from scripts.configure import REQUIRED, configuration_report, initialized_file


def test_generated_credentials_are_unique_private_and_not_overwritten(tmp_path):
    target = tmp_path / ".env.production"
    initialized_file(target)
    from dotenv import dotenv_values

    values = dotenv_values(target)
    assert len({values[key] for key in REQUIRED}) == len(REQUIRED)
    assert all(len(values[key]) >= 40 for key in REQUIRED)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        initialized_file(target)
    assert target.read_bytes() == original
    report = configuration_report(target, tmp_path / "certs")
    serialized = json.dumps(report)
    assert all(values[key] not in serialized for key in REQUIRED)
    assert not report["files_present_for_deployment"]
    assert report["oidc"] == "optional_not_configured"


def test_coverage_gate_cannot_hide_a_missing_domain_module():
    report = {
        "files": {
            name: {"summary": {"num_statements": 100, "covered_lines": 80}}
            for name in DOMAIN_MODULES
        }
    }
    assert check(report)["passed"]
    report["files"].pop("api/backup.py")
    assert not check(report)["passed"]
    report["files"]["api/backup.py"] = {"summary": {"num_statements": 100, "covered_lines": 79}}
    assert not check(report)["passed"]
