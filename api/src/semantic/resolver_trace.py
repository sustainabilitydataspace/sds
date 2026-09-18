"""Resolver — trace-pin assembly (VARCH-6f).

Step 6 (resolver), final sub-slice (f): assembles the full deterministic TRACE PIN BUNDLE the
resolver must record for every resolved value (candidate-v14 resolver contract line 165), as one
content-addressable, reproducible trace record. Runs last in the pipeline (after 6a-6e). Pure +
deterministic; persisting the trace row is the VARCH-8 write seam.

The trace pins make a read fully reproducible: pinning every input that can change the result
(EVS hash, replay manifest, profile versions, axis/term/relation versions, scope/consolidation/
disclosure/license/factor/confidence/mapping pins, and any restatement/tombstone) means replaying
at the same ``(valid_as_of, decision_commit_id)`` under the same pins yields the identical value.
The ``trace_hash`` is the canonical content hash over the WHOLE pin set, so any drift in any pin
changes the trace address (composing :mod:`src.semantic.write_replay` 4c canonical hashing).

Gates contributed here (mechanism; CI wiring in VARCH-10): trace-pin-completeness (the always-
required pins plus every resolver-declared applicable pin must be present — fail closed on a gap),
trace-hash-reproducibility (the trace is content-addressed over the full pin bundle).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Iterable

from src.semantic import write_replay

_HEX = frozenset("0123456789abcdef")

# Pins that are ALWAYS required for any resolved value.
ALWAYS_REQUIRED = (
    "valid_as_of",
    "decision_commit_id",
    "effective_version_set_hash",
    "contract_version",
    "replay_manifest_version",
    "replay_manifest_hash",
    "canonical_profile_version",
    "computation_profile_version",
)

# Pin fields that, when present, MUST be 64-lowercase-hex content hashes.
_HASH_FIELDS = frozenset(
    {
        "effective_version_set_hash",
        "replay_manifest_hash",
        "relation_set_hash",
        "consolidation_composition_profile_hash",
        "composed_disclosure_outcome_hash",
        "composed_rights_decision_hash",
        "factor_set_hash",
        "factor_vintage_policy_hash",
        "license_rights_window_hash",
        "disclosure_profile_hash",
        "suppression_decision_hash",
        "confidence_policy_hash",
        "mapping_assertion_hash",
    }
)


class TraceError(ValueError):
    """Raised when the trace pin bundle is incomplete or malformed."""


@dataclass(frozen=True)
class TracePins:
    """The full resolver trace pin bundle (candidate-v14 resolver contract line 165).

    The eight ``ALWAYS_REQUIRED`` fields are mandatory for any read; the rest are optional at the
    type level but the resolver declares which ones are APPLICABLE for the slice via
    ``required_pins`` so completeness is enforced fail-closed (a dimensioned/consolidated/private/
    restated read names its applicable pins and a missing one is rejected).
    """

    # always required
    valid_as_of: datetime
    decision_commit_id: int
    effective_version_set_hash: str
    contract_version: str
    replay_manifest_version: str
    replay_manifest_hash: str
    canonical_profile_version: str
    computation_profile_version: str
    # dimension / relation
    axis_version: str | None = None
    term_version: str | None = None
    relation_set_hash: str | None = None
    # scope / consolidation
    scope_assignment_version: str | None = None
    consolidation_consent_versions: tuple[str, ...] | None = None
    consolidation_composition_profile_version: str | None = None
    consolidation_composition_profile_hash: str | None = None
    composed_disclosure_outcome_hash: str | None = None
    composed_rights_decision_hash: str | None = None
    # measurement / factor / license
    measurement_basis: str | None = None
    factor_set_hash: str | None = None
    factor_vintage_policy_hash: str | None = None
    factor_license_rights_decision: str | None = None
    license_rights_window_hash: str | None = None
    temporal_egress_policy: str | None = None
    license_evaluation_time: datetime | None = None
    # disclosure / suppression / confidence / mapping
    disclosure_profile_version: str | None = None
    disclosure_profile_hash: str | None = None
    suppression_decision_hash: str | None = None
    confidence_policy_hash: str | None = None
    mapping_assertion_hash: str | None = None
    # private commitment (only when authorized)
    private_commitment_profile: str | None = None
    private_commitment_key_generation: str | None = None
    # restatement / tombstone (only when applicable)
    restatement_id: str | None = None
    tombstone_id: str | None = None


_ALL_PIN_NAMES = frozenset(f.name for f in fields(TracePins))


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)


def _trace_payload(pins: TracePins) -> dict:
    """Deterministic JSON-serializable payload over the whole pin set (stable rendering)."""
    payload: dict = {}
    for f in fields(pins):
        value = getattr(pins, f.name)
        if isinstance(value, datetime):
            payload[f.name] = value.isoformat()
        elif isinstance(value, tuple):
            # consent versions: sort for order-independence.
            payload[f.name] = sorted(value)
        else:
            payload[f.name] = value
    return payload


def assemble_trace_pins(pins: TracePins, *, required_pins: Iterable[str] = ()) -> str:
    """Validate completeness + shape and return the content-addressable ``trace_hash``.

    Validates that every ``ALWAYS_REQUIRED`` pin and every resolver-declared applicable pin in
    ``required_pins`` is present (non-empty / non-None), that ``decision_commit_id`` is positive
    and ``valid_as_of`` is timezone-aware, and that every present hash field is 64-lowercase-hex.
    Then returns the canonical content hash over the WHOLE pin bundle (trace reproducibility).
    """
    required = set(ALWAYS_REQUIRED) | set(required_pins)
    unknown = sorted(required - _ALL_PIN_NAMES)
    if unknown:
        raise TraceError(f"unknown required pin names: {unknown}")

    missing = []
    for name in sorted(required):
        value = getattr(pins, name)
        # a required pin is incomplete if absent, an empty string, OR an empty collection
        # (e.g. consolidation_consent_versions == ()): an empty applicable pin must fail closed,
        # not be content-addressed as if complete.
        if (
            value is None
            or (isinstance(value, str) and value == "")
            or (isinstance(value, (tuple, list, set, dict)) and len(value) == 0)
        ):
            missing.append(name)
    if missing:
        raise TraceError(f"trace pin bundle is incomplete; missing pins {missing}")

    if pins.decision_commit_id <= 0:
        raise TraceError("decision_commit_id must be positive")
    if pins.valid_as_of.tzinfo is None or pins.valid_as_of.utcoffset() is None:
        raise TraceError("valid_as_of must be timezone-aware")

    for name in _HASH_FIELDS:
        value = getattr(pins, name)
        if value is not None and not _is_hex64(value):
            raise TraceError(f"trace pin {name!r} is not a 64-lowercase-hex hash")

    return write_replay.canonical_json.content_hash(_trace_payload(pins))


def verify_trace_reproducible(
    pins: TracePins, expected_trace_hash: str, *, required_pins: Iterable[str] = ()
) -> None:
    """Re-assemble the trace and reject a mismatch (trace-hash-reproducibility)."""
    actual = assemble_trace_pins(pins, required_pins=required_pins)
    if actual != expected_trace_hash:
        raise TraceError(
            f"trace_hash mismatch: recomputed {actual} != expected {expected_trace_hash}"
        )


# --- DB write seam (documented) -------------------------------------------------------


def persist_trace(session, pins, trace_hash):  # pragma: no cover - DB seam
    """Persist the resolved value's trace row with its pin bundle (VARCH-8 write seam)."""
    raise NotImplementedError(
        "persist_trace is the VARCH-8 write-path seam; the VARCH-6f deliverable is the pure "
        "trace-pin assembly + reproducibility core."
    )
