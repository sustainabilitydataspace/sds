"""Keep public runtime declarations compatible with the immutable v1 profile."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.semantic.profiles import canonical_json as cj

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docs_only


def test_public_test_workflows_use_canonical_python():
    checked_jobs = set()
    for path in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in workflow["jobs"].items():
            if (
                job.get("env", {}).get("SDS_RUNTIME_COMPATIBILITY_SCOPE")
                == "noncanonical-service-runtime"
            ):
                continue
            steps = job.get("steps", [])
            commands = "\n".join(step.get("run", "") for step in steps)
            if not re.search(
                r"\b(pytest|test(?:-coverage|-unit)?|quick-test|api-ci-local|gate-varch)\b",
                commands,
            ):
                continue
            versions = [
                step["with"]["python-version"]
                for step in steps
                if step.get("uses", "").startswith("actions/setup-python@")
            ]
            assert versions == ["3.10"], (path.name, job_id, versions)
            checked_jobs.add((path.name, job_id))
    assert ("api-ci.yml", "api-quality-security-test") in checked_jobs


def test_api_ci_qualifies_the_advertised_noncanonical_service_runtime_range():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "api-ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["api-runtime-compatibility"]
    assert job["env"]["SDS_RUNTIME_COMPATIBILITY_SCOPE"] == (
        "noncanonical-service-runtime"
    )
    versions = job["strategy"]["matrix"]["python-version"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])

    assert versions == ["3.11", "3.12"]
    assert "python -m compileall -q src" in commands
    assert "tests/test_f02_auth_hardening.py" in commands
    assert "tests/test_healthz_dependency_status.py" in commands
    assert "tests/test_settings_extra_env.py" in commands
    assert "tests/test_settings_security.py" in commands


def test_docker_base_matches_canonical_unicode_pin():
    assert cj.PINNED_UNICODE_VERSION == "13.0.0"
    dockerfile = (REPO_ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)
    assert bases == [
        "python:3.10-slim@sha256:179cecec99c29e6f1e97caa424514599267f7dd836696dd673ea5587eefc65fa"
    ]
    assert "COPY requirements.lock ./" in dockerfile
    assert "--require-hashes" in dockerfile
    assert (REPO_ROOT / "api" / "requirements.lock").is_file()


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "api/README.md",
        "api/docs/getting-started.md",
        "api/docs/deployment.md",
        "api/docs/troubleshooting.md",
        "api/docs/tests.md",
    ],
)
def test_user_docs_distinguish_service_and_canonical_runtimes(relative_path):
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert "Python 3.10-3.12" in text
    assert "sds-canonical-json-v1" in text
    assert "CPython 3.10" in text
    assert f"`{cj.PINNED_UNICODE_VERSION}`" in text
    assert "falla cerrada" in text


def test_full_suite_bootstrap_and_docker_evidence_boundary_are_documented():
    text = (REPO_ROOT / "api" / "docs" / "tests.md").read_text(encoding="utf-8")
    assert "uv venv --clear --python 3.10 api/.venv" in text
    assert "uv pip install --python api/.venv/bin/python" in text
    assert "make -C api test" in text
    assert "workflow Docker Quickstart Smoke no ejecutan la suite canónica" in text
    assert "`/healthz` y `/ready`" in text
