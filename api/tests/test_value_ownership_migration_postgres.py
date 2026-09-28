"""Populated 045→046 ownership migration on an explicitly disposable PostgreSQL 15."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from alembic import command, op
from src.database.migrations import _alembic_config


@pytest.mark.parametrize(
    "revision_tenant,legacy_id,linked_to_legacy,source_system,should_block",
    [
        ("tenant-b", "historically-tenant-a-value", True, None, True),
        ("tenant-a", "matching-revision-is-not-ownership-evidence", True, None, True),
        ("tenant-b", "unlinked-legacy-value", False, None, False),
        ("tenant-b", "orphan-internal-source", False, "legacy_esg_values", True),
    ],
)
def test_legacy_revision_source_id_does_not_assign_value_ownership(
    revision_tenant: str,
    legacy_id: str,
    linked_to_legacy: bool,
    source_system: str | None,
    should_block: bool,
) -> None:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url or os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("requires explicit disposable PostgreSQL reset opt-in")
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "sds_disposable"
    ):
        pytest.skip("requires the named local disposable PostgreSQL database")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            version = connection.execute(text("SHOW server_version_num")).scalar_one()
            assert version.startswith("15"), "test requires PostgreSQL 15"
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "045_merge_energy_units_and_value_identity"
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO esg_values "
                    "(id, concept, entity, period, value, unit, external_key) "
                    "VALUES (:id, 'test:energy', 'shared-plant', '2024-12-31', 10, 'MWh', 'shared-key')"
                ),
                {"id": legacy_id},
            )
            context_id = connection.execute(
                text(
                    "INSERT INTO value_contexts "
                    "(tenant_id, context_hash_recipe_version, context_hash, entity_id, "
                    "reporting_period_id, period_type, reporting_boundary_id, "
                    "indicator_identifier, standard_release_id, standard_datapoint_id, "
                    "value_kind) VALUES (:tenant, 'v1', :hash, 'shared-plant', '2024', "
                    "'year', 'group', 'test:energy', 'test', 'energy', 'numeric') "
                    "RETURNING id"
                ),
                {"tenant": revision_tenant, "hash": revision_tenant * 8},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO value_revisions "
                    "(id, context_id, tenant_id, revision_number, state, value_kind, "
                    "canonical_value, numeric_value, unit, source_system, source_record_id) "
                    "VALUES ('rev-link', :context_id, :tenant, 1, 'approved', 'numeric', "
                    "'10', 10, 'MWh', :source_system, :source_record_id)"
                ),
                {
                    "context_id": context_id,
                    "tenant": revision_tenant,
                    "source_system": source_system,
                    "source_record_id": (
                        legacy_id if linked_to_legacy else "other-source"
                    ),
                },
            )
        if should_block:
            with pytest.raises(DBAPIError, match="operator-reviewed disposition"):
                with engine.connect() as connection:
                    command.upgrade(
                        _alembic_config(connection), "046_harden_auth_and_value_tenancy"
                    )
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    == "045_merge_energy_units_and_value_identity"
                )
            return
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "046_harden_auth_and_value_tenancy"
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "046_harden_auth_and_value_tenancy"
            )
            assert connection.execute(
                text(
                    "SELECT tenant_id, ownership_state FROM esg_values WHERE id = :id"
                ),
                {"id": legacy_id},
            ).one() == (None, "quarantined")
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM esg_values WHERE id = :id AND "
                        "tenant_id = :tenant AND ownership_state = 'resolved'"
                    ),
                    {"id": legacy_id, "tenant": revision_tenant},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_migration_preflight_blocks_concurrent_legacy_revision_link(
    monkeypatch,
) -> None:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url or os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("requires explicit disposable PostgreSQL reset opt-in")
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "sds_disposable"
    ):
        pytest.skip("requires the named local disposable PostgreSQL database")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(text("SHOW server_version_num"))
                .scalar_one()
                .startswith("15")
            )
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "045_merge_energy_units_and_value_identity"
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO esg_values (id, concept, entity, period, value, unit) "
                    "VALUES ('legacy-owned-unknown', 'test:energy', 'site', '2024-12-31', 1, 'MWh')"
                )
            )
            context_id = connection.execute(
                text(
                    "INSERT INTO value_contexts (tenant_id, context_hash_recipe_version, "
                    "context_hash, entity_id, reporting_period_id, period_type, "
                    "reporting_boundary_id, indicator_identifier, standard_release_id, "
                    "standard_datapoint_id, value_kind) "
                    "VALUES ('tenant-b', 'v1', 'race-check', 'site', '2024', 'year', "
                    "'group', 'test:energy', 'test', 'energy', 'numeric') RETURNING id"
                )
            ).scalar_one()

        original_execute = op.execute
        attempted = False
        blocked = False

        def after_preflight(statement, *args, **kwargs):
            nonlocal attempted, blocked
            result = original_execute(statement, *args, **kwargs)
            if "operator-reviewed disposition before migration 046" in str(statement):
                attempted = True
                try:
                    with engine.begin() as contender:
                        contender.execute(text("SET LOCAL lock_timeout = '300ms'"))
                        contender.execute(
                            text(
                                "INSERT INTO value_revisions "
                                "(id, context_id, tenant_id, revision_number, state, "
                                "value_kind, canonical_value, numeric_value, unit, source_record_id) "
                                "VALUES ('race-link', :context_id, 'tenant-b', 1, "
                                "'approved', 'numeric', '1', 1, 'MWh', 'legacy-owned-unknown')"
                            ),
                            {"context_id": context_id},
                        )
                except DBAPIError as exc:
                    assert "lock timeout" in str(exc).lower()
                    blocked = True
            return result

        monkeypatch.setattr(op, "execute", after_preflight)
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "046_harden_auth_and_value_tenancy"
            )
        assert attempted
        assert blocked, "preflight must retain a write lock through the upgrade"
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM value_revisions WHERE id = 'race-link'")
                ).scalar_one()
                == 0
            )
        # A deliberate stop at 046 must not reopen the same cross-tenant link
        # after the migration transaction releases its preflight locks.
        with pytest.raises(DBAPIError, match="external source"):
            with engine.begin() as contender:
                contender.execute(
                    text(
                        "INSERT INTO value_revisions "
                        "(id, context_id, tenant_id, revision_number, state, "
                        "value_kind, canonical_value, numeric_value, unit, source_record_id) "
                        "VALUES ('late-link', :context_id, 'tenant-b', 1, "
                        "'approved', 'numeric', '1', 1, 'MWh', 'legacy-owned-unknown')"
                    ),
                    {"context_id": context_id},
                )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO esg_values "
                    "(id, concept, entity, period, value, unit, tenant_id, ownership_state) "
                    "VALUES ('new-owned-source', 'test:energy', 'site', '2024-12-31', "
                    "1, 'MWh', 'tenant-b', 'resolved')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO value_revisions "
                    "(id, context_id, tenant_id, revision_number, state, value_kind, "
                    "canonical_value, numeric_value, unit, source_system, source_record_id) "
                    "VALUES ('same-owner-link', :context_id, 'tenant-b', 1, "
                    "'approved', 'numeric', '1', 1, 'MWh', 'sds_values', 'new-owned-source')"
                ),
                {"context_id": context_id},
            )
        with pytest.raises(DBAPIError, match="quarantined or foreign"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE esg_values SET tenant_id = 'tenant-c' "
                        "WHERE id = 'new-owned-source'"
                    )
                )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO value_revisions "
                    "(id, context_id, tenant_id, revision_number, state, value_kind, "
                    "canonical_value, numeric_value, unit, source_record_id) "
                    "VALUES ('external-source', :context_id, 'tenant-b', 2, "
                    "'approved', 'numeric', '2', 2, 'MWh', 'future-collision')"
                ),
                {"context_id": context_id},
            )
        with pytest.raises(DBAPIError, match="external source"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO esg_values "
                        "(id, concept, entity, period, value, unit, tenant_id, ownership_state) "
                        "VALUES ('future-collision', 'test:energy', 'site', "
                        "'2024-12-31', 2, 'MWh', 'tenant-c', 'resolved')"
                    )
                )
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            with engine.begin() as connection:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                connection.execute(
                    text(
                        "INSERT INTO value_revisions "
                        "(id, context_id, tenant_id, revision_number, state, "
                        "value_kind, canonical_value, numeric_value, unit, source_record_id) "
                        "VALUES ('stale-revision', :context_id, 'tenant-b', 3, "
                        "'approved', 'numeric', '3', 3, 'MWh', 'new-owned-source')"
                    ),
                    {"context_id": context_id},
                )
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            with engine.begin() as connection:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                connection.execute(
                    text(
                        "INSERT INTO esg_values (id, concept, entity, period, "
                        "value, unit, tenant_id, ownership_state) "
                        "VALUES ('stale-value', 'test:energy', 'site', "
                        "'2024-12-31', 3, 'MWh', 'tenant-b', 'resolved')"
                    )
                )
        with pytest.raises(DBAPIError, match="external source"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO esg_values (id, concept, entity, period, "
                        "value, unit, tenant_id, ownership_state) "
                        "VALUES ('future-collision', 'test:energy', 'site', "
                        "'2024-12-31', 2, 'MWh', 'tenant-b', 'resolved')"
                    )
                )
        with pytest.raises(DBAPIError, match="requires matching local value"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO value_revisions "
                        "(id, context_id, tenant_id, revision_number, state, "
                        "value_kind, canonical_value, numeric_value, unit, "
                        "source_system, source_record_id) "
                        "VALUES ('missing-internal', :context_id, 'tenant-b', 3, "
                        "'approved', 'numeric', '3', 3, 'MWh', "
                        "'sds_values', 'absent-source')"
                    ),
                    {"context_id": context_id},
                )
        with pytest.raises(DBAPIError, match="referenced value source"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM esg_values WHERE id = 'new-owned-source'")
                )
        with pytest.raises(DBAPIError, match="referenced value source"):
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE public.esg_values"))
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM public.esg_values WHERE id = 'new-owned-source'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_migration_046_rejects_repeatable_read_session() -> None:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url or os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("requires explicit disposable PostgreSQL reset opt-in")
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "sds_disposable"
    ):
        pytest.skip("requires the named local disposable PostgreSQL database")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(text("SHOW server_version_num"))
                .scalar_one()
                .startswith("15")
            )
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "045_merge_energy_units_and_value_identity"
            )
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            with engine.connect() as connection:
                connection = connection.execution_options(
                    isolation_level="REPEATABLE READ"
                )
                command.upgrade(
                    _alembic_config(connection), "046_harden_auth_and_value_tenancy"
                )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "045_merge_energy_units_and_value_identity"
            )
    finally:
        engine.dispose()


def test_migration_048_rejects_repeatable_read_session() -> None:
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url or os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("requires explicit disposable PostgreSQL reset opt-in")
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.database != "sds_disposable"
    ):
        pytest.skip("requires the named local disposable PostgreSQL database")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            assert (
                connection.execute(text("SHOW server_version_num"))
                .scalar_one()
                .startswith("15")
            )
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        with engine.connect() as connection:
            command.upgrade(
                _alembic_config(connection), "047_scope_indicator_jobs_to_tenants"
            )
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            with engine.connect() as connection:
                connection = connection.execution_options(
                    isolation_level="REPEATABLE READ"
                )
                command.upgrade(_alembic_config(connection), "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "047_scope_indicator_jobs_to_tenants"
            )
    finally:
        engine.dispose()
