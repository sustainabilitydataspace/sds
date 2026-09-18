"""Effective-version-set (EVS) assembly validation (VARCH-5d).

Step 5 (bind mappings/calculations) of the candidate-v14 implementation order, final sub-slice
(d): the deterministic validator for ``semantic_effective_version_sets`` + their members. An EVS
is the assembled snapshot that pins, for a ``(evs_key, scope_context)`` slice under a replay
manifest, exactly which version of each subject resolves — so a binding's pins (5a/5b/5c) tie
together into one reproducible, content-addressable set the resolver (VARCH-6) replays.

Composes the prior cores: bitemporal versioning + successor planning (4a); the FULL replay-
manifest validation via VARCH-4c ``validate_replay_manifest`` (5c deferred it to here) plus the
EVS↔manifest reference match; content-addressing (4c); subject kinds from the coverage matrix.

Pure + deterministic; persistence is the VARCH-6 resolver/write-path seam.

Gates contributed here: effective-version-set-equivalence (a deterministic digest over the
member set, so identical inputs yield an identical EVS), replay-manifest pin (the EVS binds a
fully-valid manifest), member scope/uniqueness integrity (members share the parent scope_context
— mirroring the composite FK — and resolve each subject at most once), bitemporal pin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from src.semantic import write_replay
from src.semantic.coverage import SUBJECT_KINDS
from src.semantic.write_temporal import (
    ProposedVersion,
    PublishedVersion,
    SuccessorPlan,
    plan_successor_publication,
    validate_temporal_window,
)


class EVSError(ValueError):
    """Raised when an effective-version-set or its members are invalid."""


@dataclass(frozen=True)
class EVSMember:
    """One resolved subject in an EVS: ``subject -> resolved_version_id``."""

    effective_version_set_id: str
    scope_context: str
    subject_kind: str
    subject_ref: str
    resolved_version_id: str
    member_hash: str | None = None


@dataclass(frozen=True)
class EffectiveVersionSet:
    """A ``semantic_effective_version_sets`` row (bitemporal versioned)."""

    id: str
    evs_key: str
    scope_context: str
    replay_manifest_ref: str
    valid_from: datetime
    decision_commit_id: int
    valid_to: datetime | None = None
    status: str = "published"
    evs_hash: str | None = None

    @property
    def logical_key(self) -> str:
        # matches the table's uq/no-overlap identity (evs_key, scope_context)
        return f"{self.evs_key}\x1f{self.scope_context}"


def canonical_evs_digest(evs: EffectiveVersionSet, members: Iterable[EVSMember]) -> str:
    """effective-version-set-equivalence: the EVS content address.

    Binds the EVS IDENTITY (evs_key, scope_context, replay_manifest_ref) together with the member
    set (sorted ``(subject_kind, subject_ref, resolved_version_id)`` tuples). Two assemblies that
    resolve the same subjects to the same versions under the same key/scope/manifest produce the
    SAME digest regardless of input order; different keys/scopes never collide.
    """
    payload = {
        "evs_key": evs.evs_key,
        "scope_context": evs.scope_context,
        "replay_manifest_ref": evs.replay_manifest_ref,
        "members": sorted(
            (m.subject_kind, m.subject_ref, m.resolved_version_id) for m in members
        ),
    }
    return write_replay.canonical_json.content_hash(payload)


def validate_evs(
    evs: EffectiveVersionSet,
    members: Iterable[EVSMember],
    *,
    replay_manifest: Mapping[str, Any],
    resolved_version_index: Mapping[tuple[str, str], frozenset[str]],
    expected_subjects: Iterable[tuple[str, str]] | None = None,
) -> None:
    """Validate an EVS + its members (fail-closed, VARCH-5d).

    ``replay_manifest`` is the FULL manifest the EVS is assembled under; it is validated here via
    VARCH-4c (schema + registry pins) and its ``manifest_id`` must equal the EVS's
    ``replay_manifest_ref``. Members must all share the EVS's ``scope_context`` (mirroring the
    composite FK), belong to this EVS, carry a known subject_kind, resolve each subject at most
    once (mirroring the uq), and each ``resolved_version_id`` must be a PUBLISHED version of that
    subject per ``resolved_version_index`` ``(subject_kind, subject_ref) -> {valid version ids}``
    (resolver-materialized for the exact slice). When ``expected_subjects`` is given, the member
    subject set must equal it exactly (slice-coverage-completeness / no stranded subjects);
    otherwise completeness is deliberately deferred to the VARCH-6 resolver. When ``evs.evs_hash``
    is present it MUST equal :func:`canonical_evs_digest` over the EVS + members.
    """
    members = list(members)
    validate_temporal_window(
        ProposedVersion(
            version_id=evs.id,
            logical_key=evs.logical_key,
            valid_from=evs.valid_from,
            decision_commit_id=evs.decision_commit_id,
            valid_to=evs.valid_to,
        )
    )
    if evs.decision_commit_id <= 0:
        raise EVSError("EVS must carry a positive decision_commit_id")

    # replay-manifest pin: the manifest must be fully valid AND match the EVS reference.
    write_replay.validate_replay_manifest(replay_manifest)
    if replay_manifest.get("manifest_id") != evs.replay_manifest_ref:
        raise EVSError(
            f"EVS replay_manifest_ref {evs.replay_manifest_ref!r} != manifest_id "
            f"{replay_manifest.get('manifest_id')!r}"
        )

    seen: set[tuple[str, str]] = set()
    for m in members:
        if m.effective_version_set_id != evs.id:
            raise EVSError(
                f"member {m.subject_kind}:{m.subject_ref} belongs to EVS "
                f"{m.effective_version_set_id!r}, not {evs.id!r}"
            )
        if m.scope_context != evs.scope_context:
            raise EVSError(
                f"member {m.subject_kind}:{m.subject_ref} scope_context "
                f"{m.scope_context!r} != EVS scope_context {evs.scope_context!r}"
            )
        if m.subject_kind not in SUBJECT_KINDS:
            raise EVSError(f"unknown member subject_kind {m.subject_kind!r}")
        key = (m.subject_kind, m.subject_ref)
        # version-pin-reproducibility: the resolved version must be a published version of
        # exactly this subject for the slice (not a bare/typoed/stale id).
        valid_versions = resolved_version_index.get(key)
        if valid_versions is None or m.resolved_version_id not in valid_versions:
            raise EVSError(
                f"member {key} resolved_version_id {m.resolved_version_id!r} is not a "
                "published version of that subject for this slice"
            )
        if key in seen:
            raise EVSError(f"subject {key} resolved more than once in the EVS")
        seen.add(key)

    # slice-coverage-completeness: no missing / no stranded subjects (when provided).
    if expected_subjects is not None:
        expected = set(expected_subjects)
        if seen != expected:
            raise EVSError(
                f"EVS member set != expected covered subjects; "
                f"missing={sorted(expected - seen)} extra={sorted(seen - expected)}"
            )

    # content address binds identity + member set.
    if evs.evs_hash is not None:
        expected_hash = canonical_evs_digest(evs, members)
        if evs.evs_hash != expected_hash:
            raise EVSError(
                f"evs_hash {evs.evs_hash} != canonical digest {expected_hash}"
            )


def plan_evs_successor(
    current: EffectiveVersionSet | None,
    proposed: EffectiveVersionSet,
    published_siblings: Iterable[EffectiveVersionSet] = (),
) -> SuccessorPlan:
    """Plan an EVS version successor, reusing the 4a successor-publication planner."""

    def _pub(e: EffectiveVersionSet) -> PublishedVersion:
        return PublishedVersion(
            version_id=e.id,
            logical_key=e.logical_key,
            valid_from=e.valid_from,
            decision_commit_id=e.decision_commit_id,
            valid_to=e.valid_to,
            status=e.status,
        )

    return plan_successor_publication(
        _pub(current) if current is not None else None,
        ProposedVersion(
            version_id=proposed.id,
            logical_key=proposed.logical_key,
            valid_from=proposed.valid_from,
            decision_commit_id=proposed.decision_commit_id,
            valid_to=proposed.valid_to,
        ),
        published_siblings=[_pub(s) for s in published_siblings],
    )


# --- DB seam (documented) -------------------------------------------------------------


def persist_evs(session, evs, members, plan):  # pragma: no cover - DB seam
    """Validate + persist an EVS and its members atomically (VARCH-6 write-path seam)."""
    raise NotImplementedError(
        "persist_evs is the VARCH-6 write-path seam; the VARCH-5d deliverable is the pure "
        "EVS assembly validation core."
    )
