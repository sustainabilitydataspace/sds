"""Fenced decision-time commit ordering (VARCH-4b).

Step 4 (write validation) of candidate-v14, sub-slice (b): the deterministic,
application-side validator for the fenced decision-time commit sequencer. The DB backbone
from migration 032 is the BACKSTOP:

* ``decision_commit_epochs`` — ``ux_one_active_decision_commit_epoch`` (a constant-key
  partial unique on ``status='active'``) allows AT MOST ONE active epoch; ``fence_token`` is
  globally unique; ``commit_id_floor`` carries the cross-epoch ordering floor.
* ``decision_commit_fences`` — ``ux_one_held_fence_per_epoch`` allows at most one ``held``
  fence per epoch; ``(epoch_number, fence_token)`` is unique.
* ``decision_commit_sequence`` — ``commit_id`` is ``GENERATED ALWAYS AS IDENTITY`` (globally
  monotonic), ``previous_commit_id`` is a self-FK with ``ck_..._monotonic`` (prev < commit)
  and ``ux_decision_commit_sequence_previous`` (a partial unique = FORK PREVENTION: no two
  commits may share a predecessor), and a composite FK binds each commit to an existing
  ``(epoch_number, fence_token)`` pair.

This module mirrors those rules so a bad allocation or failover fails fast, deterministically,
BEFORE the DB rejects it, and so the multi-row epoch-failover write is composed correctly.
Like :mod:`src.semantic.coverage` and :mod:`src.semantic.write_temporal`, it is a PURE
deterministic core plus a documented DB seam (VARCH-5/6).

Gates implemented here (mechanism; CI wiring in VARCH-10):

* ``sequencer-continuity-gate`` — a non-genesis commit's ``previous_commit_id`` MUST equal the
  current head; the genesis commit (and only it) has no predecessor. Fork prevention mirrors
  the DB partial unique.
* ``decision-time-global-ordering-gate`` — ``commit_id`` (IDENTITY) is monotonic and
  ``previous_commit_id`` equals the head, so ``previous_commit_id < commit_id`` always holds;
  an epoch failover sets the new epoch's ``commit_id_floor`` to the prior max so later epochs
  order strictly after earlier ones.
* ``decision-time-monotonicity-under-concurrency`` — a commit may be allocated ONLY under the
  single active epoch's single held fence; concurrent allocators are serialized by that fence
  (the one-held-fence partial unique).
* ``decision-time-replay-gate`` — :func:`replay_order` deterministically reconstructs the
  total commit order by walking the predecessor chain; forks, gaps, cycles, and a missing
  genesis are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# --- Frozen vocabularies --------------------------------------------------------------

EPOCH_ACTIVE = "active"
EPOCH_SUPERSEDED = "superseded"

FENCE_HELD = "held"
FENCE_RELEASED = "released"
FENCE_REVOKED = "revoked"

_HEX = frozenset("0123456789abcdef")


class SequencerError(ValueError):
    """Raised when a proposed commit allocation or epoch failover is invalid."""


# --- Pure-core data shapes ------------------------------------------------------------


@dataclass(frozen=True)
class Epoch:
    epoch_number: int
    fence_token: str
    status: str = EPOCH_ACTIVE
    commit_id_floor: int | None = None


@dataclass(frozen=True)
class Fence:
    fence_token: str
    epoch_number: int
    status: str = FENCE_HELD


@dataclass(frozen=True)
class CommitHead:
    """The current chain head (highest commit, the one no commit references as previous)."""

    commit_id: int
    epoch_number: int


@dataclass(frozen=True)
class ProposedCommit:
    epoch_number: int
    fence_token: str
    previous_commit_id: int | None  # None ONLY for the genesis commit
    commit_hash: str | None = None


@dataclass(frozen=True)
class EpochFailoverPlan:
    """A single atomic failover: supersede the old epoch/fence, promote a new active pair."""

    supersede_epoch: int
    superseded_by_epoch: int
    release_fence_token: str
    new_epoch: Epoch  # active, commit_id_floor set
    new_fence: Fence  # held


# --- Pure deterministic core ----------------------------------------------------------


def _is_hex64(value: str) -> bool:
    return len(value) == 64 and all(c in _HEX for c in value)


def validate_commit_allocation(
    active_epoch: Epoch,
    held_fence: Fence,
    head: CommitHead | None,
    proposed: ProposedCommit,
) -> None:
    """Enforce fenced, continuous, single-active allocation BEFORE the DB does.

    ``head`` is ``None`` only for the very first commit (genesis). ``commit_id`` itself is
    not validated here (the DB assigns it via IDENTITY); requiring ``previous_commit_id ==
    head`` guarantees ``previous_commit_id < commit_id`` once the IDENTITY value is issued.
    """
    if active_epoch.status != EPOCH_ACTIVE:
        raise SequencerError(
            f"epoch {active_epoch.epoch_number} is not active (status="
            f"{active_epoch.status!r}); no commit may be allocated"
        )
    if held_fence.status != FENCE_HELD:
        raise SequencerError(
            f"fence {held_fence.fence_token!r} is not held (status={held_fence.status!r})"
        )
    if held_fence.epoch_number != active_epoch.epoch_number:
        raise SequencerError(
            "held fence belongs to a different epoch than the active one"
        )
    if held_fence.fence_token != active_epoch.fence_token:
        raise SequencerError("active epoch's fence_token does not match the held fence")

    if proposed.epoch_number != active_epoch.epoch_number:
        raise SequencerError(
            "monotonicity-under-concurrency: commit must be allocated under the active "
            f"epoch {active_epoch.epoch_number}, got {proposed.epoch_number}"
        )
    if proposed.fence_token != held_fence.fence_token:
        raise SequencerError(
            "commit must claim the held fence_token of the active epoch"
        )

    if head is None:
        # Genesis is valid ONLY at the true global start. The DB partial unique on
        # previous_commit_id covers only NON-NULL values, so it cannot stop a second
        # genesis row; the core therefore rejects head=None once a commit_id_floor has
        # been established (i.e. prior commits exist — the chain must continue, not restart).
        if active_epoch.commit_id_floor not in (None, 0):
            raise SequencerError(
                "sequencer-continuity: genesis (head=None) is only valid at the global "
                f"start, but active epoch carries commit_id_floor="
                f"{active_epoch.commit_id_floor}; allocate off the current head instead"
            )
        if proposed.previous_commit_id is not None:
            raise SequencerError("genesis commit must have previous_commit_id = None")
    else:
        if proposed.previous_commit_id != head.commit_id:
            raise SequencerError(
                "sequencer-continuity: previous_commit_id "
                f"({proposed.previous_commit_id}) must equal the current head "
                f"({head.commit_id}); a different value would fork the chain"
            )

    if proposed.commit_hash is not None and not _is_hex64(proposed.commit_hash):
        raise SequencerError("commit_hash must be 64 lowercase hex chars")


def plan_epoch_failover(
    old_epoch: Epoch,
    old_fence: Fence,
    new_epoch_number: int,
    new_fence_token: str,
    current_max_commit_id: int,
) -> EpochFailoverPlan:
    """Plan an atomic failover to a fresh active epoch + held fence.

    The new epoch's ``commit_id_floor`` is the current max commit, so every commit in the new
    epoch orders strictly after every commit in the old one (decision-time-global-ordering
    across epochs). The caller MUST apply the plan in one transaction, superseding the old
    epoch/fence BEFORE promoting the new pair so the one-active / one-held partial uniques are
    never momentarily violated.
    """
    if old_epoch.status != EPOCH_ACTIVE:
        raise SequencerError("can only fail over from the currently active epoch")
    # The old_fence must genuinely be the active epoch's currently held fence, else the
    # plan would release a stale/wrong token while superseding the active epoch.
    if old_fence.status != FENCE_HELD:
        raise SequencerError("old fence must be currently held to fail over")
    if old_fence.epoch_number != old_epoch.epoch_number:
        raise SequencerError(
            "old fence belongs to a different epoch than the active one"
        )
    if old_fence.fence_token != old_epoch.fence_token:
        raise SequencerError("old fence_token does not match the active epoch's fence")
    if new_epoch_number <= old_epoch.epoch_number:
        raise SequencerError(
            f"new epoch_number ({new_epoch_number}) must exceed the old one "
            f"({old_epoch.epoch_number})"
        )
    if (
        new_fence_token == old_epoch.fence_token
        or new_fence_token == old_fence.fence_token
    ):
        raise SequencerError(
            "new fence_token must differ (fence_token is globally unique)"
        )

    return EpochFailoverPlan(
        supersede_epoch=old_epoch.epoch_number,
        superseded_by_epoch=new_epoch_number,
        release_fence_token=old_fence.fence_token,
        new_epoch=Epoch(
            epoch_number=new_epoch_number,
            fence_token=new_fence_token,
            status=EPOCH_ACTIVE,
            commit_id_floor=current_max_commit_id,
        ),
        new_fence=Fence(
            fence_token=new_fence_token,
            epoch_number=new_epoch_number,
            status=FENCE_HELD,
        ),
    )


def replay_order(commits: Iterable[tuple[int, int | None]]) -> tuple[int, ...]:
    """Deterministically reconstruct the total commit order from ``(commit_id, prev)`` pairs.

    Walks the predecessor chain from the unique genesis (``prev is None``). Raises on a fork
    (two commits sharing a predecessor), a missing/duplicate genesis, a cycle, or a gap
    (disconnected commit). The result is the single global decision-time order.
    """
    by_prev: dict[int | None, int] = {}
    ids: set[int] = set()
    for commit_id, prev in commits:
        if commit_id in ids:
            raise SequencerError(f"duplicate commit_id {commit_id}")
        ids.add(commit_id)
        if prev in by_prev:
            raise SequencerError(
                f"fork: commits {by_prev[prev]} and {commit_id} share predecessor {prev}"
            )
        by_prev[prev] = commit_id

    if not ids:
        return ()
    if None not in by_prev:
        raise SequencerError("no genesis commit (none with previous_commit_id = None)")

    order: list[int] = []
    seen: set[int] = set()
    cursor: int | None = by_prev.get(None)
    while cursor is not None:
        if cursor in seen:
            raise SequencerError(f"cycle detected at commit {cursor}")
        seen.add(cursor)
        order.append(cursor)
        cursor = by_prev.get(cursor)

    if len(order) != len(ids):
        raise SequencerError(
            f"chain is not contiguous: reached {len(order)} of {len(ids)} commits "
            "(gap or disconnected commit)"
        )
    return tuple(order)


# --- DB adapter (not unit-tested without a DB; documented seam) ------------------------


def allocate_commit(session, proposed: ProposedCommit):  # pragma: no cover - DB seam
    """Validate + insert a commit under the active epoch's held fence (VARCH-5/6 seam)."""
    raise NotImplementedError(
        "allocate_commit is the VARCH-5/6 write-path seam; the VARCH-4b deliverable is the "
        "pure fenced-sequencer validation core."
    )


def apply_epoch_failover(
    session, plan: EpochFailoverPlan
):  # pragma: no cover - DB seam
    """Apply an :class:`EpochFailoverPlan` atomically (supersede old, promote new)."""
    raise NotImplementedError(
        "apply_epoch_failover is the VARCH-5/6 write-path seam; the VARCH-4b deliverable is "
        "the pure failover planner."
    )
