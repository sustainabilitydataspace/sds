"""Tests for Alembic schema bootstrapping helpers."""

from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic.config import Config
from alembic.script import ScriptDirectory
from src.database import migrations as migrations_mod
from src.database.alembic_version import (
    ALEMBIC_VERSION_NUM_LENGTH,
    ensure_alembic_version_table_capacity,
)
from src.database.init_db import init_db_for_engine

API_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = API_ROOT / "alembic" / "versions"
BASELINE_MIGRATION = VERSIONS_DIR / "001_baseline_schema.py"
TYPED_VALUES_MIGRATION = VERSIONS_DIR / "011_allow_typed_esg_values.py"
REVISION_INTEGRITY_MIGRATION = VERSIONS_DIR / "048_harden_value_revision_integrity.py"
ALEMBIC_ENV = API_ROOT / "alembic" / "env.py"


def _script_directory() -> ScriptDirectory:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _load_migration(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_externally_managed_startup_refuses_unversioned_schema_without_writing():
    engine = create_engine("sqlite:///:memory:")
    try:
        with pytest.raises(RuntimeError, match="approved Alembic head"):
            init_db_for_engine(engine, externally_managed=True)
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_externally_managed_startup_requires_exact_nonempty_heads(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    upgrade = MagicMock(side_effect=AssertionError("startup must not migrate"))
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(128))")
            )
            conn.execute(
                text("INSERT INTO alembic_version VALUES ('001_baseline_schema')")
            )
        with pytest.raises(RuntimeError, match="approved Alembic head"):
            init_db_for_engine(engine, externally_managed=True)

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM alembic_version"))
            for head in _script_directory().get_heads():
                conn.execute(
                    text("INSERT INTO alembic_version VALUES (:head)"), {"head": head}
                )
        init_db_for_engine(engine, externally_managed=True)
        upgrade.assert_not_called()
    finally:
        engine.dispose()


def test_read_only_schema_head_cli_refuses_other_actions_or_mismatched_head(
    monkeypatch,
):
    from src.database import session as session_mod

    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(session_mod, "engine", engine)
    upgrade = MagicMock(side_effect=AssertionError("CLI must never migrate"))
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)
    try:
        monkeypatch.setattr(sys, "argv", ["src.database.init_db", "upgrade"])
        with pytest.raises(SystemExit, match="Only the read-only"):
            runpy.run_path(
                str(API_ROOT / "src" / "database" / "init_db.py"), run_name="__main__"
            )
        monkeypatch.setattr(
            sys, "argv", ["src.database.init_db", "--check-head-read-only"]
        )
        with pytest.raises(SystemExit, match="runtime refused"):
            runpy.run_path(
                str(API_ROOT / "src" / "database" / "init_db.py"), run_name="__main__"
            )
        assert inspect(engine).get_table_names() == []

        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(128))")
            )
            for head in _script_directory().get_heads():
                conn.execute(
                    text("INSERT INTO alembic_version VALUES (:head)"), {"head": head}
                )
        runpy.run_path(
            str(API_ROOT / "src" / "database" / "init_db.py"), run_name="__main__"
        )
        upgrade.assert_not_called()
    finally:
        engine.dispose()


def test_unversioned_nonempty_schema_is_never_adopted_automatically(monkeypatch):
    connection = MagicMock()
    stamp = MagicMock()
    upgrade = MagicMock()

    monkeypatch.setattr(migrations_mod, "_alembic_config", lambda conn: "config")
    monkeypatch.setattr(
        migrations_mod, "_has_alembic_version_table", lambda conn: False
    )
    monkeypatch.setattr(
        migrations_mod,
        "_existing_user_tables",
        lambda conn: {"known_legacy_table"},
    )
    monkeypatch.setattr(migrations_mod.command, "stamp", stamp)
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)

    with pytest.raises(RuntimeError, match="unversioned non-empty database"):
        migrations_mod.ensure_database_schema(connection)

    stamp.assert_not_called()
    upgrade.assert_not_called()


def test_partial_unversioned_schema_is_rejected_before_stamp_or_upgrade(monkeypatch):
    connection = MagicMock()
    stamp = MagicMock()
    upgrade = MagicMock()

    monkeypatch.setattr(migrations_mod, "_alembic_config", lambda conn: "config")
    monkeypatch.setattr(
        migrations_mod, "_has_alembic_version_table", lambda conn: False
    )
    monkeypatch.setattr(
        migrations_mod, "_existing_user_tables", lambda conn: {"indicators"}
    )
    monkeypatch.setattr(migrations_mod.command, "stamp", stamp)
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)

    with pytest.raises(RuntimeError, match="unversioned non-empty database"):
        migrations_mod.ensure_database_schema(connection)

    stamp.assert_not_called()
    upgrade.assert_not_called()


def test_fresh_database_upgrades_without_stamping(monkeypatch):
    connection = MagicMock()
    stamp = MagicMock()
    upgrade = MagicMock()

    monkeypatch.setattr(migrations_mod, "_alembic_config", lambda conn: "config")
    monkeypatch.setattr(
        migrations_mod, "_has_alembic_version_table", lambda conn: False
    )
    monkeypatch.setattr(migrations_mod, "_existing_user_tables", lambda conn: set())
    monkeypatch.setattr(migrations_mod.command, "stamp", stamp)
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)

    migrations_mod.ensure_database_schema(connection)

    stamp.assert_not_called()
    upgrade.assert_called_once_with("config", migrations_mod.HEAD_REVISION)


def test_alembic_tracked_database_only_upgrades(monkeypatch):
    connection = MagicMock()
    stamp = MagicMock()
    upgrade = MagicMock()

    monkeypatch.setattr(migrations_mod, "_alembic_config", lambda conn: "config")
    monkeypatch.setattr(migrations_mod, "_has_alembic_version_table", lambda conn: True)
    monkeypatch.setattr(
        migrations_mod, "_existing_user_tables", lambda conn: {"indicators"}
    )
    monkeypatch.setattr(migrations_mod.command, "stamp", stamp)
    monkeypatch.setattr(migrations_mod.command, "upgrade", upgrade)

    migrations_mod.ensure_database_schema(connection)

    stamp.assert_not_called()
    upgrade.assert_called_once_with("config", migrations_mod.HEAD_REVISION)


def test_postgresql_alembic_version_table_is_widened_before_upgrade():
    connection = MagicMock()
    connection.dialect.name = "postgresql"

    ensure_alembic_version_table_capacity(connection)

    statements = "\n".join(
        str(call.args[0]) for call in connection.execute.call_args_list
    )
    assert "CREATE TABLE IF NOT EXISTS alembic_version" in statements
    assert f"VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})" in statements
    assert (
        "ALTER TABLE alembic_version ALTER COLUMN version_num "
        f"TYPE VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})"
    ) in statements
    connection.commit.assert_called_once_with()


def test_non_postgresql_alembic_version_table_capacity_is_noop():
    connection = MagicMock()
    connection.dialect.name = "sqlite"

    ensure_alembic_version_table_capacity(connection)

    connection.execute.assert_not_called()
    connection.commit.assert_not_called()


def test_alembic_config_and_inspection_helpers(monkeypatch):
    connection = MagicMock()
    connection.engine.url.render_as_string.return_value = (
        "postgresql://user:[REDACTED]@host/db"
    )

    config = migrations_mod._alembic_config(connection)

    assert config.get_main_option("script_location") == str(
        migrations_mod.ALEMBIC_SCRIPT_PATH
    )
    assert (
        config.get_main_option("sqlalchemy.url")
        == "postgresql://user:[REDACTED]@host/db"
    )
    assert config.attributes["connection"] is connection

    inspector = MagicMock()
    inspector.has_table.return_value = True
    inspector.get_table_names.return_value = [
        "alembic_version",
        "indicators",
        "values",
    ]
    monkeypatch.setattr(migrations_mod, "inspect", lambda conn: inspector)

    assert migrations_mod._has_alembic_version_table(connection) is True
    assert migrations_mod._existing_user_tables(connection) == {"indicators", "values"}


def test_baseline_does_not_precreate_typed_esg_value_columns():
    text_body = BASELINE_MIGRATION.read_text(encoding="utf-8")
    esg_values_block = text_body.split('"esg_values"', 1)[1].split(
        'op.create_index("ix_esg_values_concept"', 1
    )[0]

    assert '"value_type"' not in esg_values_block
    assert '"text_value"' not in esg_values_block
    assert '"boolean_value"' not in esg_values_block
    assert 'sa.Column("value", sa.Numeric(30, 10), nullable=False)' in esg_values_block


def test_011_adds_typed_value_columns_only_when_missing(monkeypatch):
    migration = _load_migration(TYPED_VALUES_MIGRATION, "migration_011_typed_values")
    existing_columns = {
        "id",
        "concept",
        "entity",
        "period",
        "value",
        "unit",
    }
    added_columns: list[str] = []
    altered_columns: list[str] = []

    class FakeOp:
        @staticmethod
        def add_column(_table_name, column):
            added_columns.append(column.name)
            existing_columns.add(column.name)

        @staticmethod
        def alter_column(_table_name, column_name, **_kwargs):
            altered_columns.append(column_name)

    monkeypatch.setattr(migration, "op", FakeOp)
    monkeypatch.setattr(
        migration, "_column_names", lambda _table: set(existing_columns)
    )

    migration.upgrade()

    assert added_columns == ["value_type", "text_value", "boolean_value"]
    assert altered_columns == ["value"]


def test_011_skips_typed_value_columns_for_partial_legacy_state(monkeypatch):
    migration = _load_migration(TYPED_VALUES_MIGRATION, "migration_011_typed_existing")
    existing_columns = {
        "id",
        "concept",
        "entity",
        "period",
        "value",
        "value_type",
        "text_value",
        "boolean_value",
        "unit",
    }
    added_columns: list[str] = []
    altered_columns: list[str] = []

    class FakeOp:
        @staticmethod
        def add_column(_table_name, column):
            added_columns.append(column.name)

        @staticmethod
        def alter_column(_table_name, column_name, **_kwargs):
            altered_columns.append(column_name)

    monkeypatch.setattr(migration, "op", FakeOp)
    monkeypatch.setattr(
        migration, "_column_names", lambda _table: set(existing_columns)
    )

    migration.upgrade()

    assert added_columns == []
    assert altered_columns == ["value"]


def test_011_downgrade_drops_only_present_typed_value_columns(monkeypatch):
    migration = _load_migration(TYPED_VALUES_MIGRATION, "migration_011_typed_downgrade")
    existing_columns = {"value", "value_type", "boolean_value"}
    dropped_columns: list[str] = []
    altered_columns: list[str] = []
    executed_sql: list[str] = []

    class FakeOp:
        @staticmethod
        def execute(statement):
            executed_sql.append(str(statement))

        @staticmethod
        def alter_column(_table_name, column_name, **_kwargs):
            altered_columns.append(column_name)

        @staticmethod
        def drop_column(_table_name, column_name):
            dropped_columns.append(column_name)
            existing_columns.discard(column_name)

    monkeypatch.setattr(migration, "op", FakeOp)
    monkeypatch.setattr(
        migration, "_column_names", lambda _table: set(existing_columns)
    )

    migration.downgrade()

    assert executed_sql == ["DELETE FROM esg_values WHERE value IS NULL"]
    assert altered_columns == ["value"]
    assert dropped_columns == ["boolean_value", "value_type"]


def test_alembic_env_preserves_postgres_advisory_lock():
    env_text = ALEMBIC_ENV.read_text(encoding="utf-8")

    assert "MIGRATION_LOCK_KEY" in env_text
    assert "pg_advisory_lock" in env_text
    assert "pg_advisory_unlock" in env_text
    assert "ensure_alembic_version_table_capacity(connection)" in env_text


def test_fresh_postgres_upgrade_to_head_when_disposable_url_is_provided():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

        with engine.connect() as connection:
            config = migrations_mod._alembic_config(connection)
            expected_head = ScriptDirectory.from_config(config).get_current_head()

        init_db_for_engine(engine)

        with engine.connect() as connection:
            actual_head = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            columns = {
                column["name"]
                for column in inspect(connection).get_columns("esg_values")
            }

        assert actual_head == expected_head
        assert {"value_type", "text_value", "boolean_value"}.issubset(columns)
    finally:
        engine.dispose()


def test_alembic_migration_graph_keeps_demo_cleanup_evs_merge_linear():
    script = _script_directory()

    assert script.get_heads() == ["048_harden_value_revision_integrity"]
    revision_integrity = script.get_revision("048_harden_value_revision_integrity")
    assert revision_integrity.down_revision == "047_scope_indicator_jobs_to_tenants"
    indicator_jobs = script.get_revision("047_scope_indicator_jobs_to_tenants")
    assert indicator_jobs.down_revision == "046_harden_auth_and_value_tenancy"
    security_revision = script.get_revision("046_harden_auth_and_value_tenancy")
    assert (
        security_revision.down_revision == "045_merge_energy_units_and_value_identity"
    )
    latest_merge = script.get_revision("045_merge_energy_units_and_value_identity")
    assert set(latest_merge.down_revision) == {
        "043_correct_esrs_e1_5_energy_units",
        "044_widen_calculation_relation_to_parent",
    }
    energy_revision = script.get_revision("043_correct_esrs_e1_5_energy_units")
    assert energy_revision.down_revision == "042_add_localized_text"
    merge_revision = script.get_revision("038_merge_demo_cleanup_with_evs")
    assert set(merge_revision.down_revision) == {
        "036_remove_persisted_demo_indicators",
        "037_add_evs_restatement_tenant_private",
    }
    closed_enum_revision = script.get_revision("038_add_closed_enum_exemptions")
    assert closed_enum_revision.down_revision == "038_merge_demo_cleanup_with_evs"
    varch_seed_revision = script.get_revision(
        "039_seed_varch0_profiles_and_waste_plastic"
    )
    assert varch_seed_revision.down_revision == "038_add_closed_enum_exemptions"
    defer_fk_revision = script.get_revision("040_defer_successor_pointer_fks")
    assert (
        defer_fk_revision.down_revision == "039_seed_varch0_profiles_and_waste_plastic"
    )
    legacy_revision = script.get_revision("041_add_legacy_value_classifications")
    assert legacy_revision.down_revision == "040_defer_successor_pointer_fks"
    localized_revision = script.get_revision("042_add_localized_text")
    assert localized_revision.down_revision == "041_add_legacy_value_classifications"
    canonical_context_revision = script.get_revision(
        "043_add_canonical_value_context_identity"
    )
    assert canonical_context_revision.down_revision == "042_add_localized_text"
    calculation_relation_revision = script.get_revision(
        "044_widen_calculation_relation_to_parent"
    )
    assert (
        calculation_relation_revision.down_revision
        == "043_add_canonical_value_context_identity"
    )
    successor_fk_revision = script.get_revision("040_defer_successor_pointer_fks")
    assert (
        successor_fk_revision.down_revision
        == "039_seed_varch0_profiles_and_waste_plastic"
    )


def test_revision_integrity_parent_walk_casts_ids_before_array_append() -> None:
    migration_text = REVISION_INTEGRITY_MIGRATION.read_text(encoding="utf-8")

    assert "parent_walk.path || parent.id::text" in migration_text
    assert "parent_walk.path || parent.id," not in migration_text
