"""VARCH-7b contract tests — legacy read/calc policy + legacy-dimensions-audit sampling.

Pure deterministic core (candidate-v14 "Legacy Value Policy", lines 179-181): legacy values stay
readable as history; new-dimensioned-calc use is gated by classification state (auto blocked until
reviewed, grandfathered blocked without fallback); legacy/untrusted rows are flagged in exports/
traces; and a deterministic sampling audit verifies classification quality against an independent
verifier. No DB layer.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.semantic.legacy_dimensions import (
    CLASS_AUTO_PENDING,
    CLASS_CLASSIFIED,
    CLASS_GRANDFATHERED,
    CLASS_NOT_REQUIRED,
    REVIEW_QUEUED,
    REVIEW_REVIEWED,
    LegacyClassification,
    LegacyClassificationError,
    assert_usable_in_new_dimensioned_calc,
    export_trace_flags,
    is_readable_as_historical,
    must_flag_in_export,
    select_audit_sample,
    verify_classification_quality,
)

# --- read / calc policy ---------------------------------------------------------------


def test_all_states_readable_as_historical() -> None:
    for s in (
        CLASS_CLASSIFIED,
        CLASS_AUTO_PENDING,
        CLASS_GRANDFATHERED,
        CLASS_NOT_REQUIRED,
    ):
        assert is_readable_as_historical(s) is True


def test_readable_rejects_unknown_state() -> None:
    with pytest.raises(LegacyClassificationError, match="unknown classification_state"):
        is_readable_as_historical("bogus")


def test_classified_usable_in_new_calc() -> None:
    assert_usable_in_new_dimensioned_calc(CLASS_CLASSIFIED)


def test_not_required_usable() -> None:
    assert_usable_in_new_dimensioned_calc(CLASS_NOT_REQUIRED)


def test_auto_pending_blocked_until_reviewed() -> None:
    with pytest.raises(LegacyClassificationError, match="until reviewed"):
        assert_usable_in_new_dimensioned_calc(
            CLASS_AUTO_PENDING, review_status=REVIEW_QUEUED
        )
    # promoted by review -> allowed.
    assert_usable_in_new_dimensioned_calc(
        CLASS_AUTO_PENDING, review_status=REVIEW_REVIEWED
    )


def test_grandfathered_blocked_without_fallback() -> None:
    with pytest.raises(LegacyClassificationError, match="without an explicit fallback"):
        assert_usable_in_new_dimensioned_calc(CLASS_GRANDFATHERED)
    assert_usable_in_new_dimensioned_calc(
        CLASS_GRANDFATHERED, has_explicit_fallback=True
    )


def test_calc_gate_rejects_unknown_review_status() -> None:
    with pytest.raises(LegacyClassificationError, match="unknown review_status"):
        assert_usable_in_new_dimensioned_calc(CLASS_AUTO_PENDING, review_status="weird")


def test_must_flag_in_export() -> None:
    assert must_flag_in_export(CLASS_GRANDFATHERED) is True
    assert must_flag_in_export(CLASS_AUTO_PENDING) is True
    assert must_flag_in_export(CLASS_CLASSIFIED) is False
    assert must_flag_in_export(CLASS_NOT_REQUIRED) is False


def test_export_trace_flags() -> None:
    c = LegacyClassification(
        state=CLASS_GRANDFATHERED,
        confidence=Decimal("0.1"),
        contract_requires_dimensions=True,
        default_valid_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
        default_decision_commit_id=1,
        assigned_dimensions_hash=None,
        sunset_date=date(2031, 1, 1),
        classification_hash="a" * 64,
    )
    flags = export_trace_flags(c)
    assert flags["legacy_flagged"] is True
    assert flags["legacy_classification_state"] == CLASS_GRANDFATHERED
    assert flags["review_status"] == "none"
    assert flags["legacy_sunset_date"] == "2031-01-01"


# --- legacy-dimensions-audit sampling -------------------------------------------------


def test_audit_sample_is_deterministic() -> None:
    pop = [f"lc{i}" for i in range(20)]
    a = select_audit_sample(pop, sample_size=5, seed="s1")
    b = select_audit_sample(pop, sample_size=5, seed="s1")
    assert a == b and len(a) == 5
    # a different seed yields a (generally) different sample order.
    c = select_audit_sample(pop, sample_size=5, seed="s2")
    assert set(a) <= set(pop) and set(c) <= set(pop)


def test_audit_sample_size_clamped_to_population() -> None:
    got = select_audit_sample(["a", "b"], sample_size=10, seed="s")
    assert set(got) == {"a", "b"}


def test_audit_sample_rejects_nonpositive_size() -> None:
    with pytest.raises(LegacyClassificationError, match="sample_size must be positive"):
        select_audit_sample(["a"], sample_size=0, seed="s")


def test_audit_sample_rejects_duplicate_population() -> None:
    with pytest.raises(LegacyClassificationError, match="duplicate"):
        select_audit_sample(["a", "a"], sample_size=1, seed="s")


def test_audit_quality_passes_below_tolerance() -> None:
    expected = {
        "a": CLASS_CLASSIFIED,
        "b": CLASS_CLASSIFIED,
        "c": CLASS_GRANDFATHERED,
        "d": CLASS_CLASSIFIED,
    }
    verifier = {
        "a": CLASS_CLASSIFIED,
        "b": CLASS_CLASSIFIED,
        "c": CLASS_AUTO_PENDING,
        "d": CLASS_CLASSIFIED,
    }
    res = verify_classification_quality(expected, verifier, max_mismatch_rate="0.25")
    assert res.mismatch_rate == Decimal("0.25") and res.passed is True
    assert res.mismatches == ("c",)


def test_audit_quality_fails_above_tolerance() -> None:
    expected = {"a": CLASS_CLASSIFIED, "b": CLASS_CLASSIFIED}
    verifier = {"a": CLASS_GRANDFATHERED, "b": CLASS_CLASSIFIED}
    res = verify_classification_quality(expected, verifier, max_mismatch_rate="0.1")
    assert res.mismatch_rate == Decimal("0.5") and res.passed is False


def test_audit_quality_missing_verifier_is_mismatch() -> None:
    expected = {"a": CLASS_CLASSIFIED, "b": CLASS_CLASSIFIED}
    verifier = {"a": CLASS_CLASSIFIED}  # b unverified -> mismatch
    res = verify_classification_quality(expected, verifier, max_mismatch_rate="0.4")
    assert "b" in res.mismatches and res.passed is False


def test_audit_quality_rejects_empty_sample() -> None:
    with pytest.raises(LegacyClassificationError, match="empty audit sample"):
        verify_classification_quality({}, {}, max_mismatch_rate="0.1")
