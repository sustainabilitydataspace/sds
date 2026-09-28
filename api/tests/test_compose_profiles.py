from __future__ import annotations

import re
from pathlib import Path

import yaml


def test_compose_only_defines_api_and_postgres_services():
    compose_path = Path(__file__).resolve().parents[1] / "compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    services = compose["services"]
    assert "api" in services
    assert "postgres" in services

    assert set(services) == {"api", "postgres"}

    api_env = services["api"]["environment"]
    assert "ENABLE_FUSEKI" not in api_env
    assert "ENABLE_WEAVIATE" not in api_env
    assert "SPARQL_ENDPOINT" not in api_env
    assert "WEAVIATE_URL" not in api_env
    assert '["http://localhost:8090"]' in api_env["ALLOWED_ORIGINS"]

    volumes = compose["volumes"]
    assert set(volumes) == {"postgres_data"}


def test_postgres_images_use_the_explicit_official_docker_hub_reference():
    api_root = Path(__file__).resolve().parents[1]
    for filename in ("compose.yml", "compose.production.yml"):
        compose = yaml.safe_load((api_root / filename).read_text(encoding="utf-8"))
        assert (
            compose["services"]["postgres"]["image"]
            == "docker.io/library/postgres:15-alpine"
        )


def test_compose_api_uses_the_same_required_database_password_as_postgres():
    api_root = Path(__file__).resolve().parents[1]
    password = "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
    for filename in ("compose.yml", "compose.production.yml"):
        compose = yaml.safe_load((api_root / filename).read_text(encoding="utf-8"))
        assert (
            compose["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
            == password
        )
        assert compose["services"]["api"]["environment"]["DATABASE_URL"] == (
            f"postgresql://sds:{password}@postgres:5432/sds"
        )


def test_documented_compose_profiles_propagate_required_runtime_security():
    api_root = Path(__file__).resolve().parents[1]
    expected_revision = {
        "VALUE_REVISION_API_ENABLED": "true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED": "true",
        "VALUE_REVISION_PRIMARY_READ_PATH": "revision",
    }

    for filename in ("compose.yml", "compose.production.yml"):
        compose = yaml.safe_load((api_root / filename).read_text(encoding="utf-8"))
        environment = compose["services"]["api"]["environment"]
        assert (
            ":?EXPORT_SIGNING_SECRET is required}"
            in environment["EXPORT_SIGNING_SECRET"]
        )
        for key, expected in expected_revision.items():
            assert environment[key].endswith(f":-{expected}}}")

    example_keys = {
        line.split("=", 1)[0]
        for line in (api_root / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert "EXPORT_SIGNING_SECRET" in example_keys
    assert set(expected_revision).issubset(example_keys)


def test_service_makefile_exposes_only_minimal_runtime_targets():
    makefile_path = Path(__file__).resolve().parents[1] / "Makefile"
    content = makefile_path.read_text(encoding="utf-8")

    for target in ("up-fuseki", "up-weaviate", "up-full"):
        assert not re.search(
            rf"(?m)^{re.escape(target)}:", content
        ), f"Unexpected legacy target: {target}"
    assert re.search(r"(?m)^up:", content), "Missing minimal up target"
