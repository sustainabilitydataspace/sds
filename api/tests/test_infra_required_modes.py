"""Strict runtime tests for removed legacy semantic surfaces."""

from __future__ import annotations


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_legacy_sync_surface_stays_removed(client, admin_token):
    schema = client.get("/openapi.json", headers=_auth(admin_token))
    assert schema.status_code == 200
    assert not any(path.startswith("/api/v1/sync") for path in schema.json()["paths"])

    r = client.get("/api/v1/sync/status", headers=_auth(admin_token))
    assert r.status_code == 404
