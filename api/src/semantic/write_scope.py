"""Scope projection, consolidation composition & license-aware egress (VARCH-4f).

Step 4 (write validation) of candidate-v14, sub-slice (f): the deterministic decision core for
the three egress-boundary concerns, implementing the ratified
``sds:profile:consolidation-composition:v1`` and ``sds:profile:license-rights-window:v1``
profiles and reusing the VARCH-2 scope vocabulary (:mod:`src.semantic.coverage`).

Like the sibling cores it is PURE + deterministic; the actual scope-projected / disclosure-
filtered / license-aware OUTBOX and persistence are the VARCH-5/6 seam.

Gates implemented here (mechanism; CI wiring in VARCH-10):

* ``tenant-scope-isolation-gate`` — :func:`is_visible`: a tenant_private row is visible ONLY to
  an evaluation authorized for that exact tenant; public/shared rows follow the eval scope.
* ``scope-transition-no-retroactive-exposure`` — :func:`effective_scope_at`: a scope change is
  decision-time prospective; a read at a decision commit BEFORE the transition sees the prior
  scope, so widening a row's scope can never retroactively expose it to an earlier slice.
* ``cross-tenant-consolidation-authorization-gate`` — :func:`assert_consolidation_authorized`:
  every member must explicitly consent or the consolidation is blocked.
* ``consolidation-composition-profile-gate`` — :func:`compose_disclosure` (fail-closed
  most-restrictive, blocking incomparable-lattice profiles, unless a group profile is
  unanimously authorized AND at least as restrictive as every member) and
  :func:`compose_license_scope` (intersection-only; empty blocks output).
* ``license-rights-temporal-window-gate`` — :func:`assert_license_valid`: the egress time must
  fall in the license valid + decision windows and the license must be unexpired/unrevoked.
* ``license-aware-egress-gate`` — :func:`assert_egress_allowed`: a derived output is blocked
  when the source access scope / redistribution / derivative grants do not permit the target;
  :func:`egress_reevaluation` mechanizes the three ``temporal_egress_policy`` modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from src.semantic.coverage import (
    EVAL_PUBLIC,
    EVAL_SHARED,
    EVAL_TENANT,
    SCOPE_PUBLIC,
    SCOPE_SHARED,
    SCOPE_TENANT_PRIVATE,
)

# Higher rank == more restrictive (fail-closed composition picks the max).
SCOPE_RANK: Mapping[str, int] = {
    SCOPE_PUBLIC: 0,
    SCOPE_SHARED: 1,
    SCOPE_TENANT_PRIVATE: 2,
}

# temporal_egress_policy modes (from the ratified license-rights-window schema).
EGRESS_TRACE_DECISION_TIME = "trace_decision_time"
EGRESS_READ_TIME = "egress_read_time"
EGRESS_RETROACTIVE_RESTATEMENT = "retroactive_restatement"
EGRESS_POLICIES = (
    EGRESS_TRACE_DECISION_TIME,
    EGRESS_READ_TIME,
    EGRESS_RETROACTIVE_RESTATEMENT,
)


class ScopeError(ValueError):
    """Raised on a tenant-scope / scope-transition violation."""


class ConsolidationError(ValueError):
    """Raised when cross-tenant consolidation is unauthorized or cannot compose."""


class LicenseError(ValueError):
    """Raised on a license temporal-window or egress violation."""


# --- scope projection / tenant isolation ----------------------------------------------


def is_visible(
    row_scope: str,
    row_tenant: str,
    eval_scope: str,
    eval_tenant: str | None = None,
) -> bool:
    """tenant-scope-isolation: is a row admitted to an evaluation of the given scope?

    public/shared rows are admitted for every evaluation (public-only evals admit only public);
    a tenant_private row is admitted ONLY to an EVAL_TENANT evaluation for the same tenant.
    """
    if row_scope not in SCOPE_RANK:
        raise ScopeError(f"unknown row scope {row_scope!r}")
    if eval_scope not in (EVAL_PUBLIC, EVAL_SHARED, EVAL_TENANT):
        raise ScopeError(f"unknown eval scope {eval_scope!r}")
    if eval_scope == EVAL_TENANT and not eval_tenant:
        raise ScopeError("EVAL_TENANT requires eval_tenant")

    if row_scope in (SCOPE_PUBLIC, SCOPE_SHARED):
        if eval_scope == EVAL_PUBLIC:
            return row_scope == SCOPE_PUBLIC
        return True
    # tenant_private
    return eval_scope == EVAL_TENANT and row_tenant == eval_tenant


def effective_scope_at(
    prior_scope: str,
    new_scope: str,
    *,
    transition_decision_commit: int,
    read_decision_commit: int,
) -> str:
    """scope-transition-no-retroactive-exposure: the scope a read sees.

    A scope transition is decision-time prospective: a read at a decision commit strictly BEFORE
    the transition sees ``prior_scope``; only reads at/after the transition see ``new_scope``.
    This guarantees a later widening (e.g. tenant_private -> shared) cannot retroactively expose
    the row to a slice decided earlier.
    """
    if prior_scope not in SCOPE_RANK or new_scope not in SCOPE_RANK:
        raise ScopeError("unknown scope in transition")
    return (
        prior_scope if read_decision_commit < transition_decision_commit else new_scope
    )


# --- consolidation composition --------------------------------------------------------


def assert_consolidation_authorized(member_consents: Mapping[str, bool]) -> None:
    """cross-tenant-consolidation-authorization: every member must explicitly consent."""
    if not member_consents:
        raise ConsolidationError("no members to consolidate")
    missing = sorted(m for m, ok in member_consents.items() if not ok)
    if missing:
        raise ConsolidationError(f"members did not authorize consolidation: {missing}")


@dataclass(frozen=True)
class DisclosureProfile:
    """A disclosure profile for composition.

    ``rank`` is restrictiveness WITHIN a comparability domain (``lattice``). Two profiles are
    comparable only if they share a lattice; profiles from different lattices are incomparable
    and (per the ratified rule) block publication unless an approved group policy resolves them.
    """

    profile_id: str
    rank: int
    lattice: str = "default"


def compose_disclosure(
    members: Iterable[DisclosureProfile],
    group: DisclosureProfile | None = None,
    *,
    unanimous_authorization: bool = False,
) -> DisclosureProfile:
    """consolidation-composition-profile (disclosure): fail-closed most-restrictive.

    With no group profile the result is the most restrictive member. A group profile may govern
    ONLY if it is unanimously authorized AND at least as restrictive as every member. Members
    drawn from DIFFERENT lattices are incomparable and block publication (fail closed), since a
    total most-restrictive cannot be proven across them.
    """
    members = list(members)
    if not members:
        raise ConsolidationError("no member disclosure profiles to compose")
    lattices = {m.lattice for m in members}
    if len(lattices) > 1:
        raise ConsolidationError(
            f"incomparable disclosure profiles across lattices {sorted(lattices)}: "
            "publication blocked (fail-closed)"
        )
    most_restrictive = max(members, key=lambda m: (m.rank, m.profile_id))
    if group is None:
        return most_restrictive
    if (
        unanimous_authorization
        and group.lattice == most_restrictive.lattice
        and group.rank >= most_restrictive.rank
    ):
        return group
    raise ConsolidationError(
        "group disclosure profile not unanimously authorized, incomparable, or looser than a "
        "member; publication blocked (fail-closed)"
    )


def compose_license_scope(member_scopes: Iterable[frozenset[str]]) -> frozenset[str]:
    """consolidation-composition (license/consent): intersection-only; empty blocks output."""
    scopes = list(member_scopes)
    if not scopes:
        raise ConsolidationError("no member license/consent scopes to compose")
    result = set(scopes[0])
    for s in scopes[1:]:
        result &= set(s)
    if not result:
        raise ConsolidationError(
            "empty license/consent intersection: consolidated output blocked"
        )
    return frozenset(result)


# --- license-aware egress -------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime | None = None  # open-ended


def window_contains(window: Window, at: datetime) -> bool:
    """Half-open ``[start, end)`` containment; open ``end`` means +inf."""
    if at < window.start:
        return False
    return window.end is None or at < window.end


@dataclass(frozen=True)
class LicenseGrant:
    valid_window: Window
    decision_window: Window
    access_scope: str  # who may ACCESS the source (SCOPE_*)
    rights_scope: str  # the USAGE-RIGHTS scope the license actually grants (SCOPE_*)
    redistribution_grant: bool = False
    derivative_use_grant: bool = False
    expiry: datetime | None = None
    revocation: datetime | None = None
    temporal_egress_policy: str = EGRESS_READ_TIME


def assert_license_valid(
    grant: LicenseGrant, *, valid_at: datetime, decision_at: datetime
) -> None:
    """license-rights-temporal-window: egress time in both windows, unexpired, unrevoked."""
    if not window_contains(grant.valid_window, valid_at):
        raise LicenseError("egress valid-time outside the license valid window")
    if not window_contains(grant.decision_window, decision_at):
        raise LicenseError("egress decision-time outside the license decision window")
    if grant.expiry is not None and decision_at >= grant.expiry:
        raise LicenseError("license expired")
    if grant.revocation is not None and decision_at >= grant.revocation:
        raise LicenseError("license revoked")


def assert_egress_allowed(
    grant: LicenseGrant,
    *,
    target_scope: str,
    is_redistribution: bool = False,
    is_derivative: bool = False,
) -> None:
    """license-aware-egress: block an output the source license does not permit.

    The target scope must be no more permissive than EITHER the license access scope or its
    usage rights scope — egress is gated by the MORE restrictive of the two, so a license with a
    broad access scope but a narrow rights scope still blocks an over-broad egress.
    Redistribution / derivative use each require their explicit grant.
    """
    if target_scope not in SCOPE_RANK:
        raise LicenseError(f"unknown target scope {target_scope!r}")
    if grant.access_scope not in SCOPE_RANK:
        raise LicenseError(f"unknown license access scope {grant.access_scope!r}")
    if grant.rights_scope not in SCOPE_RANK:
        raise LicenseError(f"unknown license rights scope {grant.rights_scope!r}")
    # Lower rank = more permissive (public). The egress ceiling is the MORE restrictive
    # (higher rank) of access_scope and rights_scope; target must be no more permissive than it.
    ceiling = max(SCOPE_RANK[grant.access_scope], SCOPE_RANK[grant.rights_scope])
    if SCOPE_RANK[target_scope] < ceiling:
        raise LicenseError(
            f"egress target scope {target_scope!r} is more permissive than the license "
            f"access/rights ceiling (access={grant.access_scope!r}, "
            f"rights={grant.rights_scope!r})"
        )
    if is_redistribution and not grant.redistribution_grant:
        raise LicenseError("redistribution not granted by the source license")
    if is_derivative and not grant.derivative_use_grant:
        raise LicenseError("derivative use not granted by the source license")


def egress_reevaluation(
    policy: str, *, issued_decision_at: datetime, read_decision_at: datetime
) -> str:
    """Mechanize ``temporal_egress_policy`` for an ALREADY-ISSUED output read later.

    * ``trace_decision_time`` — grandfather the already-issued output (``grandfathered``); new
      requests are blocked separately via :func:`assert_egress_allowed` at their decision time.
    * ``egress_read_time`` — re-evaluate access at read/export (``reevaluate``).
    * ``retroactive_restatement`` — emit restatement/tombstone events (``restate``).
    """
    if policy not in EGRESS_POLICIES:
        raise LicenseError(f"unknown temporal_egress_policy {policy!r}")
    if policy == EGRESS_TRACE_DECISION_TIME:
        return (
            "grandfathered" if read_decision_at >= issued_decision_at else "reevaluate"
        )
    if policy == EGRESS_READ_TIME:
        return "reevaluate"
    return "restate"


# --- DB seam (documented) -------------------------------------------------------------


def project_to_outbox(
    session, rows, eval_scope, eval_tenant=None
):  # pragma: no cover - seam
    """Scope-project + disclosure-filter + license-gate rows into the outbox (VARCH-5/6 seam)."""
    raise NotImplementedError(
        "project_to_outbox is the VARCH-5/6 write-path/outbox seam; the VARCH-4f deliverable "
        "is the pure scope/consolidation/license decision core."
    )
