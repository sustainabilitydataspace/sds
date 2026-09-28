"""Contracts proving SDS no longer ships the removed guided runtime."""

from __future__ import annotations

from pathlib import Path

from src.auth.models import ROLE_PERMISSIONS, UserRole
from tests.legacy_guided_tokens import (
    LEGACY_API_PREFIX,
    LEGACY_DOC,
    LEGACY_FASTAPI_SEED,
    LEGACY_LOCAL_TRUSTED_IPS,
    LEGACY_MODE_FLAG,
    LEGACY_OVERLAY,
    LEGACY_PATH,
    LEGACY_PORTAL_DATA_FLAG,
    LEGACY_PORTAL_ROLE,
    LEGACY_PORTAL_SEED,
    LEGACY_PORTAL_USER,
    LEGACY_PORTAL_USER_FLAG,
    LEGACY_PROVIDER,
    LEGACY_REVISION,
    LEGACY_SAMPLE,
    LEGACY_SAMPLE_UPPER,
    LEGACY_SETUP_SCRIPT,
    LEGACY_USER_FLAG,
    LEGACY_WORDS,
    term,
)

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent


def test_auth_model_has_no_legacy_guided_roles():
    role_values = {role.value for role in UserRole}

    assert LEGACY_SAMPLE not in role_values
    assert LEGACY_PORTAL_ROLE not in role_values
    assert all(
        role.value not in {LEGACY_SAMPLE, LEGACY_PORTAL_ROLE}
        for role in ROLE_PERMISSIONS
    )


def test_runtime_exposes_no_removed_guided_routes_or_overlay(client):
    openapi = client.get("/openapi.json")
    docs = client.get("/docs")

    assert openapi.status_code == 200
    assert docs.status_code == 200
    assert not any(
        path.startswith(LEGACY_API_PREFIX) for path in openapi.json()["paths"]
    )
    assert f"/static/swagger-ui/{LEGACY_OVERLAY}" not in docs.text
    assert LEGACY_OVERLAY not in docs.text


def test_removed_guided_source_files_are_absent():
    removed_paths = [
        API_ROOT
        / "src"
        / "database"
        / term("bootstrap_fastapi_", LEGACY_SAMPLE, "_sample.py"),
        API_ROOT / "src" / "database" / term("bootstrap_portal_", LEGACY_SAMPLE, ".py"),
        API_ROOT / "src" / "static" / "swagger-ui" / f"{LEGACY_OVERLAY}.css",
        API_ROOT / "src" / "static" / "swagger-ui" / f"{LEGACY_OVERLAY}.js",
        API_ROOT / "src" / "data" / term("fx_rates_", LEGACY_SAMPLE, "_seed.csv"),
        API_ROOT / "scripts" / LEGACY_SETUP_SCRIPT,
        API_ROOT / "scripts" / term("seed_portal_", LEGACY_SAMPLE, "_data.py"),
        API_ROOT / "docs" / LEGACY_DOC,
    ]

    assert [
        str(path.relative_to(REPO_ROOT)) for path in removed_paths if path.exists()
    ] == []


def test_operator_entrypoints_do_not_expose_removed_guided_actions():
    dev_script = (API_ROOT / "scripts" / "dev.ps1").read_text(encoding="utf-8")
    root_makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    api_makefile = (API_ROOT / "Makefile").read_text(encoding="utf-8")
    api_readme = (API_ROOT / "README.md").read_text(encoding="utf-8")
    root_gates = (REPO_ROOT / "scripts" / "gates.ps1").read_text(encoding="utf-8")
    docker_smoke_workflow = (
        REPO_ROOT / ".github" / "workflows" / "docker-quickstart-smoke.yml"
    ).read_text(encoding="utf-8")
    compose = (API_ROOT / "compose.yml").read_text(encoding="utf-8")
    env_example = (API_ROOT / ".env.example").read_text(encoding="utf-8")

    forbidden = [
        term("Local", LEGACY_SAMPLE.capitalize()),
        term("Guided ", LEGACY_SAMPLE.capitalize()),
        term("SeedPortal", LEGACY_SAMPLE.capitalize()),
        term("Portal", LEGACY_SAMPLE.capitalize(), "Seed"),
        term("portal-", LEGACY_SAMPLE, "-seed"),
        term(LEGACY_SAMPLE, "_madrid_plant"),
        LEGACY_MODE_FLAG,
        LEGACY_LOCAL_TRUSTED_IPS,
        LEGACY_USER_FLAG,
        LEGACY_PORTAL_USER_FLAG,
        LEGACY_PORTAL_DATA_FLAG,
        LEGACY_PROVIDER,
    ]
    combined = "\n".join(
        [
            dev_script,
            root_makefile,
            api_makefile,
            api_readme,
            root_gates,
            docker_smoke_workflow,
            compose,
            env_example,
        ]
    )

    for token in forbidden:
        assert token not in combined


def test_conversion_catalog_has_no_removed_guided_fx_seed():
    source = (
        API_ROOT / "src" / "database" / "bootstrap_conversion_catalog.py"
    ).read_text(encoding="utf-8")

    assert LEGACY_PROVIDER not in source
    assert term(LEGACY_SAMPLE_UPPER, "_FX_POLICY_ID") not in source
    assert term("fx_rates_", LEGACY_SAMPLE, "_seed") not in source
    assert term("include_", LEGACY_SAMPLE, "_fx") not in source


def test_legacy_cleanup_migration_fails_loudly_on_mixed_nordhaven_tenants():
    source = (API_ROOT / "alembic" / "versions" / f"{LEGACY_REVISION}.py").read_text(
        encoding="utf-8"
    )

    assert "Mixed Nordhaven value_context tenants found" in source
    assert "Nordhaven value_context tenant realignment incomplete" in source
    assert "UPDATE value_revisions" in source
    assert "UPDATE value_revision_events" in source
    assert "UPDATE current_value_pointers" in source
    assert "UPDATE reported_value_pointers" in source
    assert "SET tenant_id = 'nordhaven_components_group'" in source


def test_current_public_surfaces_do_not_reference_removed_guided_runtime():
    current_public_surfaces = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "api" / "README.md",
        REPO_ROOT / "CHANGELOG.md",
        *sorted((REPO_ROOT / "api" / "docs").glob("*.md")),
        *sorted((REPO_ROOT / "docs").rglob("*.md")),
        *sorted((REPO_ROOT / "deliverables").rglob("*.md")),
    ]
    forbidden = [
        LEGACY_PATH,
        LEGACY_WORDS,
        LEGACY_API_PREFIX,
        LEGACY_OVERLAY,
        f"{LEGACY_SAMPLE}=",
        f"LOCAL_{LEGACY_SAMPLE_UPPER}",
        f"{LEGACY_SAMPLE_UPPER}_MODE",
        LEGACY_PROVIDER,
        term("Local", LEGACY_SAMPLE.capitalize()),
        LEGACY_PORTAL_USER,
        LEGACY_FASTAPI_SEED,
        LEGACY_PORTAL_SEED,
        LEGACY_SETUP_SCRIPT,
    ]

    leaks = []
    for path in current_public_surfaces:
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token.lower() in text:
                leaks.append(f"{path.relative_to(REPO_ROOT)}: {token}")

    assert leaks == []
