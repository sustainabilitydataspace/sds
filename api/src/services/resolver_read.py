"""Resolver read-path DB wiring (VARCH-8c).

Wires the VARCH-6 resolver pure cores (which take materialized inputs) to real bitemporal DB
queries — the ``load_resolution_inputs`` seam made concrete. Follows the repo's established sync
``Session.query(...)`` repository pattern. The pure cores still re-validate everything (hashes,
overlap, decision-time membership); this layer only LOADS, MAPS ORM rows into the resolver's
frozen dataclasses, and calls the cores.

Uniform loads (well-defined across all subjects): the fenced commit chain
(``decision_commit_sequence``), the EVS slice + members
(``semantic_effective_version_sets`` / ``_members``), and the replay manifest
(``semantic_replay_manifests``, whose JSONB columns reconstruct the manifest dict the resolver
validates). The one per-subject piece — the ``resolved_version_index`` (published version ids per
subject) — is heterogeneous (each subject kind is a different product table with its own current-
revision predicate, per ``coverage.SUBJECT_KEY_MATRIX``), so it dispatches per kind and FAILS
CLOSED for a kind whose version source is not yet wired (kinds are wired incrementally;
``canonical_concept`` is the reference implementation against ``canonical_concepts`` revisions).

Gates contributed here (mechanism; CI wiring in VARCH-10): resolver-read-input-materialization,
evs-slice-load-correctness, manifest-reconstruction, resolved-version-index-fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.database.models import CanonicalConcept
from src.database.semantic_models import (
    DecisionCommit,
    SemanticEffectiveVersionSet,
    SemanticEffectiveVersionSetMember,
    SemanticReplayManifest,
)
from src.semantic import resolver, write_sequencer
from src.semantic.resolver import SNAPSHOT_REQUIRED, ResolutionRequest
from src.semantic.write_evs import EffectiveVersionSet, EVSMember


class ResolverReadError(ValueError):
    """Raised when the resolver read path cannot materialize or resolve a slice."""


@dataclass(frozen=True)
class ResolverReadResult:
    """The outcome of resolving a read slice: a validated EVS or SNAPSHOT_REQUIRED."""

    context: resolver.ResolutionContext
    effective_version_set: EffectiveVersionSet | None
    members: tuple[EVSMember, ...]
    replay_manifest: Mapping[str, Any] | None
    snapshot_required: bool


class ResolverRepo(Protocol):
    """The read surface the resolver adapter needs (so the adapter is testable with a fake)."""

    def load_commit_chain(self) -> list[tuple[int, int | None]]: ...
    def latest_committed_commit_id(self) -> int | None: ...
    def load_evs_slice(
        self,
        evs_key: str,
        scope_context: str,
        valid_as_of: datetime,
        decision_commit_id: int,
    ) -> EffectiveVersionSet | None: ...
    def load_evs_members(self, evs_id: str) -> list[EVSMember]: ...
    def load_replay_manifest(self, manifest_ref: str) -> Mapping[str, Any]: ...
    def build_resolved_version_index(
        self,
        members: Iterable[EVSMember],
        valid_as_of: datetime,
        decision_commit_id: int,
    ) -> Mapping[tuple[str, str], frozenset[str]]: ...


class SemanticResolverRepository:
    """Concrete :class:`ResolverRepo` over the live DB (sync, legacy ``.query()`` style)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def load_commit_chain(self) -> list[tuple[int, int | None]]:
        rows = self.db.query(
            DecisionCommit.commit_id, DecisionCommit.previous_commit_id
        ).all()
        return [(int(cid), None if prev is None else int(prev)) for cid, prev in rows]

    def latest_committed_commit_id(self) -> int | None:
        return self.db.query(func.max(DecisionCommit.commit_id)).scalar()

    def load_evs_slice(
        self,
        evs_key: str,
        scope_context: str,
        valid_as_of: datetime,
        decision_commit_id: int,
    ) -> EffectiveVersionSet | None:
        row = (
            self.db.query(SemanticEffectiveVersionSet)
            .filter(
                SemanticEffectiveVersionSet.evs_key == evs_key,
                SemanticEffectiveVersionSet.scope_context == scope_context,
                SemanticEffectiveVersionSet.status == "published",
                SemanticEffectiveVersionSet.valid_from <= valid_as_of,
                or_(
                    SemanticEffectiveVersionSet.valid_to.is_(None),
                    SemanticEffectiveVersionSet.valid_to > valid_as_of,
                ),
                SemanticEffectiveVersionSet.decision_commit_id <= decision_commit_id,
            )
            .order_by(SemanticEffectiveVersionSet.decision_commit_id.desc())
            .first()
        )
        return None if row is None else _map_evs(row)

    def load_evs_members(self, evs_id: str) -> list[EVSMember]:
        rows = (
            self.db.query(SemanticEffectiveVersionSetMember)
            .filter(
                SemanticEffectiveVersionSetMember.effective_version_set_id == evs_id
            )
            .all()
        )
        return [_map_member(r) for r in rows]

    def load_replay_manifest(self, manifest_ref: str) -> Mapping[str, Any]:
        row = (
            self.db.query(SemanticReplayManifest)
            .filter(
                SemanticReplayManifest.manifest_id == manifest_ref,
                SemanticReplayManifest.status == "active",
            )
            .order_by(SemanticReplayManifest.version.desc())
            .first()
        )
        if row is None:
            raise ResolverReadError(
                f"no active replay manifest for ref {manifest_ref!r}"
            )
        return {
            "manifest_id": row.manifest_id,
            "manifest_version": row.version,
            "evaluation_order": row.evaluation_order,
            "profile_tuple": row.profile_tuple,
            "transition_policy": row.transition_policy,
        }

    def build_resolved_version_index(
        self,
        members: Iterable[EVSMember],
        valid_as_of: datetime,
        decision_commit_id: int,
    ) -> Mapping[tuple[str, str], frozenset[str]]:
        index: dict[tuple[str, str], frozenset[str]] = {}
        for kind, ref in {(m.subject_kind, m.subject_ref) for m in members}:
            index[(kind, ref)] = self._published_versions(kind, ref, valid_as_of)
        return index

    def _published_versions(
        self, subject_kind: str, subject_ref: str, valid_as_of: datetime
    ) -> frozenset[str]:
        if subject_kind == "canonical_concept":
            # canonical_concepts revisions effective at valid_as_of (effective_to NULL = current).
            # canonical_concepts.effective_from/to are NAIVE UTC; normalize the aware request to
            # the UTC INSTANT before dropping tz so a non-UTC aware valid_as_of compares the
            # instant, not local wall-clock (codex 8c M1).
            naive = valid_as_of.astimezone(timezone.utc).replace(tzinfo=None)
            rows = (
                self.db.query(
                    CanonicalConcept.id,
                    CanonicalConcept.effective_from,
                    CanonicalConcept.effective_to,
                )
                .filter(CanonicalConcept.canonical_uri == subject_ref)
                .all()
            )
            return frozenset(
                str(cid)
                for cid, eff_from, eff_to in rows
                if eff_from <= naive and (eff_to is None or naive < eff_to)
            )
        raise ResolverReadError(
            f"resolved-version source is not wired for subject_kind {subject_kind!r}; "
            "kinds are wired incrementally in VARCH-8"
        )


def _map_evs(row: SemanticEffectiveVersionSet) -> EffectiveVersionSet:
    return EffectiveVersionSet(
        id=row.id,
        evs_key=row.evs_key,
        scope_context=row.scope_context,
        replay_manifest_ref=row.replay_manifest_ref,
        valid_from=row.valid_from,
        decision_commit_id=row.decision_commit_id,
        valid_to=row.valid_to,
        status=row.status,
        evs_hash=row.evs_hash,
    )


def _map_member(row: SemanticEffectiveVersionSetMember) -> EVSMember:
    return EVSMember(
        effective_version_set_id=row.effective_version_set_id,
        scope_context=row.scope_context,
        subject_kind=row.subject_kind,
        subject_ref=row.subject_ref,
        resolved_version_id=row.resolved_version_id,
        member_hash=row.member_hash,
    )


def resolve_read_context(
    repo: ResolverRepo,
    *,
    evs_key: str,
    scope_context: str,
    reporting_period: datetime | None = None,
    measurement_period: datetime | None = None,
    trace_decision_commit_id: int | None = None,
    request_decision_commit_id: int | None = None,
    is_new_calculation: bool = False,
) -> ResolverReadResult:
    """Materialize a read slice and resolve it through the VARCH-6 cores.

    Loads the fenced commit chain, resolves the bitemporal coordinates (6a
    ``resolve_decision_context``), loads the EVS slice for ``(evs_key, scope_context)`` — returning
    a SNAPSHOT_REQUIRED result when none is materialized — then loads its members + replay manifest
    + the resolved-version index and validates the selection (6a ``select_effective_version_set``,
    which re-checks status/scope/window/hash/decision-position and the full 5d EVS validation).
    """
    chain = repo.load_commit_chain()
    latest = repo.latest_committed_commit_id()
    context = resolver.resolve_decision_context(
        ResolutionRequest(
            scope_context=scope_context,
            reporting_period=reporting_period,
            measurement_period=measurement_period,
            trace_decision_commit_id=trace_decision_commit_id,
            request_decision_commit_id=request_decision_commit_id,
            is_new_calculation=is_new_calculation,
        ),
        commit_chain=chain,
        latest_committed_commit_id=latest,
    )

    evs = repo.load_evs_slice(
        evs_key, scope_context, context.valid_as_of, context.decision_commit_id
    )
    if evs is None:
        return ResolverReadResult(
            context=context,
            effective_version_set=None,
            members=(),
            replay_manifest=None,
            snapshot_required=True,
        )

    members = repo.load_evs_members(evs.id)
    manifest = repo.load_replay_manifest(evs.replay_manifest_ref)
    index = repo.build_resolved_version_index(
        members, context.valid_as_of, context.decision_commit_id
    )
    commit_order = write_sequencer.replay_order(chain)

    selected = resolver.select_effective_version_set(
        context,
        candidate_evs=evs,
        members=members,
        replay_manifest=manifest,
        resolved_version_index=index,
        commit_order=commit_order,
    )
    # select returns SNAPSHOT_REQUIRED only when candidate_evs is None (not here), else the EVS.
    if selected == SNAPSHOT_REQUIRED:  # pragma: no cover - defensive
        return ResolverReadResult(context, None, tuple(members), manifest, True)
    return ResolverReadResult(
        context=context,
        effective_version_set=selected,
        members=tuple(members),
        replay_manifest=manifest,
        snapshot_required=False,
    )
