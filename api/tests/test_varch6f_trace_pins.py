"""VARCH-6f contract tests — resolver trace-pin assembly.

Pure deterministic core (resolver contract line 165): assemble the full trace pin bundle into a
content-addressable, reproducible trace_hash; enforce completeness of the always-required pins
plus resolver-declared applicable pins (fail-closed on a gap); validate hash-field shapes; and
re-verify reproducibility. No DB layer.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.semantic.resolver_trace import (
    ALWAYS_REQUIRED,
    TraceError,
    TracePins,
    assemble_trace_pins,
    verify_trace_reproducible,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _pins(**over):
    kwargs = dict(
        valid_as_of=T0,
        decision_commit_id=10,
        effective_version_set_hash="a" * 64,
        contract_version="cv1",
        replay_manifest_version="rmv1",
        replay_manifest_hash="b" * 64,
        canonical_profile_version="cjv1",
        computation_profile_version="compv1",
    )
    kwargs.update(over)
    return TracePins(**kwargs)


def test_assemble_minimal_required_passes() -> None:
    h = assemble_trace_pins(_pins())
    assert len(h) == 64


def test_assemble_is_deterministic() -> None:
    assert assemble_trace_pins(_pins()) == assemble_trace_pins(_pins())


def test_distinct_pins_distinct_hash() -> None:
    a = assemble_trace_pins(_pins())
    b = assemble_trace_pins(_pins(factor_set_hash="c" * 64))
    assert a != b  # adding a pin changes the trace address


def test_rejects_missing_always_required() -> None:
    with pytest.raises(TraceError, match="incomplete"):
        assemble_trace_pins(_pins(contract_version=""))


def test_rejects_missing_declared_applicable_pin() -> None:
    # a dimensioned read declares axis/term applicable; a missing one fails closed.
    with pytest.raises(TraceError, match="missing pins"):
        assemble_trace_pins(
            _pins(axis_version="ax1"),  # term_version left None
            required_pins=["axis_version", "term_version"],
        )


def test_declared_applicable_pins_present_passes() -> None:
    h = assemble_trace_pins(
        _pins(axis_version="ax1", term_version="tm1"),
        required_pins=["axis_version", "term_version"],
    )
    assert len(h) == 64


def test_rejects_unknown_required_pin_name() -> None:
    with pytest.raises(TraceError, match="unknown required pin"):
        assemble_trace_pins(_pins(), required_pins=["not_a_pin"])


def test_rejects_nonpositive_decision_commit() -> None:
    with pytest.raises(TraceError, match="decision_commit_id"):
        assemble_trace_pins(_pins(decision_commit_id=0))


def test_rejects_naive_valid_as_of() -> None:
    with pytest.raises(TraceError, match="timezone-aware"):
        assemble_trace_pins(_pins(valid_as_of=datetime(2026, 1, 1)))


def test_rejects_malformed_hash_pin() -> None:
    with pytest.raises(TraceError, match="64-lowercase-hex"):
        assemble_trace_pins(_pins(factor_set_hash="xyz"))


def test_rejects_empty_required_collection_pin() -> None:
    # M1: a required collection-valued pin that is empty must fail closed.
    with pytest.raises(TraceError, match="missing pins"):
        assemble_trace_pins(
            _pins(consolidation_consent_versions=()),
            required_pins=["consolidation_consent_versions"],
        )


def test_consent_versions_order_independent() -> None:
    a = assemble_trace_pins(
        _pins(consolidation_consent_versions=("v2", "v1")),
        required_pins=["consolidation_consent_versions"],
    )
    b = assemble_trace_pins(
        _pins(consolidation_consent_versions=("v1", "v2")),
        required_pins=["consolidation_consent_versions"],
    )
    assert a == b


def test_verify_trace_reproducible_roundtrip() -> None:
    p = _pins(suppression_decision_hash="d" * 64)
    h = assemble_trace_pins(p)
    verify_trace_reproducible(p, h)


def test_verify_trace_reproducible_detects_drift() -> None:
    p = _pins()
    h = assemble_trace_pins(p)
    drifted = replace(p, factor_set_hash="e" * 64)
    with pytest.raises(TraceError, match="trace_hash mismatch"):
        verify_trace_reproducible(drifted, h)


def test_always_required_constant_is_complete() -> None:
    # guards the always-required set against accidental shrinkage.
    assert set(ALWAYS_REQUIRED) == {
        "valid_as_of",
        "decision_commit_id",
        "effective_version_set_hash",
        "contract_version",
        "replay_manifest_version",
        "replay_manifest_hash",
        "canonical_profile_version",
        "computation_profile_version",
    }
