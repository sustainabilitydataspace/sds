"""Regression tests for F02 auth hardening (convergence run 2026-06-22).

Covers: RBAC role superset (C9), X-Forwarded-For rightmost-untrusted hop (C4),
API-key last_used debounce (C3), security headers (C5), and get_optional_user
dependency injection (C6).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone


def test_data_manager_role_is_superset_of_analyst():
    from src.auth.models import ROLE_PERMISSIONS, Permission, UserRole

    dm = set(ROLE_PERMISSIONS[UserRole.DATA_MANAGER])
    analyst = set(ROLE_PERMISSIONS[UserRole.ANALYST])
    admin = set(ROLE_PERMISSIONS[UserRole.ADMIN])

    assert analyst <= dm, "DATA_MANAGER must include all ANALYST permissions"
    assert dm <= admin, "ADMIN must include all DATA_MANAGER permissions"
    assert Permission.EXECUTE_SPARQL in dm


def test_get_real_ip_uses_rightmost_untrusted_hop(monkeypatch):
    from src.api import rate_limit
    from src.config.settings import settings

    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.1"])

    class _Client:
        host = "10.0.0.1"

    class _TrustedPeer:
        client = _Client()
        # Proxy appended the real client (203.0.113.9); 1.2.3.4 is attacker-prepended.
        headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}

    assert rate_limit._get_real_ip(_TrustedPeer()) == "203.0.113.9"

    class _UntrustedClient:
        host = "8.8.8.8"

    class _UntrustedPeer:
        client = _UntrustedClient()
        headers = {"X-Forwarded-For": "1.2.3.4"}

    # Direct peer is not a trusted proxy -> ignore XFF entirely.
    assert rate_limit._get_real_ip(_UntrustedPeer()) == "8.8.8.8"


def test_api_key_last_used_debounce():
    from src.services import api_key_store as ak

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert ak._should_update_last_used(None, now) is True
    assert ak._should_update_last_used(now - timedelta(seconds=10), now) is False
    assert ak._should_update_last_used(now - timedelta(seconds=600), now) is True


def test_security_headers_present(client):
    resp = client.get("/healthz")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


def test_get_optional_user_injects_user_store():
    from src.auth import dependencies

    sig = inspect.signature(dependencies.get_optional_user)
    assert "users" in sig.parameters, "get_optional_user must inject the user store"
