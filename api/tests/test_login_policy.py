"""Shared application password policy (operator provisioning and admin resets)."""

from __future__ import annotations

import pytest

from src.auth.login_policy import validate_password


@pytest.mark.parametrize(
    "password",
    ["Abcdefghij1k", "abcdefghij1-", "ABCDEFGHIJ1-", "Strong-Analyst-2026"],
)
def test_compliant_passwords_pass(password):
    validate_password(password)


@pytest.mark.parametrize(
    "password,rule",
    [
        ("Abcdefghi1k", "at least 12"),
        ("Aa1-" * 26, "at most 100"),
        ("Ä" * 37 + "a1", "72 bytes"),
        ("My-Password-2026", "placeholder"),
        ("change_me-NOW-2026", "placeholder"),
        ("abcdefghijkl", "three"),
        ("abcdefghij12", "three"),
    ],
)
def test_each_rule_is_enforced_without_echoing(password, rule):
    with pytest.raises(ValueError) as exc:
        validate_password(password)
    assert rule in str(exc.value)
    assert password not in str(exc.value)
