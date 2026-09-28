from __future__ import annotations

from pathlib import Path

import yaml

from tests.legacy_guided_tokens import (
    LEGACY_MODE_FLAG,
    LEGACY_OVERLAY,
    LEGACY_PORTAL_DATA_FLAG,
    LEGACY_PORTAL_USER_FLAG,
    LEGACY_SAMPLE,
    LEGACY_USER_FLAG,
)

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent
PROD_COMPOSE = API_ROOT / "compose.production.yml"
DOCKERFILE = API_ROOT / "Dockerfile"
PROD_SCRIPT = API_ROOT / "scripts" / "setup_production_server.ps1"
DEV_SCRIPT = API_ROOT / "scripts" / "dev.ps1"
NATIVE_RELEASE_SCRIPT = API_ROOT / "scripts" / "deploy_native_release.sh"
API_GITATTRIBUTES = API_ROOT / ".gitattributes"
HOSTED_PROD_GATE = (
    REPO_ROOT / "docs" / "quality" / "2026-06-04-hosted-production-readiness-gate.md"
)


def test_prod_compose_has_no_removed_guided_config_and_portal_is_off():
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    api = compose["services"]["api"]
    env = api["environment"]

    assert LEGACY_MODE_FLAG not in env
    assert LEGACY_USER_FLAG not in env
    assert LEGACY_PORTAL_USER_FLAG not in env
    assert LEGACY_PORTAL_DATA_FLAG not in env
    assert env["PORTAL_MOUNT_ENABLED"] == "false"
    assert "PORTAL_MOUNT_DIRECTORY" not in PROD_COMPOSE.read_text(encoding="utf-8")


def test_production_compose_uses_separate_project_and_env_contract():
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    assert compose["name"] == "sds-api-prod"
    api_env = compose["services"]["api"]["environment"]
    assert api_env["REQUIRE_DATABASE"] == "true"
    assert "ALLOWED_ORIGINS" in api_env
    assert "${API_PORT:-8090}:8090" in compose["services"]["api"]["ports"][0]


def test_production_installer_uses_separate_env_and_core_api_smoke():
    script = PROD_SCRIPT.read_text(encoding="utf-8")
    assert ".env.production" in script
    assert "compose.production.yml" in script
    assert "/api/v1/concepts?limit=1" in script
    assert "/api/v1/indicators?limit=1" in script
    assert "Semantic concept projection is incomplete" in script
    assert "smoke_stack.ps1" not in script
    assert LEGACY_OVERLAY not in script
    assert "docs-token" not in script
    assert f"{LEGACY_SAMPLE.capitalize()} mode" not in script


def test_prod_docker_reference_is_single_worker_before_external_migrations():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "--workers" not in dockerfile
    assert "--start-period=60s" in dockerfile


def test_production_installer_supports_disposable_project_and_env():
    script = PROD_SCRIPT.read_text(encoding="utf-8")
    assert "ProjectName" in script
    assert "EnvFile" in script
    assert '"compose"' in script
    assert '"-p", $ProjectName' in script
    assert "Get-ComposeProjectName" in script
    assert "return $ProjectName" in script


def test_production_installer_requires_explicit_cors_origin():
    script = PROD_SCRIPT.read_text(encoding="utf-8")
    assert "AllowedOrigin" in script
    assert "AllowLocalhostOrigin" in script
    assert "ALLOWED_ORIGINS cannot contain '*'" in script
    assert "localhost/default" in script


def test_production_installer_secret_and_reset_guardrails():
    script = PROD_SCRIPT.read_text(encoding="utf-8")
    assert "not shown" in script
    assert "docker volume rm" in script
    assert "$ResetDatabase" in script
    assert "com.docker.compose.volume=postgres_data" in script
    assert "BOOTSTRAP_ADMIN_PASSWORD" in script
    assert "Admin pass:  stored" in script


def test_dev_script_exposes_production_actions():
    script = DEV_SCRIPT.read_text(encoding="utf-8")
    assert '"ProductionInstall"' in script
    assert '"ProductionLogs"' in script
    assert '"ProductionDown"' in script
    assert '"ProductionPs"' in script
    assert "setup_production_server.ps1" in script
    assert "ProjectName" in script
    assert 'Alias("EnvFile")' in script
    assert "ProductionEnvFile" in script
    assert "RemoveVolumes" in script
    assert '"-p", $ProjectName' in script
    assert '$downArgs += "-v"' in script


def test_deployment_docs_pin_monitoring_alerting_and_migration_rollback():
    deployment_text = (API_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "## Monitoring y alerting" in deployment_text
    assert "/metrics" in deployment_text
    assert "scrape_configs" in deployment_text
    assert 'up{job="sds-api"}' in deployment_text
    assert "/ready" in deployment_text
    assert "/healthz" in deployment_text
    assert "sds_api_requests_total" in deployment_text
    assert "5xx" in deployment_text
    assert "ProductionLogs" in deployment_text
    assert "### Rollback con migraciones" in deployment_text
    assert "Alembic downgrade" in deployment_text
    assert "pre-migration backup" in deployment_text


def test_native_release_deploy_helper_pins_service_python_before_pip_install():
    assert NATIVE_RELEASE_SCRIPT.exists(), (
        "Hosted native deployments must use a checked-in helper instead of "
        "operator shell fragments that can accidentally select /bin/python3."
    )

    script = NATIVE_RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "MIN_PYTHON_MAJOR=3" in script
    assert "MIN_PYTHON_MINOR=10" in script
    assert "MAX_PYTHON_MINOR=12" in script
    assert "resolve_python" in script
    assert 'systemctl cat "$SERVICE_NAME"' in script
    assert "$CURRENT_SYMLINK/.venv/bin/python" in script
    assert "python3.12" in script
    assert "python3.11" in script
    assert "python3.10" in script
    assert "sys.version_info" in script
    assert "Refusing deployment: selected Python" in script
    assert "requires Python 3.10-3.12" in script
    assert "pip install \\" in script
    assert '--no-index --find-links "$RELEASE/wheelhouse" --require-hashes' in script
    assert '-r "$RELEASE/requirements.lock"' in script
    assert script.index('validate_python "$PYTHON_BIN"') < script.index(
        "pip install \\"
    )


def test_native_release_helper_rejects_insecure_env_permissions_and_ownership():
    script = NATIVE_RELEASE_SCRIPT.read_text(encoding="utf-8")

    assert "WARNING: $ENV_FILE is group/other-accessible" not in script
    assert '"$SECURE_IO_HELPER" env-digest' in script
    assert '"$SECURE_IO_HELPER" env-exec' in script
    assert 'source "$ENV_FILE"' not in script


def test_native_shell_helpers_are_pinned_to_lf():
    assert API_GITATTRIBUTES.exists()
    attrs = API_GITATTRIBUTES.read_text(encoding="utf-8")
    assert "scripts/*.sh text eol=lf" in attrs
    assert (
        "src/static/redoc/LICENSE -text whitespace=-blank-at-eol,-blank-at-eof" in attrs
    )


def test_deployment_docs_require_native_release_helper_for_hosted_api():
    deployment_text = (API_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    normalized_text = " ".join(deployment_text.split())

    assert "deploy_native_release.sh" in deployment_text
    assert "service ExecStart" in deployment_text
    assert "never call plain `python3 -m venv`" in deployment_text
    assert (
        "sudo -n env HEALTH_PORT=18090 bash /tmp/deploy_native_release.sh"
        in deployment_text
    )
    assert "sudo -n bash /tmp/deploy_native_release.sh" in deployment_text
    assert "For this hosted target, use this command" in normalized_text
    assert "For a separate self-hosted/reference systemd service" in deployment_text
    assert deployment_text.index(
        "sudo -n env HEALTH_PORT=18090 bash /tmp/deploy_native_release.sh"
    ) < deployment_text.index("For a separate self-hosted/reference")
    assert (
        "Get-Content api\\scripts\\deploy_native_release.sh | ssh"
        not in deployment_text
    )


def test_hosted_production_gate_records_native_public_api_boundary():
    assert (
        HOSTED_PROD_GATE.exists()
    ), f"Missing hosted production gate: {HOSTED_PROD_GATE}"

    text = HOSTED_PROD_GATE.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "Status: `PASS`" in text
    assert "https://sds.ueporreres.com/" in text
    assert "sds-api.service" in text
    assert "127.0.0.1:18090" in text
    assert "Basic Auth" in text
    assert "retired guided-sample cleanup revision" in text
    assert "sustainabilitydataspace.com" in text
    assert "not the E11 website" in text
    assert "subsidy" in normalized


def test_public_docs_point_to_hosted_production_gate_without_secrets():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (API_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    acceptance = (REPO_ROOT / "docs" / "quality" / "acceptance_gates.md").read_text(
        encoding="utf-8"
    )
    hosted_gate = HOSTED_PROD_GATE.read_text(encoding="utf-8")
    combined = "\n".join([readme, deployment, acceptance])

    assert "2026-06-04-hosted-production-readiness-gate.md" in combined
    assert "sds.ueporreres.com" in combined
    assert "SDS_HOSTED_BASIC_PASSWORD" not in hosted_gate
    assert "BOOTSTRAP_ADMIN_PASSWORD=" not in hosted_gate
