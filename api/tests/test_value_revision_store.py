from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import src.services.value_revision_store as revision_store_module
from src.api.models import ValueRevisionEventResponse
from src.database.models import (
    CurrentValuePointer,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.value_revision_store import (
    ValueRevisionInput,
    ValueRevisionStore,
    _canonical_boolean,
    _canonical_json_value,
    _normalized_state,
    _value_columns,
    context_to_payload,
    event_to_payload,
    revision_to_payload,
)
from src.services.value_versioning import (
    CONTEXT_HASH_RECIPE_VERSION_V2,
    SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
    SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
    ValueContextIdentity,
    ValueVersioningError,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _session():
    engine = create_engine("sqlite:///:memory:")
    for table in (
        ValueContext.__table__,
        ValueRevision.__table__,
        ValueRevisionEvent.__table__,
        CurrentValuePointer.__table__,
        ReportedValuePointer.__table__,
    ):
        table.create(engine)
    return sessionmaker(bind=engine)()


def _identity() -> ValueContextIdentity:
    return ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="entity-1",
        reporting_period_id="FY2026",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id="indicator-1",
        indicator_identifier="urn:sds:reg:esrs:e1_6_07",
        standard_release_id="ESRS_SET1_2023_12_22",
        standard_datapoint_id="E1-6_07",
        dimensions={"scope": "scope1"},
        expected_unit="tCO2e",
        expected_currency=None,
        value_kind="numeric",
    )


def test_undated_context_cannot_become_current_via_single_append() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    undated = replace(_identity(), period_start=None, period_end=None)
    with pytest.raises(
        ValueVersioningError, match="current value requires a period date"
    ):
        store.append_revision(
            ValueRevisionInput(
                context_identity=undated,
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            )
        )
    assert db.query(ValueContext).count() == 0
    assert db.query(ValueRevision).count() == 0


def test_undated_draft_cannot_be_promoted_to_current() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    undated = replace(_identity(), period_start=None, period_end=None)
    draft = store.append_revision(
        ValueRevisionInput(
            context_identity=undated,
            value=Decimal("1"),
            value_kind="numeric",
            state="draft",
        )
    )
    store.transition_revision(revision_id=draft.revision.id, to_state="submitted")
    with pytest.raises(
        ValueVersioningError, match="current value requires a period date"
    ):
        store.transition_revision(revision_id=draft.revision.id, to_state="validated")
    assert db.query(CurrentValuePointer).count() == 0
    assert db.query(ValueRevision).one().state == "submitted"


def test_bulk_undated_current_rejected_before_any_context_is_inserted() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    with pytest.raises(
        ValueVersioningError, match="current value requires a period date"
    ):
        store.append_revisions_bulk(
            [
                ValueRevisionInput(
                    context_identity=replace(
                        _identity(), period_start=None, period_end=None
                    ),
                    value=Decimal("1"),
                    value_kind="numeric",
                    state="approved",
                )
            ]
        )
    assert db.query(ValueContext).count() == 0


def test_bulk_append_lock_uses_deterministic_postgres_tenant_keys() -> None:
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )
    store = ValueRevisionStore(db)

    store._acquire_bulk_append_locks({"tenant-b", "tenant-a"})

    expected_keys = []
    for tenant_id in ("tenant-a", "tenant-b"):
        digest = hashlib.sha256(
            f"sds:value_revision_bulk:{tenant_id}".encode("utf-8")
        ).digest()
        expected_keys.append(int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF)

    assert [call.args[1]["key"] for call in db.execute.call_args_list] == expected_keys
    assert "pg_advisory_xact_lock" in str(db.execute.call_args_list[0].args[0])


def test_bulk_append_lock_is_noop_outside_postgres() -> None:
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    store = ValueRevisionStore(db)

    store._acquire_bulk_append_locks({"tenant-a"})

    db.execute.assert_not_called()


def test_bulk_append_takes_lock_before_context_resolution() -> None:
    source = inspect.getsource(ValueRevisionStore.append_revisions_bulk)

    assert source.index("self._acquire_bulk_append_locks(tenant_ids)") < source.index(
        "tenant_hash_pairs ="
    )


def test_single_append_takes_same_tenant_lock_before_context_resolution() -> None:
    source = inspect.getsource(ValueRevisionStore.append_revision)

    assert source.index(
        "self._acquire_bulk_append_locks({identity.tenant_id})"
    ) < source.index("context = self._get_or_create_context(")


def test_require_context_can_lock_row_for_transition_pointer_moves() -> None:
    db = MagicMock()
    query = MagicMock()
    context = object()
    db.query.return_value = query
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = context
    store = ValueRevisionStore(db)

    assert store._require_context(42, lock=True) is context
    query.populate_existing.assert_called_once()
    query.with_for_update.assert_called_once()


def test_transition_locks_revision_before_validating_state() -> None:
    db = MagicMock()
    query = MagicMock()
    revision = SimpleNamespace(id="rev-1", state="submitted", context_id=7)
    db.query.return_value = query
    query.filter.return_value = query
    query.populate_existing.return_value = query
    query.with_for_update.return_value = query
    query.first.return_value = revision
    store = ValueRevisionStore(db)

    assert store._require_revision("rev-1", lock=True) is revision
    query.populate_existing.assert_called_once()
    query.with_for_update.assert_called_once()
    source = inspect.getsource(ValueRevisionStore.transition_revision)
    tenant_lookup = source.index("self._revision_tenant_id(revision_id)")
    tenant_lock = source.index("self._acquire_bulk_append_locks({tenant_id})")
    revision_lock = source.index("self._require_revision(revision_id, lock=True)")
    assert tenant_lookup < tenant_lock < revision_lock


def test_reported_pointer_uses_tenant_writer_lock_before_row_locks() -> None:
    source = inspect.getsource(ValueRevisionStore.create_reported_pointer)

    tenant_lookup = source.index("initial_context = self._require_context(context_id)")
    tenant_lock = source.index(
        "self._acquire_bulk_append_locks({initial_context.tenant_id})"
    )
    context_lock = source.index(
        "context = self._require_context(context_id, lock=True)"
    )
    revision_lock = source.index(
        "revision = self._require_revision(revision_id, lock=True)"
    )
    assert tenant_lookup < tenant_lock < context_lock < revision_lock


def test_latest_current_pointers_by_context_keeps_last_pointer() -> None:
    first = CurrentValuePointer(
        context_id=7,
        tenant_id="tenant-a",
        revision_id="rev-1",
        updated_by="tester",
    )
    second = CurrentValuePointer(
        context_id=7,
        tenant_id="tenant-a",
        revision_id="rev-2",
        updated_by="tester",
    )
    other = CurrentValuePointer(
        context_id=8,
        tenant_id="tenant-a",
        revision_id="rev-3",
        updated_by="tester",
    )

    collapsed = ValueRevisionStore._latest_current_pointers_by_context(
        [first, second, other]
    )

    assert [(pointer.context_id, pointer.revision_id) for pointer in collapsed] == [
        (7, "rev-2"),
        (8, "rev-3"),
    ]


def test_bulk_append_collapses_duplicate_current_pointers_for_same_context() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    captured: list[CurrentValuePointer] = []
    store._bulk_upsert_current_pointers = captured.extend  # type: ignore[method-assign]
    identity = _identity()

    results = store.append_revisions_bulk(
        [
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal("1"),
                value_kind="numeric",
                unit="tCO2e",
                state="approved",
                source_system="manual",
                external_key="manual:1",
                created_by="tester",
            ),
            ValueRevisionInput(
                context_identity=identity,
                value=Decimal("2"),
                value_kind="numeric",
                unit="tCO2e",
                state="approved",
                source_system="manual",
                external_key="manual:2",
                created_by="tester",
            ),
        ],
        commit=False,
        refresh=False,
    )

    assert len(captured) == 1
    assert captured[0].context_id == results[-1].context.id
    assert captured[0].revision_id == results[-1].revision.id
    assert results[0].event.pointer_moved is False
    assert results[0].current_pointer is None
    assert results[-1].event.pointer_moved is True
    assert results[-1].current_pointer is captured[0]


def test_bulk_append_extends_event_hash_chain_in_input_order() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    results = store.append_revisions_bulk(
        [
            ValueRevisionInput(
                context_identity=_identity(),
                value=Decimal("1"),
                value_kind="numeric",
                state="approved",
            ),
            ValueRevisionInput(
                context_identity=_identity(),
                value=Decimal("2"),
                value_kind="numeric",
                state="approved",
            ),
        ],
        refresh=False,
    )

    assert results[0].event.previous_event_hash is None
    assert results[1].event.previous_event_hash == results[0].event.event_hash
    assert all(len(result.event.event_hash) == 64 for result in results)


def test_append_revision_creates_context_event_and_current_pointer() -> None:
    db = _session()
    store = ValueRevisionStore(db)

    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("001.2300"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
            external_key="manual:1",
            created_by="tester",
        )
    )

    assert result.context.context_hash == result.context_hash
    assert result.revision.revision_number == 1
    assert result.revision.canonical_value == "1.23"
    assert result.event.event_seq == 1
    assert result.event.context_event_seq == 1
    assert result.event.to_state == "approved"
    assert result.current_pointer is not None
    assert result.current_pointer.revision_id == result.revision.id
    assert db.query(ValueContext).count() == 1
    assert db.query(ValueRevision).count() == 1
    assert db.query(ValueRevisionEvent).count() == 1
    assert db.query(CurrentValuePointer).count() == 1


def test_revision_events_form_a_tenant_hash_chain() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            state="corrected",
            parent_revision_id=first.revision.id,
        )
    )

    assert first.event.previous_event_hash is None
    assert len(first.event.event_hash) == 64
    assert second.event.previous_event_hash == first.event.event_hash
    assert len(second.event.event_hash) == 64
    assert second.event.event_hash != first.event.event_hash


def test_event_hash_model_constraint_rejects_non_hex_lowercase_values() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )

    result.event.event_hash = "g" * 64
    with pytest.raises(IntegrityError):
        db.commit()


def test_event_chain_verifier_detects_payload_tampering_and_reordering() -> None:
    assert hasattr(revision_store_module, "verify_value_revision_event_chain")
    verify_chain = revision_store_module.verify_value_revision_event_chain
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            state="approved",
        )
    )
    events = [first.event, second.event]

    assert verify_chain(events, tenant_id="tenant-a") is True
    assert verify_chain(list(reversed(events)), tenant_id="tenant-a") is False
    second.event.event_payload = {"source": "tampered"}
    assert verify_chain(events, tenant_id="tenant-a") is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tenant_id", "tenant-b"),
        ("event_seq", 2),
        ("previous_event_hash", "0" * 64),
        ("event_hash", "f" * 64),
    ],
)
def test_event_chain_verifier_refuses_forged_authority_or_hash_fields(
    field, replacement
) -> None:
    db = _session()
    store = ValueRevisionStore(db)
    created = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    assert revision_store_module.verify_value_revision_event_chain(
        [created.event], tenant_id="tenant-a"
    )
    setattr(created.event, field, replacement)
    assert not revision_store_module.verify_value_revision_event_chain(
        [created.event], tenant_id="tenant-a"
    )


def test_event_chain_verifier_rejects_in_place_nested_payload_mutation() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    created = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    store.transition_revision(
        revision_id=created.revision.id,
        to_state="locked",
        event_payload={"audit": {"source": "original"}},
    )
    events = store.list_events(tenant_id="tenant-a")
    assert revision_store_module.verify_value_revision_event_chain(
        events, tenant_id="tenant-a"
    )
    events[-1].event_payload["audit"]["source"] = "tampered"
    assert (
        revision_store_module.verify_value_revision_event_chain(
            events, tenant_id="tenant-a"
        )
        is False
    )


def test_event_hash_canonical_json_normalizes_fractional_numbers() -> None:
    assert (
        _canonical_json_value(
            {
                "reading": 1.23e-5,
                "negative_zero": -0.0,
                "precise": Decimal("1.2300"),
            }
        )
        == '{"negative_zero":0,"precise":1.23,"reading":0.0000123}'
    )
    assert (
        _canonical_json_value(Decimal("0.123456789012345678901234567890123456789"))
        == "0.123456789012345678901234567890123456789"
    )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({1: "nontext key"}, TypeError),
        (Decimal("NaN"), ValueError),
        (object(), TypeError),
    ],
)
def test_event_hash_canonical_json_rejects_ambiguous_payloads(payload, error) -> None:
    with pytest.raises(error):
        _canonical_json_value(payload)


def test_event_hash_binds_canonical_payload_and_occurrence_time() -> None:
    common = {
        "event_id": "event-1",
        "tenant_id": "tenant-a",
        "event_seq": 1,
        "context_event_seq": 1,
        "context_id": 1,
        "revision_id": "revision-1",
        "event_type": "revision_created",
        "from_state": None,
        "to_state": "approved",
        "pointer_moved": True,
        "event_payload": {"n": Decimal("0.123456789012345678901234567890")},
        "event_payload_canonical": ('{"n":0.12345678901234567890123456789}'),
        "idempotency_key": None,
        "occurred_by": "tester",
        "previous_event_hash": None,
    }
    first = revision_store_module._build_event_hash(
        **common,
        occurred_at=datetime(2026, 1, 1, 0, 0, 0, 1),
    )
    second = revision_store_module._build_event_hash(
        **common,
        occurred_at=datetime(2026, 1, 1, 0, 0, 0, 2),
    )

    assert first != second


def test_transition_normalizes_high_precision_decimal_event_payload() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    created = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )

    event = store.transition_revision(
        revision_id=created.revision.id,
        to_state="locked",
        event_payload={"n": Decimal("0.123456789012345678901234567890123456789")},
    )

    assert event.event_payload == {"n": "0.123456789012345678901234567890123456789"}
    assert (
        revision_store_module.verify_value_revision_event_chain(
            store.list_events(tenant_id="tenant-a"),
            tenant_id="tenant-a",
        )
        is True
    )


def test_bulk_append_rejects_mixed_revision_states() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    inputs = [
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="draft",
        ),
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            state="approved",
        ),
    ]

    with pytest.raises(ValueVersioningError, match="same state"):
        store.append_revisions_bulk(inputs)

    assert db.query(ValueRevision).count() == 0


def test_bulk_append_rejects_cross_tenant_revision_references() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    other_identity = ValueContextIdentity(
        **{**_identity().__dict__, "tenant_id": "tenant-b"}
    )

    with pytest.raises(ValueVersioningError, match="same tenant"):
        store.append_revisions_bulk(
            [
                ValueRevisionInput(
                    context_identity=other_identity,
                    value=Decimal("2"),
                    value_kind="numeric",
                    state="approved",
                    input_revision_ids=[first.revision.id],
                )
            ]
        )

    assert (
        db.query(ValueRevision).filter(ValueRevision.tenant_id == "tenant-b").count()
        == 0
    )


def test_append_revision_persists_canonical_operational_context_identity() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    canonical_uri = "syg:TotalEnergyConsumptionWithinOrganization"
    identity = ValueContextIdentity(
        tenant_id="tenant-a",
        entity_id="nh_group",
        reporting_period_id="FY2024",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        period_close_date=None,
        period_type="annual",
        reporting_boundary_id="operational_control",
        sds_indicator_id=None,
        indicator_identifier=canonical_uri,
        standard_release_id=SDS_CANONICAL_OPERATIONAL_RELEASE_ID,
        standard_datapoint_id=canonical_uri,
        dimensions={},
        expected_unit="MWh",
        expected_currency=None,
        value_kind="numeric",
        canonical_concept_id=101,
        canonical_uri=canonical_uri,
        source_observation_type=SOURCE_OBSERVATION_CANONICAL_OPERATIONAL,
        context_hash_recipe_version=CONTEXT_HASH_RECIPE_VERSION_V2,
    )

    result = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("832.964"),
            value_kind="numeric",
            unit="MWh",
            state="approved",
            source_system="sds_values",
            external_key="nordhaven:uc01:fy2024:total-energy",
            created_by="tester",
        )
    )

    assert result.context.context_hash_recipe_version == CONTEXT_HASH_RECIPE_VERSION_V2
    assert result.context.indicator_identifier == canonical_uri
    assert result.context.standard_release_id == SDS_CANONICAL_OPERATIONAL_RELEASE_ID
    assert result.context.standard_datapoint_id == canonical_uri
    assert result.context.canonical_concept_id == 101
    assert result.context.canonical_uri == canonical_uri
    assert result.context.source_observation_type == (
        SOURCE_OBSERVATION_CANONICAL_OPERATIONAL
    )
    assert result.context.identity_payload["identity_fields"]["canonical_uri"] == (
        canonical_uri
    )


def test_append_second_revision_keeps_context_and_moves_pointer() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    identity = _identity()

    first = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
            external_key="manual:1",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("2"),
            value_kind="numeric",
            unit="tCO2e",
            state="corrected",
            source_system="manual",
            external_key="manual:1",
            parent_revision_id=first.revision.id,
            revision_provenance="correction",
        )
    )

    pointer = db.query(CurrentValuePointer).one()

    assert second.context.id == first.context.id
    assert second.revision.revision_number == 2
    assert second.event.event_seq == 2
    assert second.event.context_event_seq == 2
    assert pointer.revision_id == second.revision.id
    assert db.query(ValueContext).count() == 1
    assert db.query(ValueRevision).count() == 2


@pytest.mark.parametrize(
    "reference_field", ["parent_revision_id", "input_revision_ids"]
)
def test_append_rejects_cross_tenant_revision_references(reference_field: str) -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    other_identity = ValueContextIdentity(
        **{**_identity().__dict__, "tenant_id": "tenant-b"}
    )
    references = (
        {"parent_revision_id": first.revision.id}
        if reference_field == "parent_revision_id"
        else {"input_revision_ids": [first.revision.id]}
    )

    with pytest.raises(ValueVersioningError, match="same tenant"):
        store.append_revision(
            ValueRevisionInput(
                context_identity=other_identity,
                value=Decimal("2"),
                value_kind="numeric",
                state="corrected",
                **references,
            )
        )

    assert (
        db.query(ValueRevision).filter(ValueRevision.tenant_id == "tenant-b").count()
        == 0
    )


def test_append_rejects_duplicate_input_revision_references() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )

    with pytest.raises(ValueVersioningError, match="duplicate"):
        store.append_revision(
            ValueRevisionInput(
                context_identity=_identity(),
                value=Decimal("2"),
                value_kind="numeric",
                state="corrected",
                input_revision_ids=[first.revision.id, first.revision.id],
            )
        )


def test_append_rejects_cross_context_input_revision_references() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    other_identity = ValueContextIdentity(
        **{**_identity().__dict__, "entity_id": "entity-b"}
    )

    with pytest.raises(ValueVersioningError, match="same value context"):
        store.append_revision(
            ValueRevisionInput(
                context_identity=other_identity,
                value=Decimal("2"),
                value_kind="numeric",
                state="corrected",
                input_revision_ids=[first.revision.id],
            )
        )


def test_draft_revision_does_not_move_current_pointer() -> None:
    db = _session()
    store = ValueRevisionStore(db)

    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="draft",
            source_system="manual",
        )
    )

    assert result.current_pointer is None
    assert db.query(CurrentValuePointer).count() == 0


def test_voiding_current_revision_falls_back_to_latest_eligible_revision() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    identity = _identity()
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
            source_system="manual",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("2"),
            value_kind="numeric",
            state="corrected",
            source_system="manual",
            parent_revision_id=first.revision.id,
        )
    )

    event = store.transition_revision(
        revision_id=second.revision.id,
        to_state="redacted",
        occurred_by="tester",
    )

    pointer = db.query(CurrentValuePointer).one()
    assert pointer.revision_id == first.revision.id
    assert event.pointer_moved is True


def test_late_transition_of_older_revision_cannot_regress_pointer() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    identity = _identity()
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("1"),
            value_kind="numeric",
            state="submitted",
            source_system="manual",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=identity,
            value=Decimal("2"),
            value_kind="numeric",
            state="approved",
            source_system="manual",
            parent_revision_id=first.revision.id,
        )
    )

    event = store.transition_revision(
        revision_id=first.revision.id,
        to_state="validated",
        occurred_by="tester",
    )

    pointer = db.query(CurrentValuePointer).one()
    assert pointer.revision_id == second.revision.id
    assert event.pointer_moved is False


def test_transition_revision_blocks_invalid_state_move() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="reported",
            source_system="manual",
        )
    )

    with pytest.raises(ValueVersioningError):
        store.transition_revision(
            revision_id=result.revision.id,
            to_state="draft",
            event_type="invalid_transition",
            occurred_by="tester",
        )


def test_reported_pointer_is_snapshot_scoped() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="reported",
            source_system="manual",
        )
    )

    pointer = store.create_reported_pointer(
        context_id=result.context.id,
        revision_id=result.revision.id,
        report_snapshot_id="report-2026",
        reported_by="tester",
    )

    assert pointer.report_snapshot_id == "report-2026"
    assert pointer.revision_id == result.revision.id
    snapshot_event = store.list_events(tenant_id="tenant-a", limit=10)[-1]
    assert pointer.creation_event_id == snapshot_event.id
    assert db.query(ReportedValuePointer).count() == 1


def test_revision_lineage_returns_context_and_parent_ids() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            unit="tCO2e",
            state="corrected",
            source_system="manual",
            parent_revision_id=first.revision.id,
            input_revision_ids=[first.revision.id],
        )
    )

    lineage = store.get_revision_lineage(second.revision.id)

    assert lineage["revision"]["id"] == second.revision.id
    assert lineage["context"]["context_hash"] == second.context_hash
    assert lineage["parent_revision_id"] == first.revision.id
    assert lineage["input_revision_ids"] == [first.revision.id]


def test_transition_revision_to_current_state_moves_pointer_and_lists_events() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value="draft text",
            value_kind="text",
            unit=None,
            state="submitted",
            source_system="manual",
            created_by="author",
        )
    )

    event = store.transition_revision(
        revision_id=result.revision.id,
        to_state="validated",
        event_type="validation",
        occurred_by="reviewer",
        event_payload={"reason": "accepted"},
    )

    pointer = db.query(CurrentValuePointer).one()
    assert event.from_state == "submitted"
    assert event.to_state == "validated"
    assert event.pointer_moved is True
    assert pointer.revision_id == result.revision.id
    assert result.revision.creation_event_id == result.event.id
    assert result.revision.last_state_event_id == event.id
    assert pointer.last_event_id == event.id
    assert store.count_revisions(tenant_id="tenant-a", state="validated") == 1
    assert (
        store.list_revisions(
            tenant_id="tenant-a",
            context_id=result.context.id,
            state="validated",
            limit=10,
            offset=-10,
        )[0].id
        == result.revision.id
    )
    assert [item.id for item in store.list_events(tenant_id="tenant-a", limit=10)] == [
        result.event.id,
        event.id,
    ]
    assert (
        store.list_events(
            tenant_id="tenant-a",
            after_event_seq=result.event.event_seq,
            limit=10,
        )[0].id
        == event.id
    )
    assert (
        store.count_revisions(
            tenant_id="tenant-a",
            context_id=result.context.id,
            state="validated",
        )
        == 1
    )


def test_reported_pointer_is_immutable_and_rejects_mismatches() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    first = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            unit="tCO2e",
            state="reported",
            source_system="manual",
        )
    )
    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("2"),
            value_kind="numeric",
            unit="tCO2e",
            state="reported",
            source_system="manual",
        )
    )
    other_identity = _identity()
    other_identity = ValueContextIdentity(
        **{**other_identity.__dict__, "entity_id": "entity-2"}
    )
    other = store.append_revision(
        ValueRevisionInput(
            context_identity=other_identity,
            value=Decimal("3"),
            value_kind="numeric",
            unit="tCO2e",
            state="reported",
            source_system="manual",
        )
    )

    pointer = store.create_reported_pointer(
        context_id=first.context.id,
        revision_id=first.revision.id,
        report_snapshot_id="snapshot-a",
        reported_by="reporter-a",
    )
    with pytest.raises(ValueVersioningError, match="immutable"):
        store.create_reported_pointer(
            context_id=first.context.id,
            revision_id=second.revision.id,
            report_snapshot_id="snapshot-a",
            reported_by="reporter-b",
        )

    assert pointer.revision_id == first.revision.id
    assert pointer.reported_by == "reporter-a"
    snapshot_event = store.list_events(tenant_id="tenant-a", limit=10)[-1]
    assert pointer.creation_event_id == snapshot_event.id
    assert db.query(ReportedValuePointer).count() == 1
    with pytest.raises(ValueVersioningError, match="does not belong"):
        store.create_reported_pointer(
            context_id=first.context.id,
            revision_id=other.revision.id,
            report_snapshot_id="snapshot-b",
        )
    with pytest.raises(ValueVersioningError, match="report_snapshot_id is required"):
        store.create_reported_pointer(
            context_id=first.context.id,
            revision_id=first.revision.id,
            report_snapshot_id="",
        )


def test_idempotent_reported_pointer_retry_releases_transaction_locks() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("1"),
            value_kind="numeric",
            state="approved",
        )
    )
    store.create_reported_pointer(
        context_id=result.context.id,
        revision_id=result.revision.id,
        report_snapshot_id="snapshot-a",
    )
    original_commit = db.commit
    db.commit = MagicMock(wraps=original_commit)

    store.create_reported_pointer(
        context_id=result.context.id,
        revision_id=result.revision.id,
        report_snapshot_id="snapshot-a",
    )

    db.commit.assert_called_once_with()


def test_context_lineage_payloads_and_scalar_helpers_cover_non_numeric_values() -> None:
    db = _session()
    store = ValueRevisionStore(db)
    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=True,
            value_kind="boolean",
            unit=None,
            state="approved",
            source_system="manual",
        )
    )
    lineage = store.get_context_lineage(result.context.id)

    assert lineage["context"]["id"] == result.context.id
    assert lineage["revisions"][0]["boolean_value"] is True
    assert lineage["events"][0]["event_type"] == "revision_created"
    assert lineage["current_revision_id"] == result.revision.id
    assert context_to_payload(result.context)["context_hash"] == result.context_hash
    assert revision_to_payload(result.revision)["canonical_value"] == "true"
    event_payload = event_to_payload(result.event)
    assert event_payload["pointer_moved"] is True
    assert event_payload["previous_event_hash"] is None
    assert event_payload["event_hash"] == result.event.event_hash
    response_payload = ValueRevisionEventResponse.model_validate(
        event_payload
    ).model_dump()
    assert response_payload["previous_event_hash"] is None
    assert response_payload["event_hash"] == result.event.event_hash

    assert _value_columns("yes", "boolean")["boolean_value"] is True
    assert _value_columns("no", "boolean")["canonical_value"] == "false"
    assert _value_columns(None, "text")["text_value"] == ""
    assert _canonical_boolean("1") is True
    assert _canonical_boolean("0") is False
    with pytest.raises(ValueVersioningError, match="invalid boolean value"):
        _canonical_boolean("maybe")
    assert _normalized_state("approved") == "approved"
    with pytest.raises(ValueVersioningError, match="unknown value revision state"):
        _normalized_state("unknown")
    with pytest.raises(ValueVersioningError, match="state is required"):
        _normalized_state("")


def test_append_revision_uses_savepoint_retry_path(monkeypatch) -> None:
    db = _session()
    store = ValueRevisionStore(db)
    monkeypatch.setattr(store, "_supports_savepoint_retry", lambda: True)

    result = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("4.2"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
            external_key="savepoint:1",
        )
    )

    assert result.revision.canonical_value == "4.2"
    assert result.event.event_type == "revision_created"
    assert result.current_pointer is not None
    assert (
        store.get_context_lineage(result.context.id)["current_revision_id"]
        == result.revision.id
    )

    second = store.append_revision(
        ValueRevisionInput(
            context_identity=_identity(),
            value=Decimal("5.2"),
            value_kind="numeric",
            unit="tCO2e",
            state="approved",
            source_system="manual",
            external_key="savepoint:2",
        )
    )

    assert second.context.id == result.context.id
    assert second.revision.revision_number == 2


def test_missing_context_and_revision_raise_versioning_errors() -> None:
    db = _session()
    store = ValueRevisionStore(db)

    with pytest.raises(ValueVersioningError, match="value context not found"):
        store.get_context_lineage(404)
    with pytest.raises(ValueVersioningError, match="value revision not found"):
        store.get_revision_lineage("missing")


class _Nested:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FlushFailingDb:
    def __init__(self, exc: IntegrityError):
        self.exc = exc

    def begin_nested(self):
        return _Nested()

    def add(self, _obj):
        return None

    def flush(self):
        raise self.exc


def _integrity_error() -> IntegrityError:
    return IntegrityError("insert", {}, RuntimeError("duplicate"))


def test_context_retry_returns_existing_context_after_integrity_error(
    monkeypatch,
) -> None:
    store = ValueRevisionStore(_FlushFailingDb(_integrity_error()))
    identity = _identity()
    existing = ValueContext(tenant_id=identity.tenant_id, context_hash="hash")
    find_calls = iter([None, existing])

    monkeypatch.setattr(store, "_supports_savepoint_retry", lambda: True)
    monkeypatch.setattr(store, "_find_context", lambda **_kwargs: next(find_calls))

    assert (
        store._get_or_create_context(
            identity=identity,
            context_hash="hash",
            created_by="tester",
        )
        is existing
    )


def test_savepoint_retry_paths_reraise_last_integrity_error(monkeypatch) -> None:
    exc = _integrity_error()
    store = ValueRevisionStore(_FlushFailingDb(exc))
    monkeypatch.setattr(store, "_supports_savepoint_retry", lambda: True)
    monkeypatch.setattr(store, "_find_context", lambda **_kwargs: None)

    with pytest.raises(IntegrityError):
        store._get_or_create_context(
            identity=_identity(),
            context_hash="hash",
            created_by="tester",
        )

    monkeypatch.setattr(store, "_next_revision_number", lambda _context_id: 1)
    with pytest.raises(IntegrityError):
        store._insert_revision_with_retry(
            identity=_identity(),
            context=SimpleNamespace(id=1),
            revision_input=ValueRevisionInput(
                context_identity=_identity(),
                value=Decimal("1"),
                value_kind="numeric",
            ),
            state="approved",
            value_columns=_value_columns(Decimal("1"), "numeric"),
            creation_event_id="event-1",
        )

    monkeypatch.setattr(
        store,
        "_latest_tenant_event_position",
        lambda _tenant_id: (0, None),
    )
    monkeypatch.setattr(store, "_next_context_event_seq", lambda _context_id: 1)
    with pytest.raises(IntegrityError):
        store._append_event(
            context=SimpleNamespace(id=1, tenant_id="tenant-a"),
            revision=SimpleNamespace(id="revision-1"),
            event_type="revision_created",
            from_state=None,
            to_state="approved",
            pointer_moved=False,
            event_payload=None,
            occurred_by="tester",
        )
