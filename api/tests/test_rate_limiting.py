"""Tests for rate limiting (F-005)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from slowapi.middleware import SlowAPIMiddleware
from slowapi.wrappers import LimitGroup
from starlette.requests import Request

from src.api import main as main_module
from src.api.main import app
from src.api.rate_limit import _get_real_ip, limiter
from src.config.settings import settings


def test_healthz_liveness_is_not_rate_limited(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_dependency_health",
        lambda: (200, {"status": "healthy", "dependencies": {}}),
    )
    monkeypatch.setattr(
        limiter,
        "_default_limits",
        [
            LimitGroup(
                "2/minute", limiter._key_func, None, False, None, None, None, 1, False
            )
        ],
    )
    limiter._storage.reset()
    with TestClient(app) as client:
        assert [client.get("/healthz").status_code for _ in range(4)] == [200] * 4


class TestRateLimitConfig:
    def test_limiter_exists(self):
        assert limiter is not None

    def test_default_limits(self):
        assert limiter._default_limits is not None

    def test_application_installs_global_default_limit_middleware(self):
        assert any(
            middleware.cls is SlowAPIMiddleware for middleware in app.user_middleware
        )


def _request(*, headers: dict[str, str], host: str):
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def test_get_real_ip_ignores_forwarded_for_from_untrusted_client(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", [])
    request = _request(
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        host="198.51.100.20",
    )

    assert _get_real_ip(request) == "198.51.100.20"


def test_get_real_ip_uses_forwarded_for_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = _request(
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.2"},
        host="10.0.0.5",
    )

    assert _get_real_ip(request) == "203.0.113.10"


def test_get_real_ip_ignores_attacker_prepended_hop_across_trusted_proxy_chain(
    monkeypatch,
):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "10.0.0.0/24, invalid-cidr")
    request = _request(
        headers={"X-Forwarded-For": "198.51.100.99, 203.0.113.10, 10.0.0.2"},
        host="10.0.0.5",
    )

    assert _get_real_ip(request) == "203.0.113.10"


@pytest.mark.parametrize(
    "fields",
    [
        [(b"x-forwarded-for", b"bad, 203.0.113.10")],
        [
            (b"x-forwarded-for", b"bad"),
            (b"x-forwarded-for", b"203.0.113.10"),
        ],
    ],
)
def test_get_real_ip_rejects_any_malformed_forwarded_hop(monkeypatch, fields):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = Request({"type": "http", "headers": fields, "client": ("10.0.0.5", 1234)})
    assert _get_real_ip(request) == "10.0.0.5"


def test_get_real_ip_never_trusts_forwarded_header_from_unknown_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = SimpleNamespace(headers={"X-Forwarded-For": "203.0.113.10"}, client=None)

    assert _get_real_ip(request) == "unknown"


def test_get_real_ip_considers_all_forwarded_header_fields_from_trusted_peer(
    monkeypatch,
):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.99"),
                (b"x-forwarded-for", b"203.0.113.10"),
            ],
            "client": ("10.0.0.5", 1234),
        }
    )

    assert _get_real_ip(request) == "203.0.113.10"


def test_get_real_ip_uses_trusted_peer_if_forwarded_hop_is_not_ip(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = _request(headers={"X-Forwarded-For": "forged-non-ip"}, host="10.0.0.5")

    assert _get_real_ip(request) == "10.0.0.5"


def test_get_real_ip_recognizes_ipv4_mapped_ipv6_trusted_proxy_hops(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/24"])
    request = _request(
        headers={"X-Forwarded-For": "203.0.113.10, ::ffff:10.0.0.2"},
        host="::ffff:10.0.0.5",
    )

    assert _get_real_ip(request) == "203.0.113.10"


def test_shipped_uvicorn_launches_preserve_socket_peer_for_rate_limit():
    api_root = Path(__file__).resolve().parents[1]
    dockerfile = (api_root / "Dockerfile").read_text(encoding="utf-8")
    main = (api_root / "src/api/main.py").read_text(encoding="utf-8")
    dev = (api_root / "scripts/dev.ps1").read_text(encoding="utf-8")

    assert '"--no-proxy-headers"' in dockerfile
    assert "proxy_headers=False" in main
    for line in dev.splitlines():
        if "Invoke-VenvPython -m uvicorn src.api.main:app" in line:
            assert "--no-proxy-headers" in line
