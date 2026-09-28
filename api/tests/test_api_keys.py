"""API key authentication tests (offline-safe)."""

from __future__ import annotations


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_key_can_call_protected_endpoints_and_can_be_revoked(client, admin_token):
    created = client.post(
        "/auth/api-keys",
        headers=_auth(admin_token),
        json={
            "name": "ci-key",
            "description": "test",
            "permissions": ["convert_units"],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["key"].startswith("sds_")

    # API key should work as an alternative auth mechanism (no JWT required).
    units = client.get("/api/v1/units", headers={"X-API-Key": payload["key"]})
    assert units.status_code == 200

    revoked = client.delete(
        f"/auth/api-keys/{payload['id']}", headers=_auth(admin_token)
    )
    assert revoked.status_code == 200

    units_after = client.get("/api/v1/units", headers={"X-API-Key": payload["key"]})
    assert units_after.status_code == 401


def test_api_key_creation_rejects_permissions_outside_current_user(
    client, viewer_token
):
    response = client.post(
        "/auth/api-keys",
        headers=_auth(viewer_token),
        json={"name": "overclaim", "permissions": ["manage_users"]},
    )

    assert response.status_code == 403


def test_api_key_creation_allows_permission_subset(client, viewer_token):
    response = client.post(
        "/auth/api-keys",
        headers=_auth(viewer_token),
        json={"name": "read-key", "permissions": ["read_values"]},
    )

    assert response.status_code == 200
    assert response.json()["permissions"] == ["read_values"]
