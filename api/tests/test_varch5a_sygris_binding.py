"""VARCH-5a contract tests — sygris variable binding validation.

Pure deterministic core composing the VARCH-4 cores: bitemporal window integrity, slice-coverage
pin (the bound target must be a covered subject), projection-flag/target-kind vocabulary (matching
the table CHECK constraints), content-addressing, source-rights pin (license temporal validity +
license-aware egress over an explicit egress scope), and binding successor planning. No DB layer:
the binding validator is pure; persistence is the VARCH-6 seam.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.semantic import write_replay
from src.semantic.coverage import SCOPE_PUBLIC, SCOPE_SHARED, SCOPE_TENANT_PRIVATE
from src.semantic.write_binding import (
    BindingError,
    SuccessorPlan,
    SygrisBinding,
    assert_binding_source_rights,
    plan_binding_successor,
    validate_sygris_binding,
)
from src.semantic.write_scope import LicenseError, LicenseGrant, Window

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
_COVERED = {("canonical_concept", "syg:WastePlastic")}


def _binding(**over):
    kwargs = dict(
        id="b1",
        sygris_variable_ref="syg:var:1",
        target_kind="canonical_concept",
        target_ref="syg:WastePlastic",
        projection_flag="sygris_atomization_catalog",
        valid_from=T0,
        decision_commit_id=10,
    )
    kwargs.update(over)
    return SygrisBinding(**kwargs)


def test_valid_binding_passes() -> None:
    validate_sygris_binding(_binding(), covered_refs=_COVERED)


def test_rejects_uncovered_target() -> None:
    with pytest.raises(BindingError, match="slice-coverage pin"):
        validate_sygris_binding(
            _binding(target_ref="syg:NotCovered"), covered_refs=_COVERED
        )


def test_rejects_unknown_target_kind() -> None:
    with pytest.raises(BindingError, match="target_kind"):
        validate_sygris_binding(_binding(target_kind="bogus"), covered_refs=_COVERED)


def test_rejects_unknown_projection_flag() -> None:
    with pytest.raises(BindingError, match="projection_flag"):
        validate_sygris_binding(
            _binding(projection_flag="bogus"), covered_refs=_COVERED
        )


def test_rejects_naive_valid_from() -> None:
    with pytest.raises(Exception):
        validate_sygris_binding(
            _binding(valid_from=datetime(2026, 1, 1)), covered_refs=_COVERED
        )


def test_rejects_nonpositive_decision_commit() -> None:
    with pytest.raises(BindingError, match="decision_commit_id"):
        validate_sygris_binding(_binding(decision_commit_id=0), covered_refs=_COVERED)


def test_binding_hash_roundtrip_and_mismatch() -> None:
    descriptor = {"variable": "syg:var:1", "target": "syg:WastePlastic"}
    h = write_replay.canonical_json.content_hash(descriptor)
    validate_sygris_binding(
        _binding(binding_hash=h), covered_refs=_COVERED, descriptor=descriptor
    )
    with pytest.raises(Exception):  # ReplayValidationError on mismatch
        validate_sygris_binding(
            _binding(binding_hash="0" * 64),
            covered_refs=_COVERED,
            descriptor=descriptor,
        )


def test_binding_hash_without_descriptor_fails() -> None:
    with pytest.raises(BindingError, match="no descriptor"):
        validate_sygris_binding(_binding(binding_hash="a" * 64), covered_refs=_COVERED)


def _grant(access, rights):
    return LicenseGrant(
        valid_window=Window(T0, None),
        decision_window=Window(T0, None),
        access_scope=access,
        rights_scope=rights,
    )


def test_source_rights_pin_blocks_wider_scope() -> None:
    # shared-licensed source cannot back a public egress scope.
    assert_binding_source_rights(
        _grant(SCOPE_SHARED, SCOPE_SHARED),
        target_scope=SCOPE_SHARED,
        valid_at=T1,
        decision_at=T1,
    )
    with pytest.raises(LicenseError, match="ceiling|more permissive"):
        assert_binding_source_rights(
            _grant(SCOPE_SHARED, SCOPE_SHARED),
            target_scope=SCOPE_PUBLIC,
            valid_at=T1,
            decision_at=T1,
        )


def test_source_rights_pin_blocks_expired_license() -> None:
    grant = LicenseGrant(
        valid_window=Window(T0, None),
        decision_window=Window(T0, None),
        access_scope=SCOPE_SHARED,
        rights_scope=SCOPE_SHARED,
        expiry=T1,
    )
    with pytest.raises(LicenseError, match="expired"):
        assert_binding_source_rights(
            grant, target_scope=SCOPE_SHARED, valid_at=T1, decision_at=T1
        )


def test_binding_successor_plan_composes_4a() -> None:
    current = _binding(id="b1", valid_from=T0, decision_commit_id=10)
    proposed = _binding(id="b2", valid_from=T0, decision_commit_id=11)
    plan = plan_binding_successor(current, proposed, published_siblings=[current])
    assert isinstance(plan, SuccessorPlan)
    assert plan.supersede is not None
    assert plan.supersede.version_id == "b1"
    assert plan.supersede.successor_version_id == "b2"


def test_binding_first_version_is_insert_only() -> None:
    plan = plan_binding_successor(None, _binding(id="b1"))
    assert plan.is_first_version
