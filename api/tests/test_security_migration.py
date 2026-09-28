"""Security invariants for the combined authentication/tenant migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.database.models import ESGValue, IndicatorImportJob, ValueIdempotencyKey

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "046_harden_auth_and_value_tenancy.py"
)
INDICATOR_JOB_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "047_scope_indicator_jobs_to_tenants.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sds_migration_046", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_indicator_job_migration():
    spec = importlib.util.spec_from_file_location(
        "sds_migration_047", INDICATOR_JOB_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tenant_security_migration_is_explicitly_irreversible() -> None:
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="irreversible tenant security migration"):
        migration.downgrade()


def test_indicator_job_migration_purges_retained_validation_payloads() -> None:
    migration = _load_indicator_job_migration()
    statements: list[str] = []

    class _Operations:
        def execute(self, statement):
            statements.append(str(statement))

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    migration.op = _Operations()
    migration.upgrade()

    normalized = " ".join(statements).lower()
    assert "source_payload = null" in normalized
    assert "job_type = 'validation'" in normalized


def test_tenant_authority_columns_have_no_runtime_or_server_defaults() -> None:
    for model, authority_columns in (
        (ESGValue, ("tenant_id", "ownership_state")),
        (ValueIdempotencyKey, ("tenant_id", "ownership_state")),
        (
            IndicatorImportJob,
            ("tenant_id", "owner_user_id", "ownership_state"),
        ),
    ):
        for column_name in authority_columns:
            column = model.__table__.c[column_name]
            assert column.default is None
            assert column.server_default is None


def test_security_migrations_quarantine_without_inventing_tenant_identity() -> None:
    migration_046 = MIGRATION_PATH.read_text(encoding="utf-8")
    migration_047 = INDICATOR_JOB_MIGRATION_PATH.read_text(encoding="utf-8")

    assert "ownership_state" in migration_046
    assert "ownership_state" in migration_047
    assert "__legacy__" not in migration_046
    assert "__legacy_quarantine__" not in migration_047
    assert "users.company_id" not in migration_047
    assert "tenant_id = null" in migration_047.lower()
    assert "status = 'failed'" in migration_047.lower()
