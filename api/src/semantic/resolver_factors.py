"""Resolver — factor & measurement resolution (VARCH-6c).

Step 6 (resolver), sub-slice (c): the factor/measurement stage of the deterministic resolver
pipeline (candidate-v14 resolver contract lines 150-154). Runs after 6b (authorization/
disclosure) and before 6d (numeric evaluation). Pure + deterministic over resolver-materialized
inputs; DB-load is the VARCH-8 seam.

It COMPOSES the converged cores:

* :mod:`src.semantic.write_binding` (5c) — ``validate_calculation_binding`` pins the computation
  profile, factor set (id+version+hash), factor-vintage policy (id+version+hash), confidence
  policy, and replay manifest to materialized live state.
* :mod:`src.semantic.write_scope` (4f) — ``assert_license_valid`` + ``assert_egress_allowed`` for
  the factor source's bitemporal license-rights window, access/rights scope, and egress.

Vocabularies bound to the persisted CHECK constraints (semantic_models.py):
``factor_vintage_selection_policies.policy_kind``
{latest_published, reporting_period_match, fixed_vintage, as_of_decision_time, custom}.

Resolver-contract steps covered here:
    validate required axes and known term IDs
    select factor vintage using the explicit factor_vintage_selection_policy
    validate factor source authority, provenance, license-rights window, access scope, egress
    validate measurement basis, factor set, and unit commensurability
    (the calculation's replay-manifest / computation-profile / factor-set / vintage pins via 5c)

Gates contributed here (mechanism; CI wiring in VARCH-10): required-axis/term integrity,
deterministic-factor-vintage-selection, factor-source-rights, unit-commensurability, and the
selected-vintage-matches-pinned-factor-set cross-check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from src.semantic.write_binding import (
    CalculationBindingPins,
    validate_calculation_binding,
)
from src.semantic.write_scope import (
    LicenseError,
    LicenseGrant,
    assert_egress_allowed,
    assert_license_valid,
)

# factor_vintage_selection_policies.policy_kind (closed enum in the table CHECK).
VINTAGE_LATEST_PUBLISHED = "latest_published"
VINTAGE_REPORTING_PERIOD_MATCH = "reporting_period_match"
VINTAGE_FIXED = "fixed_vintage"
VINTAGE_AS_OF_DECISION_TIME = "as_of_decision_time"
VINTAGE_CUSTOM = "custom"
VINTAGE_POLICY_KINDS = frozenset(
    {
        VINTAGE_LATEST_PUBLISHED,
        VINTAGE_REPORTING_PERIOD_MATCH,
        VINTAGE_FIXED,
        VINTAGE_AS_OF_DECISION_TIME,
        VINTAGE_CUSTOM,
    }
)

_OPEN_END = datetime.max.replace(tzinfo=timezone.utc)


class FactorResolutionError(ValueError):
    """Raised when factor/measurement resolution fails."""


def _canonical_version_int(value: object) -> int:
    """Parse a STRICT canonical base-10 version (the DB column is BigInteger).

    Rejects non-canonical encodings (e.g. ``'01'``, ``'+1'``, ``'1.0'``) so a pin string can
    never compare equal to a different integer version — ``str(int(s)) == s`` must hold.
    """
    s = str(value)
    try:
        parsed = int(s)
    except (TypeError, ValueError):
        raise FactorResolutionError(f"non-canonical factor-set version {value!r}")
    if str(parsed) != s:
        raise FactorResolutionError(
            f"non-canonical factor-set version string {value!r} (expected {parsed})"
        )
    return parsed


# --- required axes & terms ------------------------------------------------------------


def validate_required_axes_and_terms(
    *,
    required_axes: Iterable[str],
    required_axis_terms: Mapping[str, Iterable[str]],
    published_axis_ids: Iterable[str],
    axis_terms: Mapping[str, set[str]],
) -> None:
    """required-axis/term integrity: every required axis is published and each term is known.

    ``required_axes`` are the axes the calculation contract demands; each MUST be in
    ``published_axis_ids`` for the slice. ``required_axis_terms`` maps an axis to the specific
    term ids the calculation pins; each MUST be a published term OF that axis (``axis_terms``).
    """
    published = set(published_axis_ids)
    for axis in required_axes:
        if axis not in published:
            raise FactorResolutionError(
                f"required axis {axis!r} is not published/effective for this slice"
            )
    for axis, terms in required_axis_terms.items():
        if axis not in published:
            raise FactorResolutionError(
                f"required axis {axis!r} (with pinned terms) is not published for this slice"
            )
        known = axis_terms.get(axis, set())
        unknown = sorted(set(terms) - known)
        if unknown:
            raise FactorResolutionError(f"axis {axis!r} has unknown term ids {unknown}")


# --- deterministic factor-vintage selection -------------------------------------------


@dataclass(frozen=True)
class FactorVintageCandidate:
    """One published factor-set vintage eligible for selection."""

    factor_set_id: str
    factor_set_key: str
    factor_set_version: int
    valid_from: datetime
    decision_commit_id: int
    factor_set_hash: str
    valid_to: datetime | None = None
    status: str = "published"


def select_factor_vintage(
    policy_kind: str,
    candidates: Iterable[FactorVintageCandidate],
    *,
    reporting_period: datetime | None = None,
    decision_commit_id: int | None = None,
    fixed_version: int | None = None,
    custom_selection_id: str | None = None,
) -> FactorVintageCandidate:
    """Deterministically pick ONE factor vintage per the explicit selection policy.

    Only ``published`` candidates are eligible. Each ``policy_kind`` is a total, deterministic
    rule; an empty or ambiguous result fails closed (the resolver never guesses a vintage):

    * ``latest_published`` — the highest ``factor_set_version``;
    * ``reporting_period_match`` — the single vintage whose ``[valid_from, valid_to)`` contains
      ``reporting_period`` (0 or >1 is an error);
    * ``fixed_vintage`` — the vintage equal to ``fixed_version``;
    * ``as_of_decision_time`` — the vintage with the greatest ``decision_commit_id`` that is
      ``<= decision_commit_id`` (the decision-time-current one);
    * ``custom`` — not derivable in the pure core; requires an explicit ``custom_selection_id``
      naming exactly one candidate.
    """
    if policy_kind not in VINTAGE_POLICY_KINDS:
        raise FactorResolutionError(f"unknown vintage policy_kind {policy_kind!r}")
    pub = [c for c in candidates if c.status == "published"]
    if not pub:
        raise FactorResolutionError("no published factor-set vintage to select")

    if policy_kind == VINTAGE_LATEST_PUBLISHED:
        top = max(c.factor_set_version for c in pub)
        winners = [c for c in pub if c.factor_set_version == top]
        if len(winners) != 1:
            raise FactorResolutionError(
                f"latest_published is ambiguous: {len(winners)} published vintages share the "
                f"highest version {top}"
            )
        return winners[0]

    if policy_kind == VINTAGE_REPORTING_PERIOD_MATCH:
        if reporting_period is None:
            raise FactorResolutionError(
                "reporting_period_match requires a reporting_period"
            )
        hits = [
            c
            for c in pub
            if c.valid_from
            <= reporting_period
            < (c.valid_to if c.valid_to is not None else _OPEN_END)
        ]
        if len(hits) != 1:
            raise FactorResolutionError(
                f"reporting_period_match resolved {len(hits)} vintages (need exactly 1)"
            )
        return hits[0]

    if policy_kind == VINTAGE_FIXED:
        if fixed_version is None:
            raise FactorResolutionError("fixed_vintage requires a fixed_version")
        hits = [c for c in pub if c.factor_set_version == fixed_version]
        if len(hits) != 1:
            raise FactorResolutionError(
                f"fixed_vintage {fixed_version} resolved {len(hits)} vintages (need 1)"
            )
        return hits[0]

    if policy_kind == VINTAGE_AS_OF_DECISION_TIME:
        if decision_commit_id is None:
            raise FactorResolutionError(
                "as_of_decision_time requires a decision_commit_id"
            )
        eligible = [c for c in pub if c.decision_commit_id <= decision_commit_id]
        if not eligible:
            raise FactorResolutionError(
                "as_of_decision_time: no vintage decided at or before the read decision"
            )
        top = max(c.decision_commit_id for c in eligible)
        winners = [c for c in eligible if c.decision_commit_id == top]
        if len(winners) != 1:
            raise FactorResolutionError(
                f"as_of_decision_time is ambiguous: {len(winners)} vintages share the "
                f"greatest eligible decision_commit_id {top}"
            )
        return winners[0]

    # custom
    if not custom_selection_id:
        raise FactorResolutionError(
            "custom vintage policy requires an explicit custom_selection_id"
        )
    hits = [c for c in pub if c.factor_set_id == custom_selection_id]
    if len(hits) != 1:
        raise FactorResolutionError(
            f"custom_selection_id {custom_selection_id!r} resolved {len(hits)} vintages"
        )
    return hits[0]


# --- factor source rights -------------------------------------------------------------


@dataclass(frozen=True)
class FactorSetProvenance:
    """The selected factor set's provenance, mirroring ``semantic_factor_sets`` columns.

    Binds the applied license to THIS factor set: a license valid for a different dataset/source
    artifact must not be acceptable, so every provenance pin is required and the applied
    ``license_ref`` must equal the factor set's own ``license_ref``.
    """

    source_authority: str
    source_dataset_id: str
    source_dataset_version: str
    evidence_hash: str
    license_ref: str


def validate_factor_source_rights(
    grant: LicenseGrant,
    *,
    provenance: FactorSetProvenance,
    applied_license_ref: str,
    known_authorities: Iterable[str],
    target_scope: str,
    valid_at: datetime,
    decision_at: datetime,
    is_redistribution: bool = False,
    is_derivative: bool = False,
) -> None:
    """factor-source-rights: provenance-bound license + valid window + permitted egress.

    Every ``provenance`` pin (source authority, dataset id+version, evidence hash, license ref)
    MUST be present (fail closed on a missing pin), the ``source_authority`` must be recognized,
    and the ``applied_license_ref`` (the license window actually used) MUST equal the factor
    set's own ``license_ref`` — so a license valid for a DIFFERENT dataset cannot back this
    factor set. Only then are the 4f temporal-window + egress checks applied.
    """
    required = {
        "source_authority": provenance.source_authority,
        "source_dataset_id": provenance.source_dataset_id,
        "source_dataset_version": provenance.source_dataset_version,
        "evidence_hash": provenance.evidence_hash,
        "license_ref": provenance.license_ref,
    }
    missing = sorted(name for name, val in required.items() if not val)
    if missing:
        raise FactorResolutionError(
            f"factor set provenance is incomplete; missing pins {missing}"
        )
    if provenance.source_authority not in set(known_authorities):
        raise FactorResolutionError(
            f"factor source_authority {provenance.source_authority!r} is not a recognized "
            "authority"
        )
    if applied_license_ref != provenance.license_ref:
        raise FactorResolutionError(
            f"applied license_ref {applied_license_ref!r} is not the factor set's licensed "
            f"window {provenance.license_ref!r}; license is not bound to this factor set"
        )
    assert_license_valid(grant, valid_at=valid_at, decision_at=decision_at)
    assert_egress_allowed(
        grant,
        target_scope=target_scope,
        is_redistribution=is_redistribution,
        is_derivative=is_derivative,
    )


# --- measurement / unit commensurability ----------------------------------------------


def assert_measurement_commensurable(
    *,
    value_quantity_kind: str,
    factor_quantity_kind: str,
    value_unit: str,
    factor_unit: str,
    measurement_basis: str,
    allowed_bases: Iterable[str],
    convertible_units: Mapping[str, set[str]],
) -> None:
    """unit-commensurability: the measured quantity and the factor must be commensurable.

    The ``measurement_basis`` (e.g. mass, energy, monetary — supplied by the contract) must be
    allowed; the value and factor must share a ``quantity_kind`` (you cannot multiply across
    incompatible kinds); and both units must belong to that quantity kind's convertible-unit set
    in the registry (``convertible_units`` maps quantity_kind -> {unit refs}).
    """
    if measurement_basis not in set(allowed_bases):
        raise FactorResolutionError(
            f"measurement_basis {measurement_basis!r} is not allowed by the contract"
        )
    if value_quantity_kind != factor_quantity_kind:
        raise FactorResolutionError(
            f"incommensurable quantity kinds: value {value_quantity_kind!r} != factor "
            f"{factor_quantity_kind!r}"
        )
    conv = convertible_units.get(value_quantity_kind, set())
    missing = sorted({value_unit, factor_unit} - conv)
    if missing:
        raise FactorResolutionError(
            f"units {missing} are not registered/convertible within quantity kind "
            f"{value_quantity_kind!r}"
        )


# --- calculation pins (compose 5c) + selected-vintage cross-check ----------------------


def validate_calculation_pins(
    pins: CalculationBindingPins,
    selected_vintage: FactorVintageCandidate,
    *,
    active_contract_hashes: Iterable[str],
    published_factor_sets: dict[str, tuple[str, str]],
    published_vintage_policies: dict[str, tuple[str, str]],
    known_confidence_policies: Iterable[str],
    known_replay_manifests: Iterable[str],
    descriptor: object | None = None,
) -> None:
    """Validate the calculation pin bundle (5c) AND that it pins the SELECTED vintage.

    Composing 5c ``validate_calculation_binding`` checks every pin resolves to materialized live
    state; this additionally cross-checks that the factor set the binding pins is exactly the
    vintage the explicit selection policy chose (id + version + hash), so the calculation cannot
    pin one factor set while the resolver selected another.
    """
    validate_calculation_binding(
        pins,
        active_contract_hashes=active_contract_hashes,
        published_factor_sets=published_factor_sets,
        published_vintage_policies=published_vintage_policies,
        known_confidence_policies=known_confidence_policies,
        known_replay_manifests=known_replay_manifests,
        descriptor=descriptor,
    )
    fs = pins.factor_set
    if fs.factor_set_id != selected_vintage.factor_set_id:
        raise FactorResolutionError(
            f"pinned factor set {fs.factor_set_id!r} != selected vintage "
            f"{selected_vintage.factor_set_id!r}"
        )
    if fs.factor_set_hash != selected_vintage.factor_set_hash:
        raise FactorResolutionError(
            "pinned factor-set hash != selected vintage hash (vintage drift)"
        )
    # canonical base-10 version compare: '01' must NOT equal selected version 1.
    if _canonical_version_int(fs.factor_set_version) != _canonical_version_int(
        selected_vintage.factor_set_version
    ):
        raise FactorResolutionError(
            f"pinned factor-set version {fs.factor_set_version!r} != selected vintage "
            f"version {selected_vintage.factor_set_version!r}"
        )


# --- DB-load seam (documented) --------------------------------------------------------


def load_factor_inputs(session, ctx):  # pragma: no cover - DB seam
    """Materialize factor/axis/measurement inputs for the slice (VARCH-8 read-path seam)."""
    raise NotImplementedError(
        "load_factor_inputs is the VARCH-8 read-path seam; the VARCH-6c deliverable is the pure "
        "factor & measurement resolution core."
    )
