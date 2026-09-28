"""Observability endpoints (/healthz, /metrics) and request-id propagation."""

from __future__ import annotations


def test_healthz_returns_dependency_status(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] in {"healthy", "degraded", "unhealthy"}
    assert "dependencies" in payload

    deps = payload["dependencies"]
    assert set(deps) == {"database"}
    assert deps["database"]["status"] in {"disabled", "healthy", "unhealthy"}


def test_untrusted_request_id_is_replaced_when_provided(client):
    from uuid import UUID

    request_id = "test-request-id-123"
    response = client.get("/healthz", headers={"X-Request-ID": request_id})
    assert (
        str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]
    )


def test_metrics_requires_system_health_permission(client, viewer_token, admin_token):
    assert client.get("/metrics").status_code == 403
    assert (
        client.get(
            "/metrics", headers={"Authorization": f"Bearer {viewer_token}"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/metrics", headers={"Authorization": f"Bearer {admin_token}"}
        ).status_code
        == 200
    )


def test_metrics_returns_prometheus_text_format(client, admin_token):
    # Warm up at least one request so labeled metrics have a sample.
    client.get("/healthz")

    response = client.get(
        "/metrics", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/plain")

    body = response.text
    assert "sds_api_requests_total" in body
    assert "sds_api_request_duration_seconds" in body
    assert "sds_api_readiness" in body


def test_metrics_readiness_gauge_tracks_functional_readiness(
    client, monkeypatch, admin_token
):
    from src.api import main

    monkeypatch.setattr(
        main, "_readiness_status", lambda: (503, {"status": "unhealthy"})
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    unhealthy = client.get("/metrics", headers=headers)
    assert "sds_api_readiness 0.0" in unhealthy.text

    monkeypatch.setattr(main, "_readiness_status", lambda: (200, {"status": "ready"}))
    healthy = client.get("/metrics", headers=headers)
    assert "sds_api_readiness 1.0" in healthy.text
