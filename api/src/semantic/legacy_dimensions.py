"""Legacy value classification & backfill policy (VARCH-7).

candidate-v14 "Legacy Value Policy": the ~25,950 ``value_contexts`` with empty
``dimensions_json`` must be deterministically classified for the semantic-atomization substrate,
given a review queue, grandfather sunset dates, deterministic valid/decision-time defaults, and a
sampling audit gate. Like the sibling VARCH cores this is a PURE deterministic core over
materialized inputs; the actual backfill scan/write is the VARCH-8 seam, and migration 041 adds
``legacy_value_classifications`` as the persisted home.

Sub-slice (a) — classification + deterministic defaults (this file's first half):

* :func:`classify_legacy_value` assigns one of the four states deterministically:
  ``classified`` (a candidate dimension assignment at/above the confidence threshold),
  ``auto_classified_pending_review`` (a candidate below threshold — not trusted for public
  dimensioned claims until reviewed), ``legacy_grandfathered`` (no candidate; carries a sunset
  date), ``not_dimension_required`` (the governing contract does not require dimensions).
* :func:`deterministic_legacy_defaults` derives the legacy valid-time / decision-time / sunset
  defaults from the value context's own period fields (no clock, fully replayable).

Gates contributed here (mechanism; CI wiring in VARCH-10): legacy-classification-determinism,
legacy-default-assignment, legacy-classification-content-address.

Vocabularies bound to the migration-041 CHECK constraints:
``legacy_value_classifications.classification_state`` / ``review_status``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from src.semantic import write_replay
from src.semantic.profiles import computation

# classification_state (migration-041 CHECK enum).
CLASS_CLASSIFIED = "classified"
CLASS_AUTO_PENDING = "auto_classified_pending_review"
CLASS_GRANDFATHERED = "legacy_grandfathered"
CLASS_NOT_REQUIRED = "not_dimension_required"
CLASSIFICATION_STATES = frozenset(
    {CLASS_CLASSIFIED, CLASS_AUTO_PENDING, CLASS_GRANDFATHERED, CLASS_NOT_REQUIRED}
)

# review_status (migration-041 CHECK enum).
REVIEW_NONE = "none"
REVIEW_QUEUED = "queued"
REVIEW_REVIEWED = "reviewed"
REVIEW_DISMISSED = "dismissed"
REVIEW_STATUSES = frozenset(
    {REVIEW_NONE, REVIEW_QUEUED, REVIEW_REVIEWED, REVIEW_DISMISSED}
)


class LegacyClassificationError(ValueError):
    """Raised when a legacy value classification is invalid."""


def _add_whole_years(d: date, years: int) -> date:
    """Add whole years deterministically, clamping Feb 29 -> Feb 28 on a non-leap target year."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # only Feb 29 -> non-leap year reaches here; clamp to Feb 28 (documented policy).
        return d.replace(year=d.year + years, day=28)


@dataclass(frozen=True)
class LegacyDefaults:
    """The deterministic legacy valid/decision-time defaults (+ grandfather sunset)."""

    valid_from: datetime
    decision_commit_id: int
    sunset_date: date


@dataclass(frozen=True)
class LegacyClassification:
    """A classified legacy value context (mirrors ``legacy_value_classifications``)."""

    state: str
    confidence: Decimal
    contract_requires_dimensions: bool
    default_valid_from: datetime
    default_decision_commit_id: int
    assigned_dimensions_hash: str | None
    sunset_date: date | None
    classification_hash: str

    @property
    def review_status(self) -> str:
        # auto-classified rows enter the review queue; everything else needs no review.
        return REVIEW_QUEUED if self.state == CLASS_AUTO_PENDING else REVIEW_NONE


def deterministic_legacy_defaults(
    *,
    period_start: date,
    legacy_decision_commit_id: int,
    grandfather_years: int,
    period_close_date: date | None = None,
) -> LegacyDefaults:
    """Derive replayable legacy defaults from the value context's own period fields.

    ``valid_from`` is the reporting ``period_start`` at UTC midnight (deterministic, no clock).
    ``decision_commit_id`` is the pinned legacy decision floor (must be a positive committed id —
    the resolver later validates membership in the fenced sequence). ``sunset_date`` is the
    grandfather horizon: ``period_close_date`` (or ``period_start`` when close is absent) plus
    ``grandfather_years`` whole years.
    """
    if legacy_decision_commit_id <= 0:
        raise LegacyClassificationError(
            "legacy_decision_commit_id must be a positive committed id"
        )
    if grandfather_years <= 0:
        raise LegacyClassificationError("grandfather_years must be positive")
    valid_from = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    anchor = period_close_date if period_close_date is not None else period_start
    sunset = _add_whole_years(anchor, grandfather_years)
    return LegacyDefaults(
        valid_from=valid_from,
        decision_commit_id=legacy_decision_commit_id,
        sunset_date=sunset,
    )


def _to_unit_decimal(name: str, value: object) -> Decimal:
    try:
        d = computation.to_decimal(value)  # rejects float/NaN/Inf
    except computation.ComputationError as exc:
        raise LegacyClassificationError(f"{name}: {exc}") from exc
    if d < 0 or d > 1:
        raise LegacyClassificationError(f"{name} must be in [0, 1], got {d}")
    return d


def _assignment_hash(assignment: Iterable[tuple[str, str]]) -> str:
    # content-address the deterministically sorted (axis, term) set.
    return write_replay.canonical_json.content_hash(sorted(assignment))


def _classification_hash(
    *,
    state: str,
    confidence: Decimal,
    contract_requires_dimensions: bool,
    default_valid_from: datetime,
    default_decision_commit_id: int,
    assigned_dimensions_hash: str | None,
    sunset_date: date | None,
) -> str:
    return write_replay.canonical_json.content_hash(
        {
            "state": state,
            "confidence": format(confidence, "f"),
            "contract_requires_dimensions": contract_requires_dimensions,
            "default_valid_from": default_valid_from.isoformat(),
            "default_decision_commit_id": default_decision_commit_id,
            "assigned_dimensions_hash": assigned_dimensions_hash,
            "sunset_date": sunset_date.isoformat() if sunset_date else None,
        }
    )


def classify_legacy_value(
    *,
    contract_requires_dimensions: bool,
    candidate_assignment: Iterable[tuple[str, str]] | None,
    confidence: object,
    confidence_threshold: object,
    defaults: LegacyDefaults,
) -> LegacyClassification:
    """Deterministically classify one legacy value context (fail-closed, content-addressed).

    Decision tree:

    1. contract does NOT require dimensions -> ``not_dimension_required``;
    2. no candidate assignment -> ``legacy_grandfathered`` (requires ``defaults.sunset_date``);
    3. candidate with ``confidence >= threshold`` -> ``classified``;
    4. candidate with ``confidence < threshold`` -> ``auto_classified_pending_review``.

    Cases 3/4 pin the assigned-dimension content hash; only the grandfathered case carries a
    sunset date (mirroring the migration-041 ``sunset IS NOT NULL`` CHECK). The returned
    ``classification_hash`` content-addresses the whole classification for replayable backfill.
    """
    conf = _to_unit_decimal("confidence", confidence)
    thr = _to_unit_decimal("confidence_threshold", confidence_threshold)
    assignment = None if candidate_assignment is None else list(candidate_assignment)

    assigned_hash: str | None = None
    sunset: date | None = None

    if not contract_requires_dimensions:
        state = CLASS_NOT_REQUIRED
    elif not assignment:
        # unclassifiable: grandfather it with the deterministic sunset (CHECK: grandfathered
        # <=> sunset present). LegacyDefaults always carries a sunset_date.
        state = CLASS_GRANDFATHERED
        sunset = defaults.sunset_date
    else:
        assigned_hash = _assignment_hash(assignment)
        state = CLASS_CLASSIFIED if conf >= thr else CLASS_AUTO_PENDING

    classification_hash = _classification_hash(
        state=state,
        confidence=conf,
        contract_requires_dimensions=contract_requires_dimensions,
        default_valid_from=defaults.valid_from,
        default_decision_commit_id=defaults.decision_commit_id,
        assigned_dimensions_hash=assigned_hash,
        sunset_date=sunset,
    )
    return LegacyClassification(
        state=state,
        confidence=conf,
        contract_requires_dimensions=contract_requires_dimensions,
        default_valid_from=defaults.valid_from,
        default_decision_commit_id=defaults.decision_commit_id,
        assigned_dimensions_hash=assigned_hash,
        sunset_date=sunset,
        classification_hash=classification_hash,
    )


# --- VARCH-7b: legacy read/calc policy --------------------------------------------------


def is_readable_as_historical(state: str) -> bool:
    """A legacy value (any classified state) remains readable as a HISTORICAL value.

    Classification restricts NEW dimensioned use; it never makes an already-recorded value
    unreadable as history. Validates the state is known.
    """
    if state not in CLASSIFICATION_STATES:
        raise LegacyClassificationError(f"unknown classification_state {state!r}")
    return True


def assert_usable_in_new_dimensioned_calc(
    state: str,
    *,
    review_status: str = REVIEW_NONE,
    has_explicit_fallback: bool = False,
) -> None:
    """Gate a legacy value's use in a NEW dimensioned calculation (fail-closed).

    * ``classified`` / ``not_dimension_required`` — allowed;
    * ``auto_classified_pending_review`` — BLOCKED until reviewed (``review_status='reviewed'``):
      a below-threshold auto-classification is not trusted for public dimensioned claims;
    * ``legacy_grandfathered`` — BLOCKED unless an explicit fallback exists.
    """
    if state not in CLASSIFICATION_STATES:
        raise LegacyClassificationError(f"unknown classification_state {state!r}")
    if review_status not in REVIEW_STATUSES:
        raise LegacyClassificationError(f"unknown review_status {review_status!r}")
    if state in (CLASS_CLASSIFIED, CLASS_NOT_REQUIRED):
        return
    if state == CLASS_AUTO_PENDING:
        if review_status == REVIEW_REVIEWED:
            return
        raise LegacyClassificationError(
            "auto_classified_pending_review is not trusted for a new dimensioned "
            "calculation until reviewed"
        )
    # legacy_grandfathered
    if has_explicit_fallback:
        return
    raise LegacyClassificationError(
        "legacy_grandfathered is blocked from a new dimensioned calculation without an "
        "explicit fallback"
    )


def must_flag_in_export(state: str) -> bool:
    """Whether a legacy value must be FLAGGED in exports/traces (legacy/untrusted)."""
    if state not in CLASSIFICATION_STATES:
        raise LegacyClassificationError(f"unknown classification_state {state!r}")
    return state in (CLASS_GRANDFATHERED, CLASS_AUTO_PENDING)


def export_trace_flags(classification: LegacyClassification) -> dict:
    """The deterministic legacy flag set carried into an export/trace projection."""
    return {
        "legacy_classification_state": classification.state,
        "legacy_flagged": must_flag_in_export(classification.state),
        "review_status": classification.review_status,
        "legacy_sunset_date": (
            classification.sunset_date.isoformat()
            if classification.sunset_date
            else None
        ),
    }


# --- VARCH-7b: legacy-dimensions-audit sampling ----------------------------------------


@dataclass(frozen=True)
class AuditResult:
    """The outcome of a legacy-dimensions-audit sample-verification."""

    sampled: tuple[str, ...]
    mismatches: tuple[str, ...]
    mismatch_rate: Decimal
    passed: bool


def select_audit_sample(
    classification_ids: Iterable[str], *, sample_size: int, seed: str
) -> tuple[str, ...]:
    """Deterministically select an audit sample (reproducible, no RNG).

    The order is by ``content_hash([seed, id])`` (a stable pseudo-random total order keyed by the
    seed), and the first ``sample_size`` are taken. The same ids + seed always yield the same
    sample, so the audit is replayable.
    """
    if sample_size <= 0:
        raise LegacyClassificationError("sample_size must be positive")
    ids = list(classification_ids)
    if len(ids) != len(set(ids)):
        raise LegacyClassificationError(
            "duplicate classification ids in audit population"
        )
    ordered = sorted(
        ids, key=lambda cid: write_replay.canonical_json.content_hash([seed, cid])
    )
    return tuple(ordered[:sample_size])


def verify_classification_quality(
    expected_states: dict[str, str],
    verifier_states: dict[str, str],
    *,
    max_mismatch_rate: object,
) -> AuditResult:
    """Sample-verify classification quality against an independent verifier (the audit gate).

    ``expected_states`` maps the SAMPLED classification id -> the backfill's assigned state;
    ``verifier_states`` maps the same ids -> an independent verifier's state. A sample whose
    verifier state is absent or differs is a mismatch. ``mismatch_rate = mismatches / sampled``
    (computed under the deterministic computation profile); the audit PASSES when the rate is at
    or below ``max_mismatch_rate``. Fails closed when a verifier verdict is missing.
    """
    rate_ceiling = _to_unit_decimal("max_mismatch_rate", max_mismatch_rate)
    if not expected_states:
        raise LegacyClassificationError("empty audit sample")

    sampled = tuple(sorted(expected_states))
    mismatches = tuple(
        cid for cid in sampled if verifier_states.get(cid) != expected_states[cid]
    )
    rate = computation.divide(len(mismatches), len(sampled))
    return AuditResult(
        sampled=sampled,
        mismatches=mismatches,
        mismatch_rate=rate,
        passed=rate <= rate_ceiling,
    )


# --- DB / backfill seam (documented) --------------------------------------------------


def backfill_legacy_classifications(session, batch):  # pragma: no cover - DB seam
    """Scan empty-dimension value_contexts + persist classifications (VARCH-8 backfill seam)."""
    raise NotImplementedError(
        "backfill_legacy_classifications is the VARCH-8 backfill seam; the VARCH-7 deliverable "
        "is the pure legacy classification + policy + audit core."
    )
