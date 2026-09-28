"""VARCH-5b contract tests — mapping component binding validation.

Pure deterministic core (composing the VARCH-4 cores): bitemporal window integrity, component-
approval pin, axis-published pin, term-belongs-to-axis pin, content-addressing, and successor
planning. Vocabulary/identity match the persisted mapping_component_bindings table. No DB layer;
persistence is the VARCH-6 seam.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.semantic import write_replay
from src.semantic.write_binding import (
    BindingError,
    MappingComponentBinding,
    SuccessorPlan,
    plan_component_binding_successor,
    validate_mapping_component_binding,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

_APPROVED = {101, 102}
_AXES = {"ax:hazard", "ax:treatment"}
_AXIS_TERMS = {"ax:hazard": {"t:hazardous", "t:non_hazardous"}, "ax:treatment": set()}


def _b(**over):
    kwargs = dict(
        id="mcb1",
        component_ref=101,
        axis_id="ax:hazard",
        valid_from=T0,
        decision_commit_id=10,
        term_id="t:hazardous",
    )
    kwargs.update(over)
    return MappingComponentBinding(**kwargs)


def _validate(b, **over):
    kwargs = dict(
        approved_component_refs=_APPROVED,
        published_axis_ids=_AXES,
        axis_terms=_AXIS_TERMS,
    )
    kwargs.update(over)
    validate_mapping_component_binding(b, **kwargs)


def test_valid_binding_passes() -> None:
    _validate(_b())


def test_valid_binding_without_term_passes() -> None:
    _validate(_b(axis_id="ax:treatment", term_id=None))


def test_rejects_unapproved_component() -> None:
    with pytest.raises(BindingError, match="approved mapping component"):
        _validate(_b(component_ref=999))


def test_rejects_nonpositive_component_ref() -> None:
    with pytest.raises(BindingError, match="positive integer"):
        _validate(_b(component_ref=0))


def test_rejects_unpublished_axis() -> None:
    with pytest.raises(BindingError, match="not published"):
        _validate(_b(axis_id="ax:unknown"))


def test_rejects_term_not_on_axis() -> None:
    with pytest.raises(BindingError, match="does not belong to axis"):
        _validate(_b(term_id="t:wrong"))


def test_rejects_term_from_other_axis() -> None:
    # a real term, but it belongs to a different axis.
    with pytest.raises(BindingError, match="does not belong to axis"):
        _validate(_b(axis_id="ax:treatment", term_id="t:hazardous"))


def test_rejects_naive_valid_from() -> None:
    with pytest.raises(Exception):
        _validate(_b(valid_from=datetime(2026, 1, 1)))


def test_rejects_nonpositive_decision_commit() -> None:
    with pytest.raises(BindingError, match="decision_commit_id"):
        _validate(_b(decision_commit_id=0))


def test_binding_hash_roundtrip_and_mismatch() -> None:
    descriptor = {"component_ref": 101, "axis": "ax:hazard", "term": "t:hazardous"}
    h = write_replay.canonical_json.content_hash(descriptor)
    validate_mapping_component_binding(
        _b(binding_hash=h),
        approved_component_refs=_APPROVED,
        published_axis_ids=_AXES,
        axis_terms=_AXIS_TERMS,
        descriptor=descriptor,
    )
    with pytest.raises(Exception):  # ReplayValidationError on mismatch
        validate_mapping_component_binding(
            _b(binding_hash="0" * 64),
            approved_component_refs=_APPROVED,
            published_axis_ids=_AXES,
            axis_terms=_AXIS_TERMS,
            descriptor=descriptor,
        )


def test_successor_plan_composes_4a() -> None:
    current = _b(id="mcb1", decision_commit_id=10)
    proposed = _b(id="mcb2", decision_commit_id=11)
    plan = plan_component_binding_successor(
        current, proposed, published_siblings=[current]
    )
    assert isinstance(plan, SuccessorPlan)
    assert plan.supersede is not None and plan.supersede.successor_version_id == "mcb2"


def test_first_version_is_insert_only() -> None:
    assert plan_component_binding_successor(None, _b()).is_first_version
