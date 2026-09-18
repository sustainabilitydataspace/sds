"""Resolver — resolution context & effective-version-set selection (VARCH-6a).

Step 6 (resolver) of the candidate-v14 implementation order, sub-slice (a): the ENTRY stage
of the deterministic resolver pipeline (contract lines 139-146). It establishes the bitemporal
read coordinates and pins the effective-version-set (EVS) the rest of the pipeline replays.

Like every VARCH slice this is a PURE deterministic core over RESOLVER-MATERIALIZED slice
inputs (the EVS + members, the fenced commit chain, the publication-chain rows, the relation
edges) plus a documented DB-load seam consumed by the VARCH-8 read APIs. It deliberately
COMPOSES the prior cores rather than re-deriving them:

* :mod:`src.semantic.write_sequencer` (4b) — the requested ``decision_commit_id`` MUST be a real
  member of the single fenced global commit order (``replay_order`` reconstructs it and rejects
  forks/gaps/cycles) and may not read a decision that has not been committed yet.
* :mod:`src.semantic.write_evs` (5d) — the selected EVS is fully validated (hash bound to its
  identity + members, members resolve to published versions, manifest valid) before use.
* :mod:`src.semantic.write_temporal` (4a) — the half-open overlap rule is reused to reject any
  valid-time overlap (or gap) in a logical key's published publication chain for the slice.

Resolver contract steps covered here:
    valid_as_of      = value.reporting_period or value.measurement_period
    decision_as_of   = trace > request > latest_committed (new calculation only)
    validate decision_as_of belongs to the fenced global commit sequence
    load effective_version_set(valid_as_of, decision_as_of, scope_context)
    validate effective_version_set hash under pinned canonical serialization profile
    if snapshot missing: return SNAPSHOT_REQUIRED
    validate no valid-time/decision-time overlap or gap in the publication chain
    validate the relation graph for the effective valid/decision slice

Gates contributed here (mechanism; CI wiring in VARCH-10): decision-time-membership (the read
decision is a committed member of the fenced order), evs-selection-pin (the slice resolves to
exactly one fully-valid EVS or SNAPSHOT_REQUIRED), publication-chain-integrity (no overlap/gap),
relation-graph-integrity (edges resolve in-slice and the graph is acyclic).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.semantic import write_evs, write_sequencer
from src.semantic.write_temporal import _overlaps  # half-open overlap (mirrors 4a)

# A far-future sentinel for open (``valid_to IS NULL``) intervals, matching write_temporal.
_OPEN_END = datetime.max.replace(tzinfo=timezone.utc)

# An EVS may back a read only while published (mirrors the _BitemporalVersioned status enum).
_EVS_STATUS_PUBLISHED = "published"

_HEX = frozenset("0123456789abcdef")

# Returned (not raised) when the requested slice has no materialized snapshot; the caller
# either builds one under a locked transaction or surfaces SNAPSHOT_REQUIRED to the client.
SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"


class ResolverError(ValueError):
    """Raised when the resolution context or EVS selection is invalid."""


# --- Pure-core data shapes ------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionRequest:
    """The raw read request before bitemporal coordinates are resolved.

    ``reporting_period`` / ``measurement_period`` give the valid-time anchor (reporting wins).
    The decision-time anchor is resolved by precedence: an existing trace's pin, then an
    explicit request pin, then — ONLY for a brand-new calculation — the latest committed
    decision. A plain read may NEVER fall through to "latest"; that would make historical
    reads non-reproducible.
    """

    scope_context: str
    reporting_period: datetime | None = None
    measurement_period: datetime | None = None
    trace_decision_commit_id: int | None = None
    request_decision_commit_id: int | None = None
    is_new_calculation: bool = False


@dataclass(frozen=True)
class ResolutionContext:
    """The resolved bitemporal read coordinates for the slice."""

    valid_as_of: datetime
    decision_commit_id: int
    scope_context: str


@dataclass(frozen=True)
class PublicationInterval:
    """One published row's valid-time window in a logical key's publication chain."""

    version_id: str
    logical_key: str
    valid_from: datetime
    valid_to: datetime | None
    decision_commit_id: int
    status: str = "published"


@dataclass(frozen=True)
class RelationEdge:
    """A directed relation edge in the effective slice (e.g. concept -> canonical_concept)."""

    src: str
    dst: str


# --- Pure deterministic core ----------------------------------------------------------


def _require_aware(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ResolverError(f"{name} must be timezone-aware: {ts!r}")


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)


def resolve_decision_context(
    request: ResolutionRequest,
    *,
    commit_chain: Iterable[tuple[int, int | None]],
    latest_committed_commit_id: int | None = None,
) -> ResolutionContext:
    """Resolve the bitemporal read coordinates and validate the decision-time anchor.

    ``commit_chain`` is the materialized ``(commit_id, previous_commit_id)`` set of the fenced
    decision-commit sequence; :func:`write_sequencer.replay_order` reconstructs the single total
    order and rejects forks/gaps/cycles/missing-genesis. The resolved ``decision_commit_id`` MUST
    be a member of that order AND not exceed the latest committed commit (no reading a decision
    from the future). ``latest_committed_commit_id`` defaults to the head of the reconstructed
    order when omitted.
    """
    # valid-time anchor: reporting period wins, else measurement period.
    valid_as_of = request.reporting_period or request.measurement_period
    if valid_as_of is None:
        raise ResolverError(
            "no valid-time anchor: request has neither reporting_period nor "
            "measurement_period"
        )
    _require_aware("valid_as_of", valid_as_of)

    # decision-time anchor precedence: trace > request > latest (NEW calculation only).
    if request.trace_decision_commit_id is not None:
        decision_commit_id = request.trace_decision_commit_id
    elif request.request_decision_commit_id is not None:
        decision_commit_id = request.request_decision_commit_id
    elif request.is_new_calculation:
        if latest_committed_commit_id is None:
            raise ResolverError(
                "new calculation requires a latest committed decision commit, but none "
                "was provided"
            )
        decision_commit_id = latest_committed_commit_id
    else:
        raise ResolverError(
            "decision-time anchor unresolved: a non-new-calculation read must pin a "
            "trace or request decision_commit_id (a read may never fall through to "
            "'latest', which would make it non-reproducible)"
        )

    # decision-time membership: the anchor must be a real member of the fenced order.
    order = write_sequencer.replay_order(commit_chain)
    if not order:
        raise ResolverError("empty commit sequence; no decision commit to read at")
    order_set = set(order)
    if decision_commit_id not in order_set:
        raise ResolverError(
            f"decision_commit_id {decision_commit_id} is not a member of the fenced "
            "global commit sequence"
        )
    head = order[-1]
    ceiling = (
        latest_committed_commit_id if latest_committed_commit_id is not None else head
    )
    if ceiling not in order_set:
        raise ResolverError(
            f"latest_committed_commit_id {ceiling} is not a member of the commit sequence"
        )
    # reading a decision strictly after the latest committed one is reading the future.
    if order.index(decision_commit_id) > order.index(ceiling):
        raise ResolverError(
            f"decision_commit_id {decision_commit_id} is later in the fenced order than "
            f"the latest committed decision {ceiling}; cannot read an uncommitted decision"
        )

    return ResolutionContext(
        valid_as_of=valid_as_of,
        decision_commit_id=decision_commit_id,
        scope_context=request.scope_context,
    )


def select_effective_version_set(
    ctx: ResolutionContext,
    *,
    candidate_evs: write_evs.EffectiveVersionSet | None,
    members: Iterable[write_evs.EVSMember] = (),
    replay_manifest: Mapping[str, Any] | None = None,
    resolved_version_index: Mapping[tuple[str, str], frozenset[str]] | None = None,
    expected_subjects: Iterable[tuple[str, str]] | None = None,
    commit_order: Iterable[int] = (),
) -> write_evs.EffectiveVersionSet | str:
    """Select + fully validate the EVS for the slice, or signal ``SNAPSHOT_REQUIRED``.

    Returns the validated EVS when a materialized snapshot exists for the slice, or the
    :data:`SNAPSHOT_REQUIRED` sentinel when ``candidate_evs is None`` (the caller then builds the
    snapshot under a locked transaction). A selected EVS MUST:

    * be ``published`` (a superseded snapshot can never back a read);
    * match the resolution ``scope_context``;
    * be effective at ``valid_as_of`` (its ``[valid_from, valid_to)`` window contains it);
    * carry a content hash (a snapshot without ``evs_hash`` cannot be validated under the
      pinned canonical serialization profile — fail closed, unlike the optional write-path
      check in VARCH-5d ``validate_evs``);
    * be DECIDED at or before the read: its ``decision_commit_id`` must be a member of the
      fenced ``commit_order`` AND not be ordered after the read's ``decision_commit_id``
      (compared by ORDER POSITION, not raw integer, to match the sequencer contract);
    * pass the full VARCH-5d validation (hash bound to identity + members, members resolve to
      published versions, replay manifest valid).

    ``commit_order`` is the replayed fenced commit order from :func:`resolve_decision_context`'s
    ``commit_chain`` (``write_sequencer.replay_order``); it makes the EVS decision-time gate a
    membership proof, not a bare integer compare.
    """
    if candidate_evs is None:
        return SNAPSHOT_REQUIRED
    if replay_manifest is None or resolved_version_index is None:
        raise ResolverError(
            "an EVS was supplied without its replay_manifest / resolved_version_index; "
            "cannot validate the selection"
        )

    if candidate_evs.status != _EVS_STATUS_PUBLISHED:
        raise ResolverError(
            f"EVS {candidate_evs.id!r} is {candidate_evs.status!r}, not published; a "
            "superseded snapshot cannot back a read"
        )
    if candidate_evs.scope_context != ctx.scope_context:
        raise ResolverError(
            f"EVS scope_context {candidate_evs.scope_context!r} != resolution scope "
            f"{ctx.scope_context!r}"
        )
    # valid-time effectiveness: valid_as_of must fall inside the EVS's [from, to) window.
    evs_to = candidate_evs.valid_to if candidate_evs.valid_to is not None else _OPEN_END
    if not (candidate_evs.valid_from <= ctx.valid_as_of < evs_to):
        raise ResolverError(
            f"EVS valid window [{candidate_evs.valid_from}, {candidate_evs.valid_to}) does "
            f"not contain valid_as_of {ctx.valid_as_of}"
        )
    # content-address pin: the snapshot MUST carry a hash to be validated under the profile.
    if not _is_hex64(candidate_evs.evs_hash):
        raise ResolverError(
            f"EVS {candidate_evs.id!r} has no valid 64-hex content hash; cannot validate it "
            "under the pinned canonical serialization profile"
        )
    # decision-time membership + ordering: the snapshot must be decided at or before the read,
    # and both decisions must be committed members of the fenced order.
    order = tuple(commit_order)
    if not order:
        raise ResolverError(
            "select_effective_version_set requires the fenced commit_order to prove the EVS "
            "decision is committed"
        )
    order_index = {cid: i for i, cid in enumerate(order)}
    if candidate_evs.decision_commit_id not in order_index:
        raise ResolverError(
            f"EVS decision_commit_id {candidate_evs.decision_commit_id} is not a member of "
            "the fenced global commit sequence"
        )
    if ctx.decision_commit_id not in order_index:
        raise ResolverError(
            f"read decision_commit_id {ctx.decision_commit_id} is not a member of the fenced "
            "global commit sequence"
        )
    if (
        order_index[candidate_evs.decision_commit_id]
        > order_index[ctx.decision_commit_id]
    ):
        raise ResolverError(
            f"EVS decision_commit_id {candidate_evs.decision_commit_id} is ordered after the "
            f"read decision {ctx.decision_commit_id}; it cannot back this read"
        )

    write_evs.validate_evs(
        candidate_evs,
        members,
        replay_manifest=replay_manifest,
        resolved_version_index=resolved_version_index,
        expected_subjects=expected_subjects,
    )
    return candidate_evs


def validate_publication_chain(
    intervals: Iterable[PublicationInterval],
    *,
    covers_point: datetime | None = None,
    require_contiguous: bool = False,
) -> None:
    """publication-chain-integrity: no valid-time overlap; resolvable at the read point.

    Validates the PUBLISHED rows of ONE logical key:

    * OVERLAP is ALWAYS rejected — two published rows may never overlap in valid time
      (mirrors the 4a published-only EXCLUDE).
    * ``covers_point`` (the read's ``valid_as_of``) is the resolver's real "no gap" guard:
      EXACTLY ONE published row must contain it. Zero rows = a gap AT THE READ SLICE (the read
      is unresolvable); more than one = an overlap. This is the contract's per-slice no-gap
      invariant and does NOT wrongly reject a legitimately SPARSE chain elsewhere in time.
    * ``require_contiguous`` is the stronger whole-chain invariant (no hole anywhere, open tail
      allowed). It is OPT-IN because many keys are legitimately sparse over their full history;
      a caller sets it only for a key contractually required to cover its whole span.
    """
    published = [iv for iv in intervals if iv.status == "published"]
    if not published:
        if covers_point is not None:
            raise ResolverError(
                f"no published version is effective at {covers_point} (gap at read slice)"
            )
        return
    keys = {iv.logical_key for iv in published}
    if len(keys) > 1:
        raise ResolverError(
            f"validate_publication_chain expects one logical key, got {sorted(keys)}"
        )
    ordered = sorted(published, key=lambda iv: iv.valid_from)
    for earlier, later in zip(ordered, ordered[1:]):
        if _overlaps(
            earlier.valid_from, earlier.valid_to, later.valid_from, later.valid_to
        ):
            raise ResolverError(
                f"publication chain for {earlier.logical_key!r} has overlapping published "
                f"rows {earlier.version_id!r} and {later.version_id!r}"
            )
        if require_contiguous:
            # earlier must be closed and meet the next exactly (no gap).
            if earlier.valid_to is None or earlier.valid_to < later.valid_from:
                raise ResolverError(
                    f"publication chain for {earlier.logical_key!r} has a gap before "
                    f"{later.version_id!r}"
                )

    # per-slice resolvability: exactly one published row must contain the read point.
    if covers_point is not None:
        covering = [
            iv
            for iv in published
            if iv.valid_from
            <= covers_point
            < (iv.valid_to if iv.valid_to is not None else _OPEN_END)
        ]
        if not covering:
            raise ResolverError(
                f"no published version is effective at {covers_point} (gap at read slice)"
            )
        # (overlap already rejected above, so len>1 cannot occur here)


def validate_relation_graph(
    edges: Iterable[RelationEdge], *, in_slice_nodes: Iterable[str]
) -> None:
    """relation-graph-integrity: every edge resolves in-slice and the graph is acyclic."""
    nodes = set(in_slice_nodes)
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        if e.src not in nodes:
            raise ResolverError(
                f"relation edge source {e.src!r} is not an in-slice published node"
            )
        if e.dst not in nodes:
            raise ResolverError(
                f"relation edge target {e.dst!r} is not an in-slice published node"
            )
        adjacency.setdefault(e.src, []).append(e.dst)

    # DFS cycle detection over the directed graph.
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes}

    def visit(node: str, stack: tuple[str, ...]) -> None:
        color[node] = GREY
        for nxt in adjacency.get(node, ()):
            if color[nxt] == GREY:
                cycle = " -> ".join(stack + (node, nxt))
                raise ResolverError(f"relation graph has a cycle: {cycle}")
            if color[nxt] == WHITE:
                visit(nxt, stack + (node,))
        color[node] = BLACK

    for n in nodes:
        if color[n] == WHITE:
            visit(n, ())


# --- DB-load seam (documented) --------------------------------------------------------


def load_resolution_inputs(session, request):  # pragma: no cover - DB seam
    """Materialize the slice inputs (EVS, members, commit chain, chains, edges) for a request.

    The VARCH-8 read-path seam: it runs the bitemporal queries that build the inputs the pure
    core validates. The VARCH-6a deliverable is the pure resolution-context + EVS-selection core.
    """
    raise NotImplementedError(
        "load_resolution_inputs is the VARCH-8 read-path seam; the VARCH-6a deliverable is "
        "the pure resolution-context + EVS-selection core."
    )
