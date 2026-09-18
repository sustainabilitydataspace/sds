"""Disclosure suppression pins & deterministic suppression outcomes (VARCH-4e).

Step 4 (write validation) of candidate-v14, sub-slice (e): the deterministic disclosure-control
core for public/shared aggregate and count outputs, implementing the ratified
``sds:profile:aggregate-disclosure:v1`` profile. Every suppression outcome is deterministic and
pins the disclosure profile id/version/hash, so a release ledger / trace / outbox event can be
replayed to exactly the same suppression set.

Like the sibling cores this is PURE + deterministic; persisting the release ledger and the
suppression-decision pins is the VARCH-5/6 seam. The k-threshold and the concrete profile pin
are inputs (they come from a concrete ``aggregate_disclosure_policies`` row at runtime).

Gates implemented here (mechanism; CI wiring in VARCH-10):

* ``aggregate-disclosure-control-gate`` — a cell whose count is below ``k_threshold`` is
  suppressed (primary k-anonymity).
* ``suppression-outcome-replay-gate`` — decisions are emitted in a deterministic cell ordering,
  so re-running :func:`decide_suppression` on the same cells + policy yields an identical set.
* ``disclosure-profile-pin-gate`` — :func:`assert_disclosure_profile_pinned` rejects a pin whose
  ``(profile_id, version, hash)`` tuple has drifted from the registry, and every result carries
  the pin it was computed under.
* ``trace-existence-side-channel-gate`` — :func:`redact_projection` emits a UNIFORM redaction
  marker for every suppressed cell (no count, no range, no suppression reason), so the public
  projection cannot reveal why a cell was suppressed or that its count was just below k.
* ``longitudinal-differencing-gate`` — complementary suppression guarantees >=2 suppressed cells
  per group (a single suppressed cell is recoverable from the group total), and
  :func:`assert_suppression_stable` rejects a cell that flips suppressed<->published across
  adjacent releases without an explicit restatement (``prospective_unless_explicit_restatement``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from src.semantic.profiles import registry

DISCLOSURE_PROFILE_ID = "sds:profile:aggregate-disclosure:v1"

# CONFORMANCE BOUNDARY (M1): this core's complementary suppression is sound only for a DISJOINT
# group partition — every cell belongs to exactly one published group total (enforced by the
# single ``Cell.group_key``). It does NOT model crosscutting/marginal releases where one cell
# participates in multiple published totals (e.g. row AND column marginals); such a release can
# still be solved across totals and requires a global suppression solver (deferred). Every
# result declares this contract so the ledger/resolver seam knows the guarantee scope.
PARTITION_CONTRACT = "disjoint_groups"

REASON_BELOW_K = "below_k"
REASON_COMPLEMENTARY = "complementary"
REASON_BELOW_K_SINGLETON = "below_k_singleton"  # suppressed, no complement possible

# A uniform public marker for any suppressed cell: identical regardless of WHY it was
# suppressed, so the projection leaks neither the count nor the suppression reason.
REDACTION_MARKER = {"disclosure": "suppressed"}


class DisclosureError(ValueError):
    """Raised when a suppression decision or disclosure pin is invalid."""


@dataclass(frozen=True)
class DisclosureProfilePin:
    profile_id: str
    version: str
    hash: str


@dataclass(frozen=True)
class Cell:
    """One aggregate/count cell: its key, its count, and the group it belongs to."""

    cell_key: str
    count: int
    group_key: str


@dataclass(frozen=True)
class SuppressionDecision:
    cell_key: str
    suppressed: bool
    reason: str  # internal/audit only — NEVER exposed in a public projection


@dataclass(frozen=True)
class SuppressionResult:
    decisions: tuple[SuppressionDecision, ...]  # sorted by cell_key (deterministic)
    profile_pin: DisclosureProfilePin
    k_threshold: int
    # Groups whose TOTAL must not be published because doing so would reveal a suppressed cell
    # (a group with exactly one suppressed cell — i.e. a below-k singleton). Machine-checkable
    # so the ledger/resolver seam fails closed instead of relying on caller discipline (M2).
    non_publishable_group_totals: frozenset[str] = frozenset()
    partition_contract: str = PARTITION_CONTRACT

    def suppressed_keys(self) -> frozenset[str]:
        return frozenset(d.cell_key for d in self.decisions if d.suppressed)


def assert_disclosure_profile_pinned(pin: DisclosureProfilePin) -> None:
    """Reject a disclosure pin whose full tuple has drifted from the registry."""
    entry = registry.list_profiles().get(pin.profile_id)
    if entry is None:
        raise DisclosureError(f"unknown disclosure profile {pin.profile_id!r}")
    if pin.version != entry["version"]:
        raise DisclosureError(
            f"disclosure profile version drift: {pin.version} != {entry['version']}"
        )
    if pin.hash != entry["hash"]:
        raise DisclosureError(
            f"disclosure profile hash drift: {pin.hash} != {entry['hash']}"
        )


def decide_suppression(
    cells: Iterable[Cell],
    *,
    k_threshold: int,
    profile_pin: DisclosureProfilePin,
    check_pin: bool = True,
) -> SuppressionResult:
    """Deterministically decide which cells to suppress under k-anonymity + complementary rule.

    1. primary: suppress any cell with ``count < k_threshold``;
    2. complementary: in any group with >=2 cells where exactly one cell is suppressed, also
       suppress the smallest-count remaining cell (tie-break by cell_key) so a suppressed value
       cannot be recovered from the group total.

    Decisions are returned sorted by ``cell_key`` for replay determinism.
    """
    if k_threshold < 1:
        raise DisclosureError("k_threshold must be >= 1")
    if check_pin:
        assert_disclosure_profile_pinned(profile_pin)

    cell_list = list(cells)
    keys = [c.cell_key for c in cell_list]
    if len(keys) != len(set(keys)):
        raise DisclosureError("duplicate cell_key in input")

    suppressed: dict[str, str] = {}
    for c in cell_list:
        if c.count < k_threshold:
            suppressed[c.cell_key] = REASON_BELOW_K

    # group cells deterministically
    groups: dict[str, list[Cell]] = {}
    for c in cell_list:
        groups.setdefault(c.group_key, []).append(c)

    for group_key in sorted(groups):
        members = sorted(groups[group_key], key=lambda c: c.cell_key)
        supp_members = [c for c in members if c.cell_key in suppressed]
        if len(members) < 2:
            # a singleton group cannot be complemented; flag it (caller must not publish a
            # group total that equals the single suppressed cell).
            for c in supp_members:
                suppressed[c.cell_key] = REASON_BELOW_K_SINGLETON
            continue
        if len(supp_members) == 1:
            candidates = [c for c in members if c.cell_key not in suppressed]
            # smallest count, then cell_key — deterministic complementary choice
            victim = min(candidates, key=lambda c: (c.count, c.cell_key))
            suppressed[victim.cell_key] = REASON_COMPLEMENTARY

    # A group with exactly one suppressed cell (only reachable for a below-k singleton, since
    # complementary suppression forces >=2 in multi-cell groups) cannot publish its total
    # without revealing the suppressed cell.
    non_publishable = frozenset(
        group_key
        for group_key, members in groups.items()
        if sum(1 for c in members if c.cell_key in suppressed) == 1
    )

    decisions = tuple(
        SuppressionDecision(
            cell_key=c.cell_key,
            suppressed=c.cell_key in suppressed,
            reason=suppressed.get(c.cell_key, ""),
        )
        for c in sorted(cell_list, key=lambda c: c.cell_key)
    )
    return SuppressionResult(
        decisions=decisions,
        profile_pin=profile_pin,
        k_threshold=k_threshold,
        non_publishable_group_totals=non_publishable,
        partition_contract=PARTITION_CONTRACT,
    )


def redact_projection(
    result: SuppressionResult, values: Mapping[str, object]
) -> dict[str, object]:
    """Build the public projection: a UNIFORM redaction marker for each suppressed cell.

    The marker is identical for every suppressed cell (no count, no range, no reason), so the
    projection reveals neither the suppressed value nor why it was suppressed.

    STRICT (M3): ``values`` keys MUST equal the suppression decision domain exactly. Omitting a
    suppressed cell or adding an unscored cell would itself leak existence/trace information, so
    a mismatch fails closed rather than silently publishing.
    """
    decision_keys = {d.cell_key for d in result.decisions}
    value_keys = set(values)
    if value_keys != decision_keys:
        raise DisclosureError(
            "redact_projection requires values to cover exactly the decision domain; "
            f"missing={sorted(decision_keys - value_keys)} "
            f"extra={sorted(value_keys - decision_keys)}"
        )
    out: dict[str, object] = {}
    suppressed = result.suppressed_keys()
    for cell_key, value in values.items():
        out[cell_key] = dict(REDACTION_MARKER) if cell_key in suppressed else value
    return out


def assert_suppression_stable(
    previous: SuppressionResult,
    current: SuppressionResult,
    *,
    restated_cells: frozenset[str] = frozenset(),
) -> None:
    """Reject a cell that flips suppressed<->published across adjacent releases.

    Disclosure changes are prospective: a cell suppressed in one release must stay suppressed in
    the adjacent release (and vice versa) UNLESS it is in ``restated_cells`` (an explicit
    restatement/tombstone). Otherwise an attacker can recover the value by differencing the two
    releases' group totals.
    """
    prev = previous.suppressed_keys()
    curr = current.suppressed_keys()
    flipped = (prev ^ curr) - restated_cells
    if flipped:
        raise DisclosureError(
            "longitudinal-differencing: cells changed suppression across adjacent releases "
            f"without an explicit restatement: {sorted(flipped)}"
        )


# --- DB seam (documented) -------------------------------------------------------------


def apply_suppression_and_ledger(session, result):  # pragma: no cover - DB seam
    """Persist suppression decision pins + the release-ledger entry (VARCH-5/6 seam)."""
    raise NotImplementedError(
        "apply_suppression_and_ledger is the VARCH-5/6 write-path seam; the VARCH-4e "
        "deliverable is the pure deterministic suppression core."
    )
