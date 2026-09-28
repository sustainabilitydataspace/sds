"""Operational CLI database URL resolution must never guess credentials."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from scripts import import_values_csv
from src.config.settings import settings

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
OPERATIONAL_SCRIPTS = (
    "backfill_concept_indicator_links.py",
    "backfill_semantic_model.py",
    "backfill_value_revisions.py",
    "compare_canonical_mapping_parity.py",
    "demo_runtime.py",
    "gate_semantic_catalog_projection.py",
    "gate_value_import_performance.py",
    "gate_wave15_linkage.py",
    "gate_wave1_parity.py",
    "gate_wave2_projection.py",
    "gate_wave4_value_isolation.py",
    "generate_ontology_projection.py",
    "import_calculation_contracts.py",
    "import_canonical_mapping_package.py",
    "import_indicators.py",
    "import_standard_mappings.py",
    "import_standard_versioning.py",
    "import_values_csv.py",
    "materialize_canonical_pairwise_mappings.py",
    "profile_value_import.py",
    "project_semantic_catalog.py",
    "run_canonical_mapping_shadow_workflow.py",
)


@pytest.mark.parametrize(
    "script_name", OPERATIONAL_SCRIPTS + ("gate_raw_value_transform_matrix.py",)
)
def test_all_operational_clis_help_without_settings_initialization(script_name):
    code = (
        "import pydantic_settings, runpy, sys; "
        "pydantic_settings.BaseSettings.__init__ = lambda *args, **kwargs: "
        "(_ for _ in ()).throw(RuntimeError('SETTINGS_EAGER')); "
        "path = sys.argv[1]; sys.argv = [path, '--help']; "
        "runpy.run_path(path, run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS_ROOT / script_name)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


SETTINGS_FALLBACK_SCRIPTS = (
    "backfill_concept_indicator_links",
    "backfill_semantic_model",
    "backfill_value_revisions",
    "compare_canonical_mapping_parity",
    "demo_runtime",
    "gate_value_import_performance",
    "gate_raw_value_transform_matrix",
    "gate_wave15_linkage",
    "gate_wave1_parity",
    "gate_wave2_projection",
    "gate_wave4_value_isolation",
    "generate_ontology_projection",
    "import_calculation_contracts",
    "import_canonical_mapping_package",
    "import_indicators",
    "import_standard_versioning",
    "import_values_csv",
    "import_standard_mappings",
    "materialize_canonical_pairwise_mappings",
    "profile_value_import",
    "run_canonical_mapping_shadow_workflow",
)


@pytest.mark.parametrize(
    "script_name",
    [
        "backfill_concept_indicator_links",
        "backfill_semantic_model",
        "backfill_value_revisions",
        "gate_value_import_performance",
        "gate_raw_value_transform_matrix",
        "gate_wave15_linkage",
        "gate_wave1_parity",
        "gate_wave2_projection",
        "gate_wave4_value_isolation",
        "generate_ontology_projection",
        "import_calculation_contracts",
        "import_standard_versioning",
        "import_values_csv",
        "profile_value_import",
    ],
)
@pytest.mark.parametrize("behavior", ["help", "explicit_url"])
def test_cli_resolves_before_settings_initialization(script_name, behavior):
    code = (
        "import importlib, pydantic_settings, runpy, sys; "
        "pydantic_settings.BaseSettings.__init__ = lambda *args, **kwargs: "
        "(_ for _ in ()).throw(RuntimeError('SETTINGS_EAGER')); "
        "path, behavior, module_name = sys.argv[1:4]; "
        "sys.argv = [path, '--help']; "
        "result = runpy.run_path(path, run_name='__main__') if behavior == 'help' "
        "else importlib.import_module('scripts.' + module_name)"
        ".get_default_database_url('sqlite:///:memory:'); "
        "assert behavior == 'help' or result == 'sqlite:///:memory:'"
    )
    script = SCRIPTS_ROOT / f"{script_name}.py"
    result = subprocess.run(
        [sys.executable, "-c", code, str(script), behavior, script_name],
        cwd=SCRIPTS_ROOT.parent,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    if behavior == "help":
        assert "--db-url" in result.stdout


def test_strict_importer_requires_explicit_database_credential(monkeypatch):
    monkeypatch.setattr(
        settings, "database_url", SecretStr("postgresql://sds:***@localhost:5432/sds")
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="Explicit database configuration required"):
        import_values_csv.get_default_database_url()


def test_resolver_prefers_explicit_cli_without_consulting_placeholder(monkeypatch):
    from scripts.operational_db_url import resolve_operational_database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert (
        resolve_operational_database_url(
            "sqlite:///:memory:", configured_url="postgresql://sds:***@localhost/sds"
        )
        == "sqlite:///:memory:"
    )


def test_resolver_rejects_empty_cli_even_with_other_credentials(monkeypatch):
    from scripts.operational_db_url import resolve_operational_database_url

    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic-test-only-credential")
    with pytest.raises(ValueError, match="Explicit database configuration required"):
        resolve_operational_database_url("")


def test_resolver_encodes_explicit_component_password(monkeypatch):
    from scripts.operational_db_url import resolve_operational_database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "synthetic:test@only")
    assert "synthetic%3Atest%40only" in resolve_operational_database_url(
        configured_url="postgresql://sds:***@localhost/sds"
    )


@pytest.mark.parametrize("encoded_password", ["Password", "%70assword", "pass"])
def test_resolver_rejects_weak_password_even_when_explicit_or_encoded(
    encoded_password,
):
    from scripts.operational_db_url import resolve_operational_database_url

    with pytest.raises(ValueError, match="Explicit database configuration required"):
        resolve_operational_database_url(
            f"postgresql://tester:{encoded_password}@localhost/sds"
        )


@pytest.mark.parametrize(
    ("authority_and_path", "query"),
    [
        ("tester:synthetic-strong@localhost/sds", "?password=password"),
        ("tester:synthetic-strong@localhost/sds", "?%70assword=password"),
        ("tester@localhost/sds", "?password=synthetic-strong"),
        ("tester:synthetic-strong@localhost/sds", "?host=other-host"),
        ("tester:synthetic-strong@localhost/sds", "?hostaddr=192.0.2.1"),
        ("tester:synthetic-strong@localhost/sds", "?user=other-user"),
        ("tester@localhost/sds", ""),
        ("/sds", ""),
    ],
)
def test_resolver_rejects_effective_credential_or_destination_override(
    authority_and_path, query
):
    from scripts.operational_db_url import resolve_operational_database_url

    with pytest.raises(ValueError, match="Explicit database configuration required"):
        resolve_operational_database_url(f"postgresql://{authority_and_path}{query}")


def test_resolver_rejects_malformed_url_without_exposing_supplied_value():
    from scripts.operational_db_url import resolve_operational_database_url

    marker = "synthetic-sensitive-value"
    with pytest.raises(
        ValueError, match="Explicit database configuration required"
    ) as refused:
        resolve_operational_database_url(f"postgresql:/tester:{marker}@localhost/sds")
    assert marker not in str(refused.value)


@pytest.mark.parametrize(
    "script_name", ["gate_semantic_catalog_projection", "project_semantic_catalog"]
)
def test_semantic_cli_explicit_override_does_not_initialize_settings(script_name):
    module = importlib.import_module(f"scripts.{script_name}")
    with patch.dict(sys.modules, {"src.config.settings": None}):
        assert (
            module._get_default_database_url("sqlite:///:memory:")
            == "sqlite:///:memory:"
        )


@pytest.mark.parametrize("script_name", SETTINGS_FALLBACK_SCRIPTS)
def test_operational_clis_honor_settings_only_url_without_environment_fallback(
    monkeypatch, script_name
):
    module = importlib.import_module(f"scripts.{script_name}")
    configured_url = "postgresql://tester:" + "synthetic-strong@localhost/sds"
    fake_settings = SimpleNamespace(database_url=SecretStr(configured_url))
    monkeypatch.setitem(
        sys.modules, "src.config.settings", SimpleNamespace(settings=fake_settings)
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert module.get_default_database_url() == configured_url


@pytest.mark.parametrize(
    "script_name",
    [
        "gate_wave1_parity",
        "gate_wave2_projection",
        "gate_wave4_value_isolation",
        "gate_value_import_performance",
        "gate_raw_value_transform_matrix",
        "import_calculation_contracts",
        "import_values_csv",
        "profile_value_import",
    ],
)
def test_cli_preserves_settings_before_environment_precedence(monkeypatch, script_name):
    module = importlib.import_module(f"scripts.{script_name}")
    configured_url = "postgresql://tester:" + "synthetic-strong@localhost/sds"
    monkeypatch.setitem(
        sys.modules,
        "src.config.settings",
        SimpleNamespace(
            settings=SimpleNamespace(database_url=SecretStr(configured_url))
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert module.get_default_database_url() == configured_url


def test_value_gate_hierarchy_fixture_import_does_not_initialize_settings():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pydantic_settings; "
            "pydantic_settings.BaseSettings.__init__ = lambda *args, **kwargs: "
            "(_ for _ in ()).throw(RuntimeError('SETTINGS_EAGER')); "
            "from scripts import value_import_gate_hierarchy; "
            "assert 'src.config.settings' not in __import__('sys').modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script_name", OPERATIONAL_SCRIPTS)
def test_operational_scripts_do_not_resolve_database_before_argument_parse(script_name):
    source = (SCRIPTS_ROOT / script_name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert 'os.getenv("POSTGRES_PASSWORD", "password")' not in source
    assert "postgresql://sds:password@" not in source
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "add_argument":
            continue
        if not any(
            isinstance(arg, ast.Constant) and arg.value == "--db-url"
            for arg in node.args
        ):
            continue
        assert not any(
            isinstance(keyword.value, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "default"
        )
