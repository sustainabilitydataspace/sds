from __future__ import annotations


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_refresh_token_is_rotated_and_single_use(client):
    tokens = _login(client)

    first = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    replay = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert first.status_code == 200, first.text
    assert first.json()["refresh_token"]
    assert first.json()["refresh_token"] != tokens["refresh_token"]
    assert replay.status_code == 401


def test_password_change_invalidates_existing_access_and_refresh_tokens(client):
    tokens = _login(client)

    changed = client.post(
        "/auth/change-password",
        headers=_auth(tokens["access_token"]),
        json={"current_password": "admin123", "new_password": "new-admin-pass-2026"},
    )
    old_access = client.get("/auth/me", headers=_auth(tokens["access_token"]))
    old_refresh = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert changed.status_code == 200, changed.text
    assert old_access.status_code == 401
    assert old_refresh.status_code == 401


def test_logout_without_refresh_body_invalidates_existing_refresh_token(client):
    tokens = _login(client)

    logged_out = client.post("/auth/logout", headers=_auth(tokens["access_token"]))
    old_refresh = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    assert logged_out.status_code == 200, logged_out.text
    assert old_refresh.status_code == 401
