from __future__ import annotations

import importlib.util
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.services.value_revision_store import _build_event_hash
from src.services.value_versioning import VALUE_STATE_TRANSITIONS

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "048_harden_value_revision_integrity.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "revision_integrity_048", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_integrity_migration_adds_composite_graph_constraints() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision = "048_harden_value_revision_integrity"' in source
    assert 'down_revision = "047_scope_indicator_jobs_to_tenants"' in source
    for constraint_name in (
        "uq_value_contexts_id_tenant",
        "uq_value_revisions_id_context_tenant",
        "fk_value_revisions_context_tenant",
        "fk_value_revisions_parent_context_tenant",
        "fk_value_revision_events_context_tenant",
        "fk_value_revision_events_revision_context_tenant",
        "fk_current_value_pointers_context_tenant",
        "fk_current_value_pointers_revision_context_tenant",
        "fk_reported_value_pointers_context_tenant",
        "fk_reported_value_pointers_revision_context_tenant",
        "ck_value_contexts_tenant_nonblank",
        "ck_value_revisions_tenant_nonblank",
        "ck_value_revision_events_tenant_nonblank",
        "ck_current_value_pointers_tenant_nonblank",
        "ck_reported_value_pointers_tenant_nonblank",
    ):
        assert constraint_name in source


def test_migration_rejects_undated_current_contexts_before_schema_change() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    preflight = source.split("def _preflight(", 1)[1].split("def ", 1)[0]
    assert "current context without a period date" in preflight
    assert "context.period_start IS NULL" in preflight
    assert "context.period_end IS NULL" in preflight
    assert "public.current_value_pointers" in preflight
    assert "_CURRENT_POINTER_STATES" in preflight
    current_head = source.split(
        "CREATE OR REPLACE FUNCTION public.validate_current_value_pointer_head()", 1
    )[1].split("END;", 1)[0]
    assert "context.period_start IS NULL" in current_head
    assert "context.period_end IS NULL" in current_head
    assert "BEFORE UPDATE OR DELETE ON value_contexts" in source


def test_revision_integrity_migration_backfills_and_protects_event_chain() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'sa.Column("previous_event_hash", sa.String(length=64)' in source
    assert 'sa.Column("event_hash", sa.String(length=64)' in source
    assert "sds-value-revision-event-chain-v1" in source
    assert "trg_value_revision_events_append_only" in source
    assert "trg_value_revision_events_chain_insert" in source
    assert "trg_reported_value_pointers_immutable" in source
    assert "trg_value_revisions_immutable_payload" in source
    assert "BEFORE UPDATE OR DELETE ON value_revision_events" in source
    assert "BEFORE INSERT ON public.value_revision_events" in source
    assert "pg_advisory_xact_lock" in source
    assert "BEFORE UPDATE OR DELETE ON public.reported_value_pointers" in source
    assert "BEFORE UPDATE OR DELETE ON value_revisions" in source
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" not in source
    assert "canonicalize_sds_jsonb" in source
    assert "event_payload_canonical" in source
    assert "occurred_at" in source
    assert 'YYYY-MM-DD"T"HH24:MI:SS.US' in source
    assert "calculate_sds_value_revision_event_hash" in source
    assert "legacy value revision event hash validation failed" in source
    assert "expected_event_hash" in source
    assert "sha256(" in source and "convert_to(" in source
    assert "NEW.event_hash IS DISTINCT FROM expected_event_hash" in source
    assert "latest_context_event_seq" in source
    assert "invalid first value revision context event position" in source
    assert "value revision creation event must be first" in source
    assert "value revision event requires an existing creation event" in source
    assert "invalid value revision context event continuation" in source


def test_revision_integrity_migration_validates_input_revision_graph() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "trg_value_revisions_validate_inputs" in source
    assert "BEFORE INSERT ON value_revisions" in source
    assert "jsonb_array_elements" in source
    assert "duplicate input revision references" in source
    assert "input revision tenant mismatch or missing revision" in source
    assert "input_revision.context_id IS DISTINCT FROM revision.context_id" in source
    assert "input_revision.context_id IS DISTINCT FROM NEW.context_id" in source


def test_revision_integrity_migration_rejects_parent_cycles() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "WITH RECURSIVE parent_walk" in source
    assert "legacy value revision parent graph contains a cycle" in source
    assert "trg_value_revisions_validate_parent_graph" in source
    assert "AFTER INSERT ON value_revisions" in source
    assert "REFERENCING NEW TABLE AS new_revisions" in source
    assert "value revision parent graph cannot contain a cycle" in source


def test_revision_integrity_migration_makes_context_identity_immutable() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "trg_value_contexts_immutable" in source
    assert "BEFORE UPDATE OR DELETE ON value_contexts" in source
    assert "value contexts are immutable identities" in source


def test_revision_integrity_migration_binds_reported_pointer_identity_to_context() -> (
    None
):
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "pointer.entity_id IS DISTINCT FROM context.entity_id" in source
    assert (
        "pointer.reporting_period_id IS DISTINCT FROM context.reporting_period_id"
        in source
    )
    assert (
        "pointer.standard_release_id IS DISTINCT FROM context.standard_release_id"
        in source
    )
    assert "trg_reported_value_pointers_validate_identity" in source
    assert "BEFORE INSERT ON public.reported_value_pointers" in source
    assert "reported value pointer identity does not match its context" in source


def test_revision_integrity_migration_guards_state_and_current_pointer_mutations() -> (
    None
):
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "ck_value_revisions_state" in source
    assert "trg_value_revisions_validate_state_transition" in source
    assert "invalid value revision state transition" in source
    assert "trg_value_revisions_record_state_effect" in source
    assert "trg_value_revisions_record_creation_effect" in source
    assert "trg_value_revision_events_require_effects" in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "value revision state transition requires an exact event effect" in source
    assert "value revision creation requires an exact event effect" in source
    assert "trg_current_value_pointers_record_effect" in source
    assert "current value pointer must target an eligible revision state" in source
    assert "current value pointer mutation requires an exact event effect" in source


def test_revision_integrity_uses_one_shot_effect_receipts_not_tuple_xmin() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "value_revision_event_effects" in source
    assert "pk_value_revision_event_effects" in source
    assert "event_id, effect_kind" in source
    assert "revision_created" in source
    assert "state_transition" in source
    assert "current_pointer" in source
    assert "reported_pointer" in source
    assert "event.xmin" not in source
    assert "pg_current_xact_id" not in source
    assert "require_value_revision_event_effects" in source
    assert "value revision event effects are immutable" in source
    assert (
        "value revision event effects can only be emitted by protected triggers"
        in source
    )
    assert "pg_trigger_depth() < 2" in source
    assert "pg_my_temp_schema() <> 0" in source
    assert "validate_value_revision_event_effect" in source
    assert "trg_value_revision_event_effects_validate" in source
    assert "event effect does not match the durable mutation" in source
    assert source.index(
        "INSERT INTO public.value_revision_event_effects"
    ) < source.index("trg_value_revision_event_effects_guard_insert")
    assert "current value pointer identity is immutable" in source
    assert (
        "cannot remove a current pointer while an eligible revision remains" in source
    )
    assert "current pointer cannot regress behind a newer eligible revision" in source
    assert "old_revision_state" in source
    assert "receipt_revision_id := old_revision_id" in source


def test_revision_integrity_migration_enforces_runtime_transition_matrix() -> None:
    migration = _load_migration_module()
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    expected = {
        (from_state, to_state)
        for from_state, to_states in VALUE_STATE_TRANSITIONS.items()
        for to_state in to_states
    }

    assert migration._ALLOWED_STATE_TRANSITIONS == expected
    assert "legacy revision history contains a forbidden state transition" in source
    assert "_transition_predicate_sql()" in source
    assert "invalid value revision state transition" in source


def test_revision_integrity_trigger_queries_are_public_schema_bound() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for relation in (
        "value_contexts",
        "value_revisions",
        "value_revision_events",
        "current_value_pointers",
        "reported_value_pointers",
        "value_revision_event_effects",
    ):
        assert f"public.{relation}" in source
    assert "SET search_path = pg_catalog, public" in source


def test_revision_integrity_preflight_rejects_incomplete_legacy_history() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "legacy revision history lacks exactly one creation event" in source
    assert "legacy revision creation event is not first" in source
    assert "legacy non-creation event has a null source state" in source
    assert "value revision event source state is required after creation" in source
    assert "legacy revision event state history is discontinuous" in source
    assert "legacy current pointers lack a bound pointer event" in source
    assert "legacy current pointer is not the highest eligible revision" in source
    assert "legacy reported pointers lack a bound snapshot event" in source
    assert "legacy reported pointer event lacks a durable pointer" in source
    assert "reported pointer event must move a reported pointer" in source
    assert "reported pointer event cannot transition revision state" in source
    assert "event.event_type <> 'reported_pointer_created'" in source
    assert "legacy current pointer history resolves to a missing pointer" in source


def test_revision_integrity_backfill_uses_bounded_keyset_batches() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "LIMIT 500" in source
    assert "stream_results=True" not in source
    assert "last_tenant_id" in source
    assert "last_event_seq" in source


def test_revision_integrity_migration_fails_before_constraining_inconsistent_rows() -> (
    None
):
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    for relation in (
        "value_revisions",
        "value_revision_events",
        "current_value_pointers",
        "reported_value_pointers",
    ):
        assert f"legacy {relation} rows violate tenant/context integrity" in source
    assert "legacy tenant event sequence is not contiguous" in source
    assert "legacy context event sequence is not contiguous" in source
    assert "row_number() OVER (PARTITION BY tenant_id" in source
    assert "row_number() OVER (PARTITION BY context_id" in source


def test_revision_integrity_migration_locks_graph_before_preflight() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    lock_marker = "IN SHARE ROW EXCLUSIVE MODE"
    search_path_marker = "SET LOCAL search_path = pg_catalog, public, pg_temp"
    assert search_path_marker in source
    assert source.index(search_path_marker) < source.index("LOCK TABLE")
    assert 'schema="public"' in source
    assert "CREATE OR REPLACE FUNCTION public." in source
    assert "CREATE OR REPLACE FUNCTION canonicalize_sds_jsonb" not in source
    assert lock_marker in source
    assert source.index(lock_marker) < source.index("_preflight(bind)")
    for table_name in (
        "value_contexts",
        "value_revisions",
        "value_revision_events",
        "current_value_pointers",
        "reported_value_pointers",
    ):
        assert (
            table_name in source[source.index("LOCK TABLE") : source.index(lock_marker)]
        )


@pytest.mark.parametrize(
    "event_payload",
    [
        None,
        {"amount": Decimal("1.2300")},
        {"reading": 1.23e-5, "negative_zero": -0.0},
    ],
)
def test_migration_backfill_uses_the_runtime_event_hash_contract(event_payload) -> None:
    migration = _load_migration_module()
    event_payload_canonical = migration._canonical_json_value(event_payload)
    occurred_at = datetime(2026, 1, 2, 3, 4, 5, 6789)
    row = {
        "id": "event-1",
        "tenant_id": "tenant-a",
        "event_seq": 7,
        "context_event_seq": 3,
        "context_id": 11,
        "revision_id": "revision-1",
        "event_type": "revision_created",
        "from_state": None,
        "to_state": "approved",
        "pointer_moved": True,
        "event_payload_text": event_payload_canonical,
        "idempotency_key": "idem-1",
        "occurred_at": occurred_at,
        "occurred_by": "tester",
    }

    assert migration._event_hash(row, "a" * 64) == _build_event_hash(
        event_id=row["id"],
        tenant_id=row["tenant_id"],
        event_seq=row["event_seq"],
        context_event_seq=row["context_event_seq"],
        context_id=row["context_id"],
        revision_id=row["revision_id"],
        event_type=row["event_type"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        pointer_moved=row["pointer_moved"],
        event_payload=event_payload,
        event_payload_canonical=event_payload_canonical,
        idempotency_key=row["idempotency_key"],
        occurred_at=occurred_at,
        occurred_by=row["occurred_by"],
        previous_event_hash="a" * 64,
    )
