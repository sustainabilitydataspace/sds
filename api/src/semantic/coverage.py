"""Atomization coverage gate (VARCH-2).

Step 2 of the candidate-v14 implementation order: the coverage gate, BEFORE seed (VARCH-3),
bindings (VARCH-5) and resolver (VARCH-6). This module ships the gate MECHANISM and its
deterministic, scope/tenant-aware, bitemporal computation; real coverage DATA and the
CI make-target wiring (``atomization-coverage-gate``) are deferred to VARCH-3/VARCH-5/VARCH-10.

The gate answers one question for a frozen slice: *does every active atomization subject join
either an approved atomization contract/binding (a "covering provider") or an unexpired
closed-enum exemption?* It is split into a PURE deterministic core (``compute_coverage``,
unit-testable with synthetic inputs and no DB) and a thin DB adapter
(``fetch_coverage_inputs``) that materializes the denominator and providers for a slice.

Design decisions frozen at VARCH-2 discovery (codex BLOCK, items M1-M7):

* **M1/M5 subject-key matrix** — ``SUBJECT_KEY_MATRIX`` freezes, per denominator subject kind,
  the source table, the canonical ``subject_ref`` scheme, the membership predicate, and the
  eligible coverage provider paths. The pure core compares normalized ``(kind, ref, tenant)``
  tuples; the adapter is responsible for producing refs under exactly this scheme so the
  denominator and the providers agree.
* **M2 scope/tenant-aware exemptions** — exemptions (and providers) carry ``scope_kind`` +
  ``tenant_id`` mirroring ``semantic_scope_assignments``; a public/shared subject is only
  covered by a public-tenant row, a tenant-private subject only by a row for the same tenant.
* **M3 decision-time filtering** — a bitemporal row is *effective* for a slice iff
  ``status='published'`` AND ``valid_from <= valid_as_of < valid_to`` (open ``valid_to`` is
  still open) AND ``decision_commit_id <= slice.decision_commit_id``. Later-published rows can
  never retroactively cover an earlier slice.
* **M4 calculation subjects** — ``calculation_contract`` / ``_component`` / ``_dimension`` have
  NO non-exemption provider path at VARCH-2 (binding paths arrive in VARCH-5); the matrix marks
  their only eligible path as ``exemption``. The gate therefore reports them uncovered until a
  later slice — which is correct, not a bug.
* **M6 aggregate-inference safety** — for a non-tenant-authorized (``public``/``shared``)
  evaluation the core never admits ``tenant_private`` subjects/rows and never emits a
  ``tenant_private`` bucket; it only sets the boolean ``redacted_tenant_private`` flag (no
  counts) so a shared caller cannot infer private existence.
* **M7 expiry semantics** — exemption bitemporal effectiveness (valid + decision) is separate
  from expiry; an exemption covers a subject only when it is effective for the slice AND
  ``expires_at > slice.as_of_timestamp``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

# --- Frozen vocabularies --------------------------------------------------------------

PUBLIC_TENANT = "__public__"

SCOPE_PUBLIC = "public"
SCOPE_SHARED = "shared"
SCOPE_TENANT_PRIVATE = "tenant_private"
SCOPE_KINDS = (SCOPE_PUBLIC, SCOPE_SHARED, SCOPE_TENANT_PRIVATE)

# Evaluation scope classes.
EVAL_PUBLIC = "public"  # public-only denominator
EVAL_SHARED = "shared"  # public + shared denominator (default public surface)
EVAL_TENANT = "tenant"  # public + shared + one authorized tenant's private subjects

# Coverage provider paths (which provider table can cover a subject kind).
PATH_ATOMIZATION_CONTRACT = "atomization_contract"  # concept_atomization_contracts
PATH_SYGRIS_BINDING = "sygris_variable_binding"  # sygris_variable_bindings
PATH_COMPONENT_BINDING = "mapping_component_binding"  # mapping_component_bindings
PATH_EXEMPTION = "exemption"  # semantic_closed_enum_exemptions


@dataclass(frozen=True)
class SubjectKindSpec:
    """Frozen per-kind contract for denominator membership and coverage (M1/M4/M5)."""

    table: str
    ref_scheme: str  # how subject_ref is built (the canonical natural key)
    membership_predicate: str  # which rows are "active" for the denominator
    eligible_paths: tuple[str, ...]  # provider paths that may cover this kind


# Subject kinds == the candidate-v14 coverage denominator. Mirrors the migration-038
# ck_semantic_closed_enum_exemptions_subject_kind CHECK list.
SUBJECT_KEY_MATRIX: Mapping[str, SubjectKindSpec] = {
    "concept": SubjectKindSpec(
        "concepts",
        "concepts.uri",
        "concept_state IN ('catalogued','semantically_modelled','calculable')",
        (PATH_ATOMIZATION_CONTRACT, PATH_SYGRIS_BINDING, PATH_EXEMPTION),
    ),
    "canonical_concept": SubjectKindSpec(
        "canonical_concepts",
        "canonical_concepts.canonical_uri",
        "effective_to IS NULL",  # current revision
        (PATH_ATOMIZATION_CONTRACT, PATH_SYGRIS_BINDING, PATH_EXEMPTION),
    ),
    "standard_datapoint": SubjectKindSpec(
        "standard_datapoints",
        "standard_release_id || ':' || code",
        "lifecycle_status = 'active'",
        (PATH_ATOMIZATION_CONTRACT, PATH_EXEMPTION),
    ),
    "mapping_assertion_group": SubjectKindSpec(
        "mapping_assertion_groups",
        "mapping_assertion_groups.id",
        "valid_to IS NULL AND approval_status = 'approved'",
        (PATH_COMPONENT_BINDING, PATH_EXEMPTION),
    ),
    "mapping_assertion_component": SubjectKindSpec(
        "mapping_assertion_components",
        "mapping_assertion_components.id",
        "parent group valid_to IS NULL AND approval_status = 'approved'",
        (PATH_COMPONENT_BINDING, PATH_EXEMPTION),
    ),
    # M4: calculation subjects have NO non-exemption provider path at VARCH-2.
    "calculation_contract": SubjectKindSpec(
        "canonical_calculation_contracts",
        "canonical_calculation_contracts.contract_hash",
        "is_active = true AND effective_to IS NULL",
        (PATH_EXEMPTION,),
    ),
    "calculation_component": SubjectKindSpec(
        "canonical_calculation_components",
        "contract_hash || ':' || component_id",
        "parent contract is_active = true AND effective_to IS NULL",
        (PATH_EXEMPTION,),
    ),
    "calculation_dimension": SubjectKindSpec(
        "canonical_calculation_dimensions",
        "contract_hash || ':' || dimension_id",
        "parent contract is_active = true AND effective_to IS NULL",
        (PATH_EXEMPTION,),
    ),
}

SUBJECT_KINDS = tuple(SUBJECT_KEY_MATRIX.keys())


# --- Pure-core data shapes ------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One active denominator subject for the slice."""

    kind: str
    ref: str
    scope_kind: str = SCOPE_PUBLIC
    tenant_id: str = PUBLIC_TENANT


@dataclass(frozen=True)
class BitemporalRow:
    """A covering provider row carrying the bitemporal fields needed for M3 selection."""

    kind: str
    ref: str
    valid_from: datetime
    decision_commit_id: int
    path: str
    valid_to: datetime | None = None
    status: str = "published"
    scope_kind: str = SCOPE_PUBLIC
    tenant_id: str = PUBLIC_TENANT


@dataclass(frozen=True)
class ExemptionRow(BitemporalRow):
    """A closed-enum exemption row: a bitemporal row plus an expiry (M7)."""

    expires_at: datetime | None = None  # required in practice; checked by the core
    exemption_reason: str = "closed_enum_complete"


@dataclass(frozen=True)
class CoverageSlice:
    """The frozen slice the gate is evaluated against."""

    valid_as_of: datetime
    decision_commit_id: int
    as_of_timestamp: datetime  # read/decision wall-clock for exemption expiry (M7)
    scope_class: str = EVAL_SHARED
    tenant_id: str | None = None  # required iff scope_class == EVAL_TENANT


@dataclass(frozen=True)
class CoverageResult:
    """Deterministic coverage outcome for a slice."""

    total: int
    covered: int
    uncovered: tuple[tuple[str, str], ...]  # sorted (kind, ref)
    coverage_fraction: float
    by_scope: Mapping[str, int]  # covered count per scope bucket (no private bucket)
    exempted: tuple[tuple[str, str], ...]
    redacted_tenant_private: bool = False

    @property
    def is_full(self) -> bool:
        return self.total == self.covered and not self.uncovered


# --- M3 effective-row selection -------------------------------------------------------


def _is_effective(row: BitemporalRow, slc: CoverageSlice) -> bool:
    """Bitemporal effectiveness: published, valid-interval containment, decision <= commit."""
    if row.status != "published":
        return False
    if row.valid_from > slc.valid_as_of:
        return False
    if row.valid_to is not None and slc.valid_as_of >= row.valid_to:
        return False
    return row.decision_commit_id <= slc.decision_commit_id


def _tenant_visible(scope_kind: str, tenant_id: str, slc: CoverageSlice) -> bool:
    """Scope/tenant admission (M2/M6).

    public/shared rows are admitted for every evaluation; tenant_private rows ONLY for an
    EVAL_TENANT evaluation whose tenant_id matches. A non-tenant evaluation never admits any
    tenant_private row or subject.
    """
    if scope_kind in (SCOPE_PUBLIC, SCOPE_SHARED):
        if slc.scope_class == EVAL_PUBLIC:
            return scope_kind == SCOPE_PUBLIC
        return True
    # tenant_private
    return slc.scope_class == EVAL_TENANT and tenant_id == slc.tenant_id


def _covers(row: BitemporalRow, subject: Subject) -> bool:
    """A row covers a subject iff key matches, the row's provider path is eligible for the
    subject kind (M4: ``SUBJECT_KEY_MATRIX[kind].eligible_paths`` is ENFORCED here, not just
    metadata — e.g. a calculation subject is never covered by a non-exemption provider at
    VARCH-2), and the tenant scope is compatible.
    """
    if row.kind != subject.kind or row.ref != subject.ref:
        return False
    spec = SUBJECT_KEY_MATRIX.get(subject.kind)
    if spec is None or row.path not in spec.eligible_paths:
        return False
    if subject.scope_kind in (SCOPE_PUBLIC, SCOPE_SHARED):
        return row.tenant_id == PUBLIC_TENANT
    return row.tenant_id == subject.tenant_id


def compute_coverage(
    subjects: Iterable[Subject],
    providers: Iterable[BitemporalRow],
    exemptions: Iterable[ExemptionRow],
    slc: CoverageSlice,
) -> CoverageResult:
    """Pure, deterministic coverage computation for a frozen slice.

    Raises ``ValueError`` for an EVAL_TENANT slice without a tenant_id.
    """
    if slc.scope_class == EVAL_TENANT and not slc.tenant_id:
        raise ValueError("EVAL_TENANT coverage requires slice.tenant_id")
    if slc.scope_class not in (EVAL_PUBLIC, EVAL_SHARED, EVAL_TENANT):
        raise ValueError(f"unknown scope_class {slc.scope_class!r}")

    # Denominator: admit only scope-visible subjects (M6 — never admit private to a
    # non-tenant evaluation). Track whether any private subject was redacted.
    admitted: list[Subject] = []
    redacted_private = False
    for s in subjects:
        if _tenant_visible(s.scope_kind, s.tenant_id, slc):
            admitted.append(s)
        elif s.scope_kind == SCOPE_TENANT_PRIVATE:
            redacted_private = True

    eff_providers = [
        r
        for r in providers
        if _is_effective(r, slc) and _tenant_visible(r.scope_kind, r.tenant_id, slc)
    ]
    eff_exemptions = [
        e
        for e in exemptions
        if _is_effective(e, slc)
        and _tenant_visible(e.scope_kind, e.tenant_id, slc)
        and e.expires_at is not None
        and e.expires_at > slc.as_of_timestamp  # M7
    ]

    uncovered: list[tuple[str, str]] = []
    exempted: list[tuple[str, str]] = []
    by_scope: dict[str, int] = {}
    covered = 0
    for s in admitted:
        by_contract = any(_covers(r, s) for r in eff_providers)
        by_exempt = (not by_contract) and any(_covers(e, s) for e in eff_exemptions)
        if by_contract or by_exempt:
            covered += 1
            by_scope[s.scope_kind] = by_scope.get(s.scope_kind, 0) + 1
            if by_exempt:
                exempted.append((s.kind, s.ref))
        else:
            uncovered.append((s.kind, s.ref))

    total = len(admitted)
    fraction = 1.0 if total == 0 else covered / total
    return CoverageResult(
        total=total,
        covered=covered,
        uncovered=tuple(sorted(uncovered)),
        coverage_fraction=fraction,
        by_scope=dict(sorted(by_scope.items())),
        exempted=tuple(sorted(exempted)),
        redacted_tenant_private=redacted_private,
    )


class CoverageGateError(AssertionError):
    """Raised when the coverage gate fails (uncovered subjects remain)."""

    def __init__(self, result: CoverageResult) -> None:
        self.result = result
        preview = ", ".join(f"{k}:{r}" for k, r in result.uncovered[:20])
        more = (
            "" if len(result.uncovered) <= 20 else f" (+{len(result.uncovered) - 20})"
        )
        super().__init__(
            f"atomization coverage gate FAILED: {result.covered}/{result.total} covered "
            f"({result.coverage_fraction:.4f}); uncovered: {preview}{more}"
        )


def assert_full_coverage(result: CoverageResult) -> None:
    """Gate assertion: raise unless every admitted subject is covered."""
    if not result.is_full:
        raise CoverageGateError(result)


# --- DB adapter (not unit-tested without a DB; documented seam) ------------------------


def fetch_coverage_inputs(session, slc: CoverageSlice):  # pragma: no cover - DB seam
    """Materialize (subjects, providers, exemptions) for ``slc`` under SUBJECT_KEY_MATRIX.

    This is the integration seam consumed by VARCH-5/VARCH-6 once seed + bindings exist. It is
    intentionally thin and deterministic: it builds ``subject_ref`` for each denominator kind
    under exactly the matrix ``ref_scheme`` and emits providers/exemptions with their raw
    bitemporal fields so the pure core performs the M3/M7 selection. Kept out of unit coverage
    because it requires a live schema with the VARCH tables + legacy denominator tables.
    """
    raise NotImplementedError(
        "fetch_coverage_inputs is the VARCH-5/6 integration seam; the VARCH-2 deliverable "
        "is the pure coverage core + closed-enum exemption table."
    )
