"""Centralized read scope-authorization for public temporal reads (VARCH-8a).

candidate-v14 Public API Decision + the ratified ``sds:profile:public-temporal-read:v1`` contract:
all semantic read endpoints (`/api/v1/semantic-dimensions`, `/api/v1/concepts`) use ONE centralized
scope filter so a read can never expose tenant-private existence to a public/shared surface. This
is the pure decision core the FastAPI dependency wraps; it does NOT re-derive the visibility rule —
it delegates to the ratified VARCH-2/4f cores:

* :mod:`src.semantic.coverage` — the scope vocabulary (EVAL_PUBLIC/SHARED/TENANT, SCOPE_*,
  PUBLIC_TENANT).
* :mod:`src.semantic.write_scope` — ``is_visible`` (tenant-scope isolation) and
  ``effective_scope_at`` (no-retroactive-public-exposure).
* :mod:`src.semantic.write_commitment` — ``assert_no_private_leak`` for the projection-level
  privacy invariants.

Privacy invariants enforced (from the contract): no_tenant_private_existence,
no_unauthorized_membership, no_private_count_metadata, no_blocked_or_restated_payload_details,
no_later_scope_transition_exposing_prior_private_data. (no_unpublished_sygris_variables is a
publication-state filter applied by the resolver/repository, not here.)

Gates contributed here (mechanism; CI wiring in VARCH-10): centralized-read-scope-resolution,
tenant-scope-isolation (delegated), no-retroactive-public-exposure (delegated),
public-projection-privacy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from src.semantic import write_scope
from src.semantic.coverage import EVAL_PUBLIC, EVAL_SHARED, EVAL_TENANT, PUBLIC_TENANT
from src.semantic.write_commitment import CommitmentError, assert_no_private_leak

# The authorization scope CLASSES a read may evaluate under (== coverage EVAL_* vocabulary).
SCOPE_CLASSES = frozenset({EVAL_PUBLIC, EVAL_SHARED, EVAL_TENANT})


class ReadScopeError(ValueError):
    """Raised when a read's scope cannot be authorized."""


def resolve_eval_scope(
    *,
    requested_scope_class: str,
    user_company_id: str | None,
    is_admin: bool = False,
) -> tuple[str, str | None]:
    """Map a request to its ``(eval_scope, eval_tenant)`` — the centralized authorization step.

    ``public`` / ``shared`` reads bind NO tenant (they see only public, or public+shared). A
    ``tenant`` read requires the principal to be assigned to a real tenant (``company_id``, not the
    ``__public__`` sentinel); it is evaluated for THAT tenant only — a user can never request a
    tenant scope they are not assigned to (admins included resolve to their own assigned tenant
    here; cross-tenant admin reads are out of scope for the public temporal-read surface).
    """
    if requested_scope_class not in SCOPE_CLASSES:
        raise ReadScopeError(f"unknown scope class {requested_scope_class!r}")
    if requested_scope_class in (EVAL_PUBLIC, EVAL_SHARED):
        return (requested_scope_class, None)
    # EVAL_TENANT
    tenant = user_company_id
    if not tenant or tenant == PUBLIC_TENANT:
        raise ReadScopeError(
            "a tenant-scoped read requires an assigned tenant (company_id)"
        )
    return (EVAL_TENANT, tenant)


def admit_subject(
    row_scope: str,
    row_tenant: str,
    *,
    eval_scope: str,
    eval_tenant: str | None,
) -> bool:
    """tenant-scope-isolation: is a subject admitted to this evaluation? (delegates 4f)."""
    return write_scope.is_visible(row_scope, row_tenant, eval_scope, eval_tenant)


def effective_scope_for_read(
    prior_scope: str,
    new_scope: str,
    *,
    transition_decision_commit: int,
    read_decision_commit: int,
) -> str:
    """no-retroactive-public-exposure: the scope a read at this decision sees (delegates 4f)."""
    return write_scope.effective_scope_at(
        prior_scope,
        new_scope,
        transition_decision_commit=transition_decision_commit,
        read_decision_commit=read_decision_commit,
    )


def filter_visible_subjects(
    subjects: Iterable[tuple[str, str, Any]],
    *,
    eval_scope: str,
    eval_tenant: str | None,
) -> list[Any]:
    """Return only the payloads of subjects admitted to the evaluation.

    ``subjects`` is an iterable of ``(row_scope, row_tenant, payload)``. A tenant-private subject
    not belonging to the authorized tenant is DROPPED (not error) so its very existence is never
    revealed — the no_tenant_private_existence / no_unauthorized_membership invariants.
    """
    return [
        payload
        for row_scope, row_tenant, payload in subjects
        if admit_subject(
            row_scope, row_tenant, eval_scope=eval_scope, eval_tenant=eval_tenant
        )
    ]


def assert_public_projection_clean(
    projection: Mapping[str, Any],
    *,
    allowed: frozenset[str] | None = None,
) -> None:
    """public-projection-privacy: a public/shared projection leaks no private field.

    Delegates to 4d ``assert_no_private_leak`` (denylist of private/commitment/payload fields,
    plus an optional public allowlist). Covers no_private_count_metadata and
    no_blocked_or_restated_payload_details for the rendered projection. Re-raises as
    :class:`ReadScopeError` so the read path has a single error type.
    """
    try:
        if allowed is not None:
            assert_no_private_leak(projection, allowed=allowed)
        else:
            assert_no_private_leak(projection)
    except CommitmentError as exc:
        raise ReadScopeError(str(exc)) from exc


def assert_scope_not_retroactively_widened(
    *,
    prior_scope: str,
    new_scope: str,
    transition_decision_commit: int,
    read_decision_commit: int,
    eval_scope: str,
    eval_tenant: str | None,
    row_tenant: str,
) -> None:
    """Fail-closed guard: a later scope widening must not expose an earlier-decided read.

    Composes :func:`effective_scope_for_read` (the scope the read SEES at its decision) then
    :func:`admit_subject`; if the subject is not admissible at the EFFECTIVE (decision-time) scope
    it is rejected, so a ``tenant_private -> shared/public`` transition decided AFTER the read
    cannot retroactively expose the prior-private subject.
    """
    effective = effective_scope_for_read(
        prior_scope,
        new_scope,
        transition_decision_commit=transition_decision_commit,
        read_decision_commit=read_decision_commit,
    )
    if not admit_subject(
        effective, row_tenant, eval_scope=eval_scope, eval_tenant=eval_tenant
    ):
        raise ReadScopeError(
            "no-retroactive-public-exposure: subject is not visible at its effective "
            f"decision-time scope {effective!r} for this evaluation"
        )
