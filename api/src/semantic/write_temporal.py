"""Bitemporal write-integrity validation (VARCH-4a).

Step 4 (write validation) of the candidate-v14 implementation order, sub-slice (a):
the deterministic, application-side checks that GUARD a versioned bitemporal write
before it reaches the DB, plus the deterministic plan that performs a *successor
publication* as one atomic supersede+insert.

The DB constraints/triggers from migrations 032/033/035 are the BACKSTOP:

* ``sds_reject_publication_mutation`` (032, ``semantic_publication_chains``) and
  ``sds_reject_versioned_mutation`` (033, the ``_BitemporalVersioned`` tables) make a
  published row append-only: it may transition ONLY ``published -> superseded`` and may
  change ONLY ``status`` plus its successor-pointer column. Every other field —
  crucially including ``valid_to`` — is immutable.
* the published-row no-overlap EXCLUDE (``WHERE status = 'published'``) forbids two
  *published* rows for the same logical key from overlapping in valid time.

Because ``valid_to`` is immutable, supersession is a DECISION-TIME operation, not a
valid-time abutment: the predecessor keeps its original valid window and is merely
flagged superseded (which removes it from the published-only overlap check), while the
successor is inserted as a new published row carrying a later ``decision_commit_id``.
This module is the pre-write validator so a violation fails fast with a meaningful
error, and so the two-row successor write is composed correctly and reproducibly.

Like :mod:`src.semantic.coverage`, it is split into a PURE deterministic core
(``validate_temporal_window``, ``assert_published_immutable``,
``plan_successor_publication``) unit-testable with synthetic inputs and no DB, plus a
thin DB seam (``apply_successor_publication``) deferred to VARCH-5/6.

Gates implemented here (mechanism; CI make-target wiring lands in VARCH-10):

* ``temporal-window-integrity-gate`` — every timestamp is timezone-aware and
  ``valid_to`` is open (``None``) or strictly after ``valid_from``.
* ``published-version-immutability-gate`` — mirrors the 032/033 triggers: a published
  row may change ONLY ``status`` (``published -> superseded``) and its successor-pointer
  column; ``valid_to`` and all other fields are immutable.
* ``successor-publication-atomicity-gate`` — ``plan_successor_publication`` emits ONE
  plan holding both the supersede op and the insert op; the caller MUST apply both in a
  single transaction (the DB seam does so). A first version yields an insert-only plan.
* ``write-skew-window-invariant-gate`` — the successor's published valid window must not
  overlap any OTHER published sibling for the same logical key (the predecessor is
  excluded because the same-transaction supersede removes it from ``published``). This
  mirrors the DB EXCLUDE so the violation is caught before the constraint fires.
* ``bitemporal-reproducibility-gate`` — a successor's ``decision_commit_id`` is strictly
  greater than its predecessor's, so replay at a fixed decision commit is deterministic
  (a later decision can never reorder before an earlier one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

# --- Frozen vocabularies --------------------------------------------------------------

STATUS_PUBLISHED = "published"
STATUS_SUPERSEDED = "superseded"

# Fields a published row may legally change, by table family (mirrors the triggers).
#   _BitemporalVersioned tables (033): status + superseded_by_version_id.
#   semantic_publication_chains (032): status + superseded_by_commit_id.
MUTABLE_ON_SUPERSEDE_VERSIONED = frozenset({"status", "superseded_by_version_id"})
MUTABLE_ON_SUPERSEDE_PUBLICATION = frozenset({"status", "superseded_by_commit_id"})

# A far-future sentinel for open (``valid_to IS NULL``) intervals in overlap math.
_OPEN_END = datetime.max.replace(tzinfo=timezone.utc)


class WriteValidationError(ValueError):
    """Raised when a proposed bitemporal write violates a VARCH-4a invariant."""


# --- Pure-core data shapes ------------------------------------------------------------


@dataclass(frozen=True)
class PublishedVersion:
    """An existing published row in a version chain (the predecessor)."""

    version_id: str  # row PK
    logical_key: str  # natural key shared across versions (e.g. axis_key)
    valid_from: datetime
    decision_commit_id: int
    valid_to: datetime | None = None
    status: str = STATUS_PUBLISHED


@dataclass(frozen=True)
class ProposedVersion:
    """A new version to publish (the successor, or the first version in a chain)."""

    version_id: str
    logical_key: str
    valid_from: datetime
    decision_commit_id: int
    valid_to: datetime | None = None


@dataclass(frozen=True)
class SupersedeOp:
    """Flip a predecessor ``published -> superseded`` and point it at its successor.

    Carries NO ``valid_to``: supersession is decision-time and never edits the window.
    """

    version_id: str
    new_status: str  # always STATUS_SUPERSEDED
    successor_version_id: str


@dataclass(frozen=True)
class InsertOp:
    """Insert a new published row."""

    version: ProposedVersion


@dataclass(frozen=True)
class SuccessorPlan:
    """A single atomic write: optionally supersede the predecessor, then insert.

    ``supersede`` is ``None`` for the first version in a chain (insert-only).
    """

    insert: InsertOp
    supersede: SupersedeOp | None = None

    @property
    def is_first_version(self) -> bool:
        return self.supersede is None


# --- Pure deterministic core ----------------------------------------------------------


def _require_aware(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise WriteValidationError(f"{name} must be timezone-aware: {ts!r}")


def validate_temporal_window(version: PublishedVersion | ProposedVersion) -> None:
    """temporal-window-integrity-gate: tz-aware bounds and ``valid_to > valid_from``."""
    _require_aware("valid_from", version.valid_from)
    if version.valid_to is not None:
        _require_aware("valid_to", version.valid_to)
        if version.valid_to <= version.valid_from:
            raise WriteValidationError(
                f"valid_to ({version.valid_to!r}) must be strictly after valid_from "
                f"({version.valid_from!r}) for {version.logical_key!r}"
            )


def assert_published_immutable(
    old: Mapping[str, object],
    new: Mapping[str, object],
    *,
    mutable_fields: frozenset[str] = MUTABLE_ON_SUPERSEDE_VERSIONED,
) -> None:
    """published-version-immutability-gate.

    Mirrors the 032/033 append-only triggers deterministically: a published row may
    change ONLY the fields in ``mutable_fields`` (status + the successor pointer), the
    status transition must be ``published -> superseded``, and the successor pointer must
    be set. ``valid_to`` and everything else are immutable.

    ``old`` and ``new`` MUST be FULL row images (identical key sets), mirroring the
    triggers' ``to_jsonb(OLD)``/``to_jsonb(NEW)`` whole-row comparison; a partial patch
    dict would silently misclassify the omitted columns as unchanged.
    """
    if set(old) != set(new):
        raise WriteValidationError(
            "assert_published_immutable requires full row images (identical key sets); "
            f"differing keys: {sorted(set(old) ^ set(new))}"
        )
    old_status = old.get("status")
    if old_status != STATUS_PUBLISHED:
        raise WriteValidationError(
            f"row is immutable once {old_status!r}; only a published row may be updated"
        )
    if new.get("status") != STATUS_SUPERSEDED:
        raise WriteValidationError("published row may only transition to 'superseded'")

    # The successor pointer (the non-status member of the mutable set) must be set.
    pointer_fields = mutable_fields - {"status"}
    if not any(new.get(p) is not None for p in pointer_fields):
        raise WriteValidationError(
            f"supersede must set the successor pointer ({sorted(pointer_fields)})"
        )

    changed = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    illegal = changed - mutable_fields
    if illegal:
        raise WriteValidationError(
            f"immutable fields changed on a published row: {sorted(illegal)}"
        )


def _overlaps(
    a_from: datetime, a_to: datetime | None, b_from: datetime, b_to: datetime | None
) -> bool:
    """Half-open ``[from, to)`` overlap; ``None`` end means open (+inf)."""
    a_end = a_to if a_to is not None else _OPEN_END
    b_end = b_to if b_to is not None else _OPEN_END
    return a_from < b_end and b_from < a_end


def plan_successor_publication(
    current: PublishedVersion | None,
    proposed: ProposedVersion,
    published_siblings: Iterable[PublishedVersion] = (),
) -> SuccessorPlan:
    """successor-publication-atomicity-gate + write-skew + reproducibility checks.

    Returns the single atomic plan to publish ``proposed`` as the successor of
    ``current`` (or as the first version when ``current is None``). Raises
    :class:`WriteValidationError` on any invariant violation BEFORE the DB is touched.

    ``published_siblings`` are the OTHER currently-published rows for the same logical
    key; the successor's window must not overlap any of them (the predecessor is allowed
    to overlap because the same-transaction supersede removes it from ``published``).
    """
    validate_temporal_window(proposed)

    pred_id = current.version_id if current is not None else None
    for sib in published_siblings:
        if sib.version_id in (pred_id, proposed.version_id):
            continue
        if sib.status != STATUS_PUBLISHED:
            continue
        if sib.logical_key != proposed.logical_key:
            continue
        if _overlaps(
            proposed.valid_from, proposed.valid_to, sib.valid_from, sib.valid_to
        ):
            raise WriteValidationError(
                f"successor window for {proposed.logical_key!r} overlaps published "
                f"sibling {sib.version_id!r}"
            )

    if current is None:
        return SuccessorPlan(insert=InsertOp(proposed))

    if current.logical_key != proposed.logical_key:
        raise WriteValidationError(
            f"successor logical_key {proposed.logical_key!r} != predecessor "
            f"{current.logical_key!r}"
        )
    if current.status != STATUS_PUBLISHED:
        raise WriteValidationError(
            f"cannot supersede a non-published predecessor (status={current.status!r})"
        )
    if proposed.decision_commit_id <= current.decision_commit_id:
        raise WriteValidationError(
            "bitemporal-reproducibility: successor decision_commit_id "
            f"({proposed.decision_commit_id}) must be strictly greater than predecessor "
            f"({current.decision_commit_id})"
        )

    return SuccessorPlan(
        insert=InsertOp(proposed),
        supersede=SupersedeOp(
            version_id=current.version_id,
            new_status=STATUS_SUPERSEDED,
            successor_version_id=proposed.version_id,
        ),
    )


# --- DB adapter (not unit-tested without a DB; documented seam) ------------------------


def apply_successor_publication(
    session, plan: SuccessorPlan, table
):  # pragma: no cover - DB seam
    """Apply a :class:`SuccessorPlan` atomically against ``table`` for a live session.

    The integration seam consumed by VARCH-5/6 once the write path exists: it MUST run
    the supersede UPDATE and the insert INSERT inside a single transaction so the
    published-only overlap EXCLUDE and the append-only trigger see a consistent state.
    Kept out of unit coverage because it requires a live schema with the VARCH tables.
    """
    raise NotImplementedError(
        "apply_successor_publication is the VARCH-5/6 write-path seam; the VARCH-4a "
        "deliverable is the pure temporal write-integrity core."
    )
