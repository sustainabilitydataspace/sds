"""VARCH-8a contract tests — centralized read scope-authorization.

Pure core: map a request to (eval_scope, eval_tenant); admit subjects via the ratified 4f
visibility rule; drop (never reveal) unauthorized tenant-private subjects; no-retroactive-public-
exposure guard; public-projection privacy. No HTTP/DB layer.
"""

from __future__ import annotations

import pytest

from src.semantic.coverage import (
    EVAL_PUBLIC,
    EVAL_SHARED,
    EVAL_TENANT,
    SCOPE_PUBLIC,
    SCOPE_SHARED,
    SCOPE_TENANT_PRIVATE,
)
from src.semantic.read_scope import (
    ReadScopeError,
    admit_subject,
    assert_public_projection_clean,
    assert_scope_not_retroactively_widened,
    filter_visible_subjects,
    resolve_eval_scope,
)

# --- resolve_eval_scope ---------------------------------------------------------------


def test_public_and_shared_bind_no_tenant() -> None:
    assert resolve_eval_scope(
        requested_scope_class=EVAL_PUBLIC, user_company_id="c1"
    ) == (
        EVAL_PUBLIC,
        None,
    )
    assert resolve_eval_scope(
        requested_scope_class=EVAL_SHARED, user_company_id=None
    ) == (
        EVAL_SHARED,
        None,
    )


def test_tenant_binds_company() -> None:
    assert resolve_eval_scope(
        requested_scope_class=EVAL_TENANT, user_company_id="acme"
    ) == (EVAL_TENANT, "acme")


def test_tenant_without_company_rejected() -> None:
    with pytest.raises(ReadScopeError, match="requires an assigned tenant"):
        resolve_eval_scope(requested_scope_class=EVAL_TENANT, user_company_id=None)
    with pytest.raises(ReadScopeError, match="requires an assigned tenant"):
        resolve_eval_scope(
            requested_scope_class=EVAL_TENANT, user_company_id="__public__"
        )


def test_unknown_scope_class_rejected() -> None:
    with pytest.raises(ReadScopeError, match="unknown scope class"):
        resolve_eval_scope(requested_scope_class="root", user_company_id="c1")


# --- admit_subject / filter -----------------------------------------------------------


def test_public_subject_visible_everywhere() -> None:
    assert admit_subject(
        SCOPE_PUBLIC, "__public__", eval_scope=EVAL_PUBLIC, eval_tenant=None
    )
    assert admit_subject(
        SCOPE_SHARED, "__public__", eval_scope=EVAL_SHARED, eval_tenant=None
    )


def test_tenant_private_invisible_to_public() -> None:
    assert not admit_subject(
        SCOPE_TENANT_PRIVATE, "acme", eval_scope=EVAL_PUBLIC, eval_tenant=None
    )


def test_tenant_private_visible_only_to_owner() -> None:
    assert admit_subject(
        SCOPE_TENANT_PRIVATE, "acme", eval_scope=EVAL_TENANT, eval_tenant="acme"
    )
    assert not admit_subject(
        SCOPE_TENANT_PRIVATE, "acme", eval_scope=EVAL_TENANT, eval_tenant="other"
    )


def test_filter_drops_unauthorized_tenant_private() -> None:
    subjects = [
        (SCOPE_PUBLIC, "__public__", "pub"),
        (SCOPE_TENANT_PRIVATE, "acme", "acme-secret"),
        (SCOPE_TENANT_PRIVATE, "other", "other-secret"),
    ]
    # public eval: only the public subject survives (private existence never revealed).
    assert filter_visible_subjects(
        subjects, eval_scope=EVAL_PUBLIC, eval_tenant=None
    ) == ["pub"]
    # acme tenant eval: public + acme's own private.
    assert filter_visible_subjects(
        subjects, eval_scope=EVAL_TENANT, eval_tenant="acme"
    ) == ["pub", "acme-secret"]


# --- privacy projection ---------------------------------------------------------------


def test_public_projection_clean_passes() -> None:
    assert_public_projection_clean({"uri": "x", "label": "y", "count": 5})


def test_public_projection_rejects_private_field() -> None:
    with pytest.raises(ReadScopeError, match="leaks private field"):
        assert_public_projection_clean({"uri": "x", "payload_ciphertext": "zzz"})


def test_public_projection_allowlist_blocks_unlisted() -> None:
    with pytest.raises(ReadScopeError, match="not in the public allowlist"):
        assert_public_projection_clean(
            {"uri": "x", "secret_attr": "z"}, allowed=frozenset({"uri"})
        )


# --- no-retroactive-public-exposure ---------------------------------------------------


def test_retroactive_widen_blocks_earlier_read() -> None:
    # row goes tenant_private -> shared at commit 5; a public read at commit 3 must NOT see it.
    with pytest.raises(ReadScopeError, match="no-retroactive-public-exposure"):
        assert_scope_not_retroactively_widened(
            prior_scope=SCOPE_TENANT_PRIVATE,
            new_scope=SCOPE_SHARED,
            transition_decision_commit=5,
            read_decision_commit=3,
            eval_scope=EVAL_SHARED,
            eval_tenant=None,
            row_tenant="acme",
        )


def test_read_after_transition_is_visible() -> None:
    assert_scope_not_retroactively_widened(
        prior_scope=SCOPE_TENANT_PRIVATE,
        new_scope=SCOPE_SHARED,
        transition_decision_commit=5,
        read_decision_commit=9,  # after the widen
        eval_scope=EVAL_SHARED,
        eval_tenant=None,
        row_tenant="acme",
    )
