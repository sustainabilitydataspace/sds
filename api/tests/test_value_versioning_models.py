from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from src.database import models as db_models


def _load_migration_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "022_create_value_versioning_tables.py"
    )
    spec = importlib.util.spec_from_file_location(
        "create_value_versioning_tables",
        module_path,
    )
    assert spec and spec.loader, "Migration module spec not found"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_value_versioning_model_tables_and_columns():
    expected = {
        db_models.ValueContext: {
            "tenant_id",
            "context_hash_recipe_version",
            "context_hash",
            "entity_id",
            "reporting_period_id",
            "period_start",
            "period_end",
            "reporting_boundary_id",
            "canonical_concept_id",
            "canonical_uri",
            "source_observation_type",
            "indicator_identifier",
            "standard_release_id",
            "standard_datapoint_id",
            "dimensions_json",
            "scenario_basis",
            "value_kind",
            "expected_unit",
            "expected_currency",
        },
        db_models.ValueRevision: {
            "context_id",
            "tenant_id",
            "revision_number",
            "state",
            "value_kind",
            "canonical_value",
            "numeric_value",
            "unit",
            "currency",
            "source_system",
            "source_record_id",
            "external_key",
            "evidence_hash",
            "calculation_contract_id",
            "formula_contract_hash",
            "input_revision_ids",
            "trace_hash",
            "parent_revision_id",
            "revision_provenance",
        },
        db_models.ValueRevisionEvent: {
            "tenant_id",
            "event_seq",
            "context_event_seq",
            "context_id",
            "revision_id",
            "event_type",
            "from_state",
            "to_state",
            "pointer_moved",
            "event_payload",
        },
        db_models.CurrentValuePointer: {
            "tenant_id",
            "context_id",
            "revision_id",
            "pointer_basis",
        },
        db_models.ReportedValuePointer: {
            "tenant_id",
            "entity_id",
            "reporting_period_id",
            "standard_release_id",
            "report_snapshot_id",
            "context_id",
            "revision_id",
            "pointer_basis",
        },
    }

    for model, expected_columns in expected.items():
        actual_columns = set(model.__table__.columns.keys())
        assert expected_columns.issubset(actual_columns)


def test_value_versioning_migration_chains_after_current_head():
    module = _load_migration_module()

    assert module.revision == "022_create_value_versioning_tables"
    assert module.down_revision == "021_canonical_mapping_package_jobs"


def test_value_revision_graph_uses_tenant_context_composite_foreign_keys():
    def foreign_key_signatures(model):
        signatures = set()
        for constraint in model.__table__.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            elements = list(constraint.elements)
            signatures.add(
                (
                    tuple(element.parent.name for element in elements),
                    elements[0].column.table.name,
                    tuple(element.column.name for element in elements),
                )
            )
        return signatures

    assert (
        ("context_id", "tenant_id"),
        "value_contexts",
        ("id", "tenant_id"),
    ) in foreign_key_signatures(db_models.ValueRevision)
    assert (
        ("parent_revision_id", "context_id", "tenant_id"),
        "value_revisions",
        ("id", "context_id", "tenant_id"),
    ) in foreign_key_signatures(db_models.ValueRevision)

    for model in (
        db_models.ValueRevisionEvent,
        db_models.CurrentValuePointer,
        db_models.ReportedValuePointer,
    ):
        signatures = foreign_key_signatures(model)
        assert (
            ("context_id", "tenant_id"),
            "value_contexts",
            ("id", "tenant_id"),
        ) in signatures
        assert (
            ("revision_id", "context_id", "tenant_id"),
            "value_revisions",
            ("id", "context_id", "tenant_id"),
        ) in signatures


def test_value_revision_graph_rejects_blank_tenants_and_malformed_hashes():
    expected_constraints = {
        db_models.ValueContext: {"ck_value_contexts_tenant_nonblank"},
        db_models.ValueRevision: {"ck_value_revisions_tenant_nonblank"},
        db_models.ValueRevisionEvent: {
            "ck_value_revision_events_tenant_nonblank",
            "ck_value_revision_events_previous_hash_format",
            "ck_value_revision_events_hash_format",
        },
        db_models.CurrentValuePointer: {"ck_current_value_pointers_tenant_nonblank"},
        db_models.ReportedValuePointer: {"ck_reported_value_pointers_tenant_nonblank"},
    }

    for model, expected_names in expected_constraints.items():
        actual_names = {
            constraint.name
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert expected_names.issubset(actual_names)


def test_value_revision_model_restricts_states_to_the_runtime_contract():
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in db_models.ValueRevision.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_value_revisions_state" in constraints
    state_sql = constraints["ck_value_revisions_state"]
    for state in ("draft", "approved", "reported", "voided", "redacted"):
        assert f"'{state}'" in state_sql
