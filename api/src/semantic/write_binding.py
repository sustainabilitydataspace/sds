"""Binding validation — sygris variable bindings (VARCH-5a) + mapping component bindings (5b).

Step 5 (bind mappings/calculations) of the candidate-v14 implementation order. Sub-slice (a) is
the validator for ``sygris_variable_bindings`` (bind a Sygris variable to a covered atomization
subject); sub-slice (b) is the validator for ``mapping_component_bindings`` (bind a mapping-
assertion component to a semantic axis + optional term); sub-slice (c) validates a calculation
binding's deterministic PIN BUNDLE (computation profile, factor set version, vintage policy,
confidence policy, replay manifest). All deliberately COMPOSE the VARCH-4 write-validation cores
rather than re-deriving them:

* :mod:`src.semantic.write_temporal` — a binding is a bitemporal versioned row; window integrity
  and successor publication reuse the 4a core (and migration 040's deferrable self-FK).
* :mod:`src.semantic.coverage` — the bound ``(target_kind, target_ref)`` MUST be an active,
  covered denominator subject (slice-coverage pin); target kinds come from ``SUBJECT_KEY_MATRIX``.
* :mod:`src.semantic.write_scope` — the source-rights pin: a binding's source license must be
  temporally in force AND permit the egress SCOPE the binding exposes. NOTE ``projection_flag``
  is a binding-catalog MODE (``sygris_atomization_catalog``/``none``), NOT a scope; the egress
  scope is supplied explicitly to :func:`assert_binding_source_rights`.
* :mod:`src.semantic.write_replay` — ``binding_hash`` is verified as the content hash of the
  binding descriptor (content-addressing pin).

Pure + deterministic; persistence is the VARCH-6 resolver/write-path seam. The effective-
version-set assembly (which ties a binding's pins together) is sub-slice 5d (deferred).

Gates contributed here (mechanism; CI wiring in VARCH-10): slice-coverage pin, bitemporal pin,
scope-projection validity, source-rights pin, binding content-addressing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from src.semantic import write_replay, write_scope
from src.semantic.write_temporal import (
    ProposedVersion,
    PublishedVersion,
    SuccessorPlan,
    plan_successor_publication,
    validate_temporal_window,
)

# Vocabulary MUST match the persisted sygris_variable_bindings CHECK constraints
# (semantic_models.py): target_kind and projection_flag are closed enums there.
BINDING_TARGET_KINDS = frozenset({"concept", "canonical_concept"})
PROJECTION_FLAGS = frozenset({"sygris_atomization_catalog", "none"})


class BindingError(ValueError):
    """Raised when a proposed sygris variable binding is invalid."""


@dataclass(frozen=True)
class SygrisBinding:
    """A proposed/active ``sygris_variable_bindings`` row (bitemporal versioned)."""

    id: str
    sygris_variable_ref: str
    target_kind: str
    target_ref: str
    projection_flag: str
    valid_from: datetime
    decision_commit_id: int
    valid_to: datetime | None = None
    status: str = "published"
    binding_hash: str | None = None

    @property
    def logical_key(self) -> str:
        # one binding chain per (variable, target); versions supersede along it.
        return f"{self.sygris_variable_ref}\x1f{self.target_kind}\x1f{self.target_ref}"


def validate_sygris_binding(
    binding: SygrisBinding,
    *,
    covered_refs: Iterable[tuple[str, str]],
    descriptor: Any | None = None,
) -> None:
    """Validate a sygris variable binding against all VARCH-5a pins (fail-closed).

    ``covered_refs`` is the set of ``(subject_kind, subject_ref)`` the VARCH-2 coverage gate
    reports as active+covered; the bound target MUST be among them (slice-coverage pin). The
    CALLER (the VARCH-6 resolver) MUST compute ``covered_refs`` for the SAME slice context as the
    binding — identical ``valid_as_of``, ``decision_commit_id``, evaluation scope, and tenant —
    or the pin is meaningless. ``descriptor`` (when given with a ``binding_hash``) is the
    canonical binding payload whose content hash must equal ``binding_hash``.
    """
    # bitemporal window integrity (reuse 4a)
    validate_temporal_window(
        ProposedVersion(
            version_id=binding.id,
            logical_key=binding.logical_key,
            valid_from=binding.valid_from,
            decision_commit_id=binding.decision_commit_id,
            valid_to=binding.valid_to,
        )
    )
    if binding.target_kind not in BINDING_TARGET_KINDS:
        raise BindingError(
            f"unknown binding target_kind {binding.target_kind!r}; "
            f"must be one of the coverage subject kinds"
        )
    if binding.projection_flag not in PROJECTION_FLAGS:
        raise BindingError(f"unknown projection_flag {binding.projection_flag!r}")
    if binding.decision_commit_id <= 0:
        raise BindingError("binding must carry a positive decision_commit_id")

    # slice-coverage pin: the bound subject must be an active, covered denominator subject.
    if (binding.target_kind, binding.target_ref) not in set(covered_refs):
        raise BindingError(
            f"binding target ({binding.target_kind}, {binding.target_ref}) is not a covered "
            "atomization subject for this slice (slice-coverage pin)"
        )

    # content-addressing pin (reuse 4c)
    if binding.binding_hash is not None:
        if descriptor is None:
            raise BindingError(
                "binding_hash present but no descriptor supplied to verify it"
            )
        write_replay.verify_content_hash(descriptor, binding.binding_hash)


def assert_binding_source_rights(
    license_grant: write_scope.LicenseGrant,
    *,
    target_scope: str,
    valid_at: datetime,
    decision_at: datetime,
    is_redistribution: bool = False,
    is_derivative: bool = False,
) -> None:
    """source-rights pin: the binding's source license must permit its egress.

    ``projection_flag`` is a binding-catalog mode (``sygris_atomization_catalog``/``none``), NOT
    a scope, so the egress SCOPE the binding exposes is supplied explicitly. This both validates
    the license is temporally in force (window/expiry/revocation — VARCH-4f) AND that the egress
    target scope + redistribution/derivative use are permitted, so an expired/revoked or
    over-broad source cannot back the binding.
    """
    write_scope.assert_license_valid(
        license_grant, valid_at=valid_at, decision_at=decision_at
    )
    write_scope.assert_egress_allowed(
        license_grant,
        target_scope=target_scope,
        is_redistribution=is_redistribution,
        is_derivative=is_derivative,
    )


def plan_binding_successor(
    current: SygrisBinding | None,
    proposed: SygrisBinding,
    published_siblings: Iterable[SygrisBinding] = (),
) -> SuccessorPlan:
    """Plan a binding version successor, reusing the 4a successor-publication planner."""

    def _pub(b: SygrisBinding) -> PublishedVersion:
        return PublishedVersion(
            version_id=b.id,
            logical_key=b.logical_key,
            valid_from=b.valid_from,
            decision_commit_id=b.decision_commit_id,
            valid_to=b.valid_to,
            status=b.status,
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


# --- VARCH-5b: mapping component bindings ---------------------------------------------


@dataclass(frozen=True)
class MappingComponentBinding:
    """A proposed/active ``mapping_component_bindings`` row (bitemporal versioned).

    Binds a mapping-assertion component (``component_ref``, a loose integer reference — no FK)
    to a semantic ``axis_id`` and an OPTIONAL ``term_id`` on that axis.
    """

    id: str
    component_ref: int
    axis_id: str
    valid_from: datetime
    decision_commit_id: int
    term_id: str | None = None
    valid_to: datetime | None = None
    status: str = "published"
    binding_hash: str | None = None

    @property
    def logical_key(self) -> str:
        # matches uq/no-overlap identity (component_ref, axis_id)
        return f"{self.component_ref}\x1f{self.axis_id}"


def validate_mapping_component_binding(
    binding: MappingComponentBinding,
    *,
    approved_component_refs: Iterable[int],
    published_axis_ids: Iterable[str],
    axis_terms: dict[str, set[str]],
    descriptor: Any | None = None,
) -> None:
    """Validate a mapping component binding against all VARCH-5b pins (fail-closed).

    Pins (all inputs are materialized by the VARCH-6 resolver for the binding's exact slice —
    identical valid_as_of / decision_commit_id / scope / tenant): ``approved_component_refs`` are
    the mapping-assertion components ELIGIBLE for binding — i.e. exactly those whose parent
    ``mapping_assertion_groups`` row has ``valid_to IS NULL AND approval_status='approved'`` for
    the slice (the VARCH-2 coverage membership predicate); ``published_axis_ids`` are the axes
    published+effective for the slice; ``axis_terms`` maps each axis_id to its published term_ids.
    A term, when present, MUST belong to the bound axis.
    """
    validate_temporal_window(
        ProposedVersion(
            version_id=binding.id,
            logical_key=binding.logical_key,
            valid_from=binding.valid_from,
            decision_commit_id=binding.decision_commit_id,
            valid_to=binding.valid_to,
        )
    )
    if not isinstance(binding.component_ref, int) or binding.component_ref <= 0:
        raise BindingError("component_ref must be a positive integer")
    if binding.decision_commit_id <= 0:
        raise BindingError("binding must carry a positive decision_commit_id")
    if binding.component_ref not in set(approved_component_refs):
        raise BindingError(
            f"component_ref {binding.component_ref} is not an approved mapping component "
            "for this slice (slice-coverage pin)"
        )
    if binding.axis_id not in set(published_axis_ids):
        raise BindingError(
            f"axis {binding.axis_id!r} is not published/effective for this slice"
        )
    # term-belongs-to-axis pin: an optional term must be a published term OF the bound axis.
    if binding.term_id is not None:
        if binding.term_id not in axis_terms.get(binding.axis_id, set()):
            raise BindingError(
                f"term {binding.term_id!r} does not belong to axis {binding.axis_id!r}"
            )
    if binding.binding_hash is not None:
        if descriptor is None:
            raise BindingError(
                "binding_hash present but no descriptor supplied to verify it"
            )
        write_replay.verify_content_hash(descriptor, binding.binding_hash)


def plan_component_binding_successor(
    current: MappingComponentBinding | None,
    proposed: MappingComponentBinding,
    published_siblings: Iterable[MappingComponentBinding] = (),
) -> SuccessorPlan:
    """Plan a mapping-component-binding version successor, reusing the 4a planner."""

    def _pub(b: MappingComponentBinding) -> PublishedVersion:
        return PublishedVersion(
            version_id=b.id,
            logical_key=b.logical_key,
            valid_from=b.valid_from,
            decision_commit_id=b.decision_commit_id,
            valid_to=b.valid_to,
            status=b.status,
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


# --- VARCH-5c: calculation binding pins ----------------------------------------------


@dataclass(frozen=True)
class FactorSetPin:
    """Pins the exact factor set a calculation binds (id + version + content hash)."""

    factor_set_id: str
    factor_set_version: str
    factor_set_hash: str


@dataclass(frozen=True)
class CalculationBindingPins:
    """The deterministic pin bundle a calculation binding MUST carry.

    A calculation is reproducible only if it pins every input that can change its result: the
    computation profile, the factor set (version+hash), the factor-vintage selection policy, the
    confidence-aggregation policy, and the replay manifest. ``contract_hash`` content-addresses
    the bound calculation contract.
    """

    contract_hash: str
    computation_profile_hash: str
    factor_set: FactorSetPin
    vintage_policy: "VintagePolicyPin"
    confidence_policy: str
    replay_manifest_ref: str
    binding_hash: str | None = None


@dataclass(frozen=True)
class VintagePolicyPin:
    """Pins the exact factor-vintage selection policy (id + version + content hash).

    Symmetric with :class:`FactorSetPin`: ``factor_vintage_selection_policies`` is bitemporal/
    versioned with a ``policy_hash``, so pinning only the id would miss content drift.
    """

    policy_id: str
    policy_version: str
    policy_hash: str


def validate_calculation_binding(
    pins: CalculationBindingPins,
    *,
    active_contract_hashes: Iterable[str],
    published_factor_sets: dict[str, tuple[str, str]],
    published_vintage_policies: dict[str, tuple[str, str]],
    known_confidence_policies: Iterable[str],
    known_replay_manifests: Iterable[str],
    descriptor: Any | None = None,
) -> None:
    """Validate a calculation binding's deterministic pins (fail-closed, VARCH-5c).

    Every input is resolver-materialized for the binding's EXACT slice (same valid_as_of /
    decision_commit_id / scope / tenant). Each pin must resolve to live state — a bare non-empty
    string is never sufficient:

    * ``active_contract_hashes`` — hashes of the active ``canonical_calculation_contracts`` rows;
    * ``published_factor_sets`` / ``published_vintage_policies`` — ``id -> (version, hash)`` of the
      published+effective factor sets / vintage policies;
    * ``known_confidence_policies`` — the valid confidence-aggregation policy ids;
    * ``known_replay_manifests`` — the valid replay-manifest refs (the manifest itself is fully
      validated by VARCH-4c ``validate_replay_manifest`` at assembly/5d).
    """
    # contract pin: must be one of the active calc contracts for the slice
    if pins.contract_hash not in set(active_contract_hashes):
        raise BindingError(
            f"contract_hash {pins.contract_hash!r} is not an active calculation contract "
            "for this slice"
        )
    # computation-profile pin (reuse 4c — must match the frozen registry hash)
    write_replay.verify_computation_profile_pin(pins.computation_profile_hash)

    # factor-set-version pin: published with the exact version+hash
    fs = published_factor_sets.get(pins.factor_set.factor_set_id)
    if fs is None:
        raise BindingError(
            f"factor set {pins.factor_set.factor_set_id!r} is not published/effective for "
            "this slice"
        )
    if (pins.factor_set.factor_set_version, pins.factor_set.factor_set_hash) != fs:
        raise BindingError(
            f"factor set {pins.factor_set.factor_set_id!r} version/hash drift: pinned "
            f"({pins.factor_set.factor_set_version}, {pins.factor_set.factor_set_hash}) != "
            f"published {fs}"
        )

    # factor-vintage-selection pin: symmetric id+version+hash check
    vp = published_vintage_policies.get(pins.vintage_policy.policy_id)
    if vp is None:
        raise BindingError(
            f"factor-vintage policy {pins.vintage_policy.policy_id!r} is not published for "
            "this slice"
        )
    if (pins.vintage_policy.policy_version, pins.vintage_policy.policy_hash) != vp:
        raise BindingError(
            f"factor-vintage policy {pins.vintage_policy.policy_id!r} version/hash drift: "
            f"pinned ({pins.vintage_policy.policy_version}, {pins.vintage_policy.policy_hash})"
            f" != published {vp}"
        )

    # confidence-aggregation policy pin: must be a known policy
    if pins.confidence_policy not in set(known_confidence_policies):
        raise BindingError(
            f"unknown confidence-aggregation policy {pins.confidence_policy!r}"
        )
    # replay-manifest pin: must resolve to a known manifest ref
    if pins.replay_manifest_ref not in set(known_replay_manifests):
        raise BindingError(
            f"replay_manifest_ref {pins.replay_manifest_ref!r} does not resolve to a known "
            "replay manifest for this slice"
        )

    if pins.binding_hash is not None:
        if descriptor is None:
            raise BindingError(
                "binding_hash present but no descriptor supplied to verify it"
            )
        write_replay.verify_content_hash(descriptor, pins.binding_hash)


# --- DB seam (documented) -------------------------------------------------------------


def persist_binding(session, binding, plan):  # pragma: no cover - DB seam
    """Validate + persist a binding atomically (VARCH-6 write-path seam; 5a/5b/5c)."""
    raise NotImplementedError(
        "persist_binding is the VARCH-6 write-path seam; the VARCH-5 deliverable is the pure "
        "binding validation core composing the VARCH-4 cores."
    )
