"""Regression tests for the Docker quickstart smoke workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-quickstart-smoke.yml"
DELIVERABLES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deliverables-check.yml"
SMOKE_SCRIPT = REPO_ROOT / "api" / "scripts" / "smoke_stack.sh"


def workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow[True]


def test_docker_quickstart_smoke_runs_stack_and_functional_smoke():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["api-docker-quickstart-smoke"]["steps"]
    run_script = "\n".join(str(step.get("run", "")) for step in steps)
    named_steps = {step.get("name"): step for step in steps}

    assert "docker compose" in run_script
    assert "up -d" in run_script
    assert 'ALLOWED_ORIGINS=["http://localhost:8090"]' in run_script
    assert named_steps["Run functional quickstart smoke"]["run"] == (
        "NO_START=1 bash scripts/smoke_stack.sh minimal"
    )
    qualification = named_steps[
        "Qualify public synthetic demo, API conversions, and 1k regression tripwire"
    ]
    assert qualification["env"]["PYTHON"] == "${{ env.pythonLocation }}/bin/python"
    assert "DEMO_CI_BOOTSTRAP_ADMIN_PASSWORD" in qualification["run"]
    assert "--ci-api-principal --api-url http://127.0.0.1:8090" in qualification["run"]
    assert "ci-admin-password" not in WORKFLOW.read_text(encoding="utf-8")
    assert (
        "openssl rand -hex 32" in named_steps["Prepare quickstart environment"]["run"]
    )
    prep = named_steps["Prepare quickstart environment"]["run"]
    assert 'EXPORT_SIGNING_SECRET="$(openssl rand -hex 32)"' in prep
    assert "EXPORT_SIGNING_SECRET=${EXPORT_SIGNING_SECRET}" in prep
    assert "DATABASE_URL=postgresql://sds:***" not in prep
    assert (
        'DATABASE_URL="postgresql://sds:${POSTGRES_PASSWORD}@127.0.0.1:55432/sds"'
        in prep
    )
    assert '"$DATABASE_URL" >> "$GITHUB_ENV"' in prep
    assert steps.index(named_steps["Run functional quickstart smoke"]) < steps.index(
        qualification
    )
    smoke_script = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert 'wait_for_url "${API_BASE_URL}/healthz" "API"' in smoke_script
    assert 'curl -fsS "${API_BASE_URL}/ready" -H "$authz_header"' in smoke_script
    assert 'authz_header="Authorization: Bearer $token"' in smoke_script
    assert 'authz_header="Authorization: Bearer ***"' not in smoke_script
    assert named_steps["Show quickstart diagnostics on failure"]["if"] == "failure()"
    # Compose output is untrusted: no step may print or archive it. A failed
    # redaction after writing a raw file would still upload that file.
    assert (
        "docker compose --env-file .env -f compose.yml ps"
        in named_steps["Show quickstart diagnostics on failure"]["run"]
    )
    assert "docker compose --env-file .env -f compose.yml logs" not in run_script
    assert "compose-logs.raw.txt" not in run_script
    assert named_steps["Stop quickstart stack"]["if"] == "always()"
    assert "down -v --remove-orphans" in named_steps["Stop quickstart stack"]["run"]
    capture = named_steps["Capture quickstart evidence"]
    upload = named_steps["Upload quickstart evidence"]
    assert capture["if"] == "always()"
    assert "docker compose" in capture["run"]
    assert "artifacts/docker-quickstart" in capture["run"]
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "api/artifacts/docker-quickstart/"
    assert (
        steps.index(capture)
        < steps.index(upload)
        < steps.index(named_steps["Stop quickstart stack"])
    )
    assert ".env" not in str(upload["with"]["path"])


def test_docker_quickstart_smoke_runs_on_runtime_change_paths():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow_triggers(workflow)

    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers
    assert triggers["push"]["branches"] == ["main"]

    expected_paths = {
        ".github/workflows/docker-quickstart-smoke.yml",
        "api/.dockerignore",
        "api/Dockerfile",
        "api/compose.yml",
        "api/.env.example",
        "api/requirements.txt",
        "api/requirements.lock",
        "api/scripts/smoke_stack.sh",
        "api/scripts/demo_runtime.py",
        "api/scripts/gate_value_import_performance.py",
        "api/demo/**",
        "api/src/**",
        "api/ontologies/**",
        "api/alembic.ini",
        "api/alembic/**",
    }
    assert set(triggers["push"]["paths"]) == expected_paths
    assert set(triggers["pull_request"]["paths"]) == expected_paths


def test_env_example_keeps_plain_local_setup_db_backed():
    env_example = (REPO_ROOT / "api" / ".env.example").read_text(encoding="utf-8")

    assert "REQUIRE_DATABASE=true" in env_example
    assert "USE_POSTGRES_UNITS=true" in env_example


def test_deliverables_check_workflow_has_timeout_and_concurrency():
    workflow = yaml.safe_load(DELIVERABLES_WORKFLOW.read_text(encoding="utf-8"))

    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "github.ref" in concurrency["group"]

    deliverables_job = workflow["jobs"]["deliverables"]
    assert deliverables_job["timeout-minutes"] == 10
