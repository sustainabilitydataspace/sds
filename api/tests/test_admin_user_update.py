"""Admin management of another user's profile, tenant, role, state and password.

Covers ``PUT /auth/users/{username}`` and
``POST /auth/users/{username}/reset-password``: authorization (bearer admin with
manage_users only), self-target rejection, input validation without echoing
secrets, email uniqueness, session revocation of the target, persistence-failure
propagation, rate limiting and audit logging.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.api.main import app
from src.api.rate_limit import limiter
from src.auth.jwt_handler import jwt_handler
from src.auth.models import (
    ROLE_PERMISSIONS,
    APIKeyCreate,
    Permission,
    UserCreate,
    UserRole,
    UserUpdate,
)
from src.services.api_key_store import InMemoryAPIKeyStore, get_api_key_store
from src.services.user_store import DatabaseUserStore, InMemoryUserStore, get_user_store
from structlog.testing import capture_logs

ADMIN_PASSWORD = "admin-password-for-tests"
ANALYST_PASSWORD = "analyst-password-for-tests"
SECRET_VALUES = (ADMIN_PASSWORD, ANALYST_PASSWORD)


@pytest.fixture
def stores():
    limiter._storage.reset()
    users = InMemoryUserStore()
    users.create_user(
        UserCreate(
            username="admin",
            email="admin@example.com",
            password=ADMIN_PASSWORD,
            role=UserRole.ADMIN,
            company_id="sds-admin",
        )
    )
    users.create_user(
        UserCreate(
            username="analyst",
            email="analyst@example.com",
            password=ANALYST_PASSWORD,
            role=UserRole.ANALYST,
        )
    )
    users.create_user(
        UserCreate(
            username="viewer",
            email="viewer@example.com",
            password="viewer-password-for-tests",
            role=UserRole.VIEWER,
            company_id="sds-demo",
        )
    )
    keys = InMemoryAPIKeyStore()
    app.dependency_overrides[get_user_store] = lambda: users
    app.dependency_overrides[get_api_key_store] = lambda: keys
    yield users, keys
    app.dependency_overrides.pop(get_user_store, None)
    app.dependency_overrides.pop(get_api_key_store, None)
    limiter._storage.reset()


@pytest.fixture
def client(stores):
    return TestClient(app)


def _access(users, username):
    user = users.get_user(username=username)
    return jwt_handler.create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        company_id=user.company_id,
        auth_version=user.auth_version,
    )


def _refresh(users, username):
    user = users.get_user(username=username)
    return jwt_handler.create_refresh_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        company_id=user.company_id,
        auth_version=user.auth_version,
    )


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _put(client, username, body, headers):
    return client.put(f"/auth/users/{username}", json=body, headers=headers)


class TestAdminAssignsTenantRoleActive:
    def test_admin_assigns_company_id_to_analyst(self, client, stores):
        users, _ = stores
        before = users.get_user(username="analyst").auth_version

        response = _put(
            client,
            "analyst",
            {"company_id": " sds-demo "},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "analyst"
        assert body["company_id"] == "sds-demo"
        assert body["role"] == "analyst"
        assert set(body["permissions"]) == {
            p.value for p in ROLE_PERMISSIONS[UserRole.ANALYST]
        }
        stored = users.get_user(username="analyst")
        assert stored.company_id == "sds-demo"
        assert stored.auth_version == before + 1

    @pytest.mark.parametrize(
        "body,attribute,expected",
        [
            ({"role": "data_manager"}, "role", UserRole.DATA_MANAGER),
            ({"is_active": False}, "is_active", False),
        ],
    )
    def test_admin_changes_role_or_active_once(
        self, client, stores, body, attribute, expected
    ):
        users, _ = stores
        before = users.get_user(username="analyst").auth_version

        response = _put(client, "analyst", body, _bearer(_access(users, "admin")))

        assert response.status_code == 200
        stored = users.get_user(username="analyst")
        assert getattr(stored, attribute) == expected
        assert stored.auth_version == before + 1

    def test_company_id_length_limit_applies_after_strip(self, client, stores):
        users, _ = stores
        tenant = "t" * 50

        response = _put(
            client,
            "analyst",
            {"company_id": f"  {tenant}  "},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        assert users.get_user(username="analyst").company_id == tenant

    def test_only_requested_fields_change(self, client, stores):
        users, _ = stores
        before = users.get_user(username="viewer")

        response = _put(
            client, "viewer", {"role": "analyst"}, _bearer(_access(users, "admin"))
        )

        assert response.status_code == 200
        after = users.get_user(username="viewer")
        assert after.company_id == before.company_id
        assert after.email == before.email
        assert after.is_active is True


class TestTargetSessionRevocation:
    @pytest.mark.parametrize(
        "body",
        [{"company_id": "sds-demo"}, {"role": "viewer"}, {"is_active": False}],
    )
    def test_pre_update_access_and_refresh_tokens_rejected(self, client, stores, body):
        users, _ = stores
        old_access = _access(users, "analyst")
        old_refresh = _refresh(users, "analyst")
        assert client.get("/auth/me", headers=_bearer(old_access)).status_code == 200

        response = _put(client, "analyst", body, _bearer(_access(users, "admin")))
        assert response.status_code == 200

        assert client.get("/auth/me", headers=_bearer(old_access)).status_code == 401
        refreshed = client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert refreshed.status_code == 401


class TestAuthorization:
    @pytest.mark.parametrize("username", ["analyst", "viewer"])
    def test_non_admin_bearer_forbidden(self, client, stores, username):
        users, _ = stores
        target = "viewer" if username == "analyst" else "analyst"
        before = users.get_user(username=target)

        response = _put(
            client,
            target,
            {"company_id": "x-tenant"},
            _bearer(_access(users, username)),
        )

        assert response.status_code == 403
        assert users.get_user(username=target) == before

    def test_admin_api_key_with_manage_users_forbidden(self, client, stores):
        users, keys = stores
        admin = users.get_user(username="admin")
        key = keys.create_api_key(
            user_id=admin.id,
            request=APIKeyCreate(
                name="admin-key", permissions=[Permission.MANAGE_USERS]
            ),
        ).key
        before = users.get_user(username="analyst")

        response = _put(
            client, "analyst", {"company_id": "sds-demo"}, {"X-API-Key": key}
        )

        assert response.status_code == 403
        assert users.get_user(username="analyst") == before

    def test_missing_credentials_rejected(self, client, stores):
        response = _put(client, "analyst", {"company_id": "sds-demo"}, {})
        assert response.status_code == 403

    def test_invalid_bearer_rejected(self, client, stores):
        response = _put(
            client, "analyst", {"company_id": "sds-demo"}, _bearer("not-a-jwt")
        )
        assert response.status_code == 401


class TestInvariants:
    @pytest.mark.parametrize(
        "body", [{"company_id": "sds-demo"}, {"role": "viewer"}, {"is_active": False}]
    )
    def test_self_target_rejected_without_change(self, client, stores, body):
        users, _ = stores
        before = users.get_user(username="admin")

        response = _put(client, "admin", body, _bearer(_access(users, "admin")))

        assert response.status_code == 400
        assert users.get_user(username="admin") == before

    def test_unknown_user_returns_404(self, client, stores):
        users, _ = stores
        response = _put(
            client,
            "nobody",
            {"company_id": "sds-demo"},
            _bearer(_access(users, "admin")),
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"password": "new-password-123"},
            {"username": "renamed"},
            {"email": "not-an-email"},
            {"email": None},
            {"full_name": None},
            {"full_name": "   "},
            {"full_name": "x" * 101},
            {"company_id": None},
            {"role": None},
            {"is_active": None},
            {"company_id": ""},
            {"company_id": "   "},
            {"company_id": "string"},
            {"company_id": "TENANT"},
            {"company_id": "x" * 51},
            {"role": "superadmin"},
            {"is_active": "true"},
            {"is_active": 1},
        ],
    )
    def test_invalid_input_rejected_without_change(self, client, stores, body):
        users, _ = stores
        before = users.get_user(username="analyst")

        response = _put(client, "analyst", body, _bearer(_access(users, "admin")))

        assert response.status_code == 422
        assert users.get_user(username="analyst") == before

    def test_me_endpoint_cannot_change_another_account(self, client, stores):
        users, _ = stores
        before = users.get_user(username="analyst")

        response = client.put(
            "/auth/me",
            json={"company_id": "sds-other"},
            headers=_bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        assert response.json()["username"] == "admin"
        assert users.get_user(username="analyst") == before


class TestPersistenceFailure:
    def _store(self, users, *, update_result, still_exists=True):
        store = MagicMock(wraps=users)
        store.update_user.side_effect = None
        store.update_user.return_value = update_result
        target = users.get_user(username="analyst")
        store.get_user.side_effect = [target, target if still_exists else None]
        return store

    def test_unpersisted_update_is_500_not_stale_200(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        app.dependency_overrides[get_user_store] = lambda: self._store(
            users, update_result=None
        )

        response = _put(client, "analyst", {"company_id": "sds-demo"}, _bearer(token))

        assert response.status_code == 500
        assert response.json()["error"]["message"] == "User update failed"

    def test_target_vanished_during_update_is_404(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        app.dependency_overrides[get_user_store] = lambda: self._store(
            users, update_result=None, still_exists=False
        )

        response = _put(client, "analyst", {"company_id": "sds-demo"}, _bearer(token))

        assert response.status_code == 404

    def test_unexpected_error_is_generic_500(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        store = MagicMock(wraps=users)
        store.update_user.side_effect = RuntimeError("db exploded at host=secret")
        app.dependency_overrides[get_user_store] = lambda: store

        response = _put(client, "analyst", {"company_id": "sds-demo"}, _bearer(token))

        assert response.status_code == 500
        assert "secret" not in response.text


class TestDatabaseUserStore:
    def _store(self, update_result):
        record = MagicMock(id="user_analyst", auth_version=0)
        repo = MagicMock()
        repo.get_user_by_username.return_value = record
        repo.get_user_by_email.return_value = None
        repo.update_user.return_value = update_result
        store = DatabaseUserStore.__new__(DatabaseUserStore)
        store._repo = repo
        store._to_user = lambda r: r
        return store, repo

    @pytest.mark.parametrize(
        "update,expected",
        [
            (UserUpdate(company_id="sds-demo"), {"company_id": "sds-demo"}),
            (UserUpdate(role=UserRole.VIEWER), {"role": "viewer"}),
            (UserUpdate(is_active=False), {"is_active": False}),
        ],
    )
    def test_security_fields_forwarded_with_auth_version_increment(
        self, update, expected
    ):
        updated = MagicMock()
        store, repo = self._store(updated)

        result = store.update_user(
            username="analyst", update=update, allow_admin_fields=True
        )

        assert result is updated
        repo.update_user.assert_called_once_with(
            "user_analyst", increment_auth_version=True, **expected
        )

    def test_repository_failure_is_not_masked_by_stale_record(self):
        store, _ = self._store(None)

        result = store.update_user(
            username="analyst",
            update=UserUpdate(company_id="sds-demo"),
            allow_admin_fields=True,
        )

        assert result is None


class TestAuditLogging:
    FORBIDDEN_KEYS = {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "headers",
        "body",
        "error",
    }

    def _assert_no_secrets(self, events, *secrets):
        for event in events:
            assert not (self.FORBIDDEN_KEYS & set(event)), event
            rendered = repr(event)
            for secret in (*SECRET_VALUES, *secrets):
                assert secret not in rendered, event

    def test_success_events(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")

        with capture_logs() as events:
            response = _put(
                client, "analyst", {"company_id": "sds-demo"}, _bearer(token)
            )

        assert response.status_code == 200
        attempt = next(e for e in events if e["event"] == "admin_user_update_attempt")
        assert attempt["actor_user_id"] == "user_admin"
        assert attempt["target_username"] == "analyst"
        assert attempt["requested_fields"] == ["company_id"]
        updated = next(e for e in events if e["event"] == "admin_user_updated")
        assert updated["actor_user_id"] == "user_admin"
        assert updated["target_user_id"] == "user_analyst"
        assert updated["changed_fields"] == ["company_id"]
        assert updated["company_id"] == "sds-demo"
        assert updated["auth_version"] == 1
        self._assert_no_secrets(events, token)

    @pytest.mark.parametrize(
        "target,reason,status",
        [("admin", "self_target", 400), ("nobody", "not_found", 404)],
    )
    def test_denied_events(self, client, stores, target, reason, status):
        users, _ = stores
        token = _access(users, "admin")

        with capture_logs() as events:
            response = _put(client, target, {"company_id": "sds-demo"}, _bearer(token))

        assert response.status_code == status
        denied = next(e for e in events if e["event"] == "admin_user_update_denied")
        assert denied["reason"] == reason
        assert denied["actor_user_id"] == "user_admin"
        assert denied["target_username"] == target
        self._assert_no_secrets(events, token)

    def test_failure_event_has_no_exception_text(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        store = MagicMock(wraps=users)
        store.update_user.side_effect = RuntimeError("db exploded at host=secret")
        app.dependency_overrides[get_user_store] = lambda: store

        with capture_logs() as events:
            _put(client, "analyst", {"company_id": "sds-demo"}, _bearer(token))

        failed = next(e for e in events if e["event"] == "admin_user_update_failed")
        assert failed["error_type"] == "RuntimeError"
        self._assert_no_secrets(events, token, "db exploded", "host=secret")


NEW_PASSWORD = "Fresh-Analyst-2026!"


def _reset(client, username, body, headers):
    return client.post(
        f"/auth/users/{username}/reset-password", json=body, headers=headers
    )


class TestProfileFields:
    def test_admin_sets_email_and_full_name(self, client, stores):
        users, _ = stores
        old_access = _access(users, "analyst")
        old_refresh = _refresh(users, "analyst")
        before = users.get_user(username="analyst")

        response = _put(
            client,
            "analyst",
            {"email": "analyst.new@example.com", "full_name": "  Ana Lyst  "},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        after = users.get_user(username="analyst")
        assert after.username == "analyst"
        assert after.email == "analyst.new@example.com"
        assert after.full_name == "Ana Lyst"
        assert after.company_id == before.company_id
        assert after.role == before.role
        assert after.auth_version == before.auth_version + 1
        assert client.get("/auth/me", headers=_bearer(old_access)).status_code == 401
        refreshed = client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert refreshed.status_code == 401

    def test_duplicate_email_is_409_without_change(self, client, stores):
        users, _ = stores
        before = users.get_user(username="analyst")

        response = _put(
            client,
            "analyst",
            {"email": "viewer@example.com", "full_name": "Changed"},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 409
        assert users.get_user(username="analyst") == before

    def test_case_variant_email_is_accepted_exact_uniqueness(self, client, stores):
        users, _ = stores

        response = _put(
            client,
            "analyst",
            {"email": "Viewer@Example.com"},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200

    def test_me_duplicate_email_is_409(self, client, stores):
        users, _ = stores

        response = client.put(
            "/auth/me",
            json={"email": "viewer@example.com"},
            headers=_bearer(_access(users, "analyst")),
        )

        assert response.status_code == 409
        assert users.get_user(username="analyst").email == "analyst@example.com"

    def test_password_in_profile_body_is_rejected_without_echo(self, client, stores):
        users, _ = stores
        before = users.get_user(username="analyst")

        response = _put(
            client,
            "analyst",
            {"password": NEW_PASSWORD},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 422
        assert NEW_PASSWORD not in response.text
        assert users.get_user(username="analyst") == before


class TestDatabaseProfileUpdate:
    def _store(self, *, owner=None, update_result=None, update_error=None):
        record = MagicMock(id="user_analyst", auth_version=3)
        repo = MagicMock()
        repo.get_user_by_username.return_value = record
        repo.get_user_by_email.return_value = owner
        if update_error is not None:
            repo.update_user.side_effect = update_error
        else:
            repo.update_user.return_value = update_result or MagicMock()
        store = DatabaseUserStore.__new__(DatabaseUserStore)
        store._repo = repo
        store._to_user = lambda r: r
        return store, repo

    def test_admin_email_and_full_name_forwarded_with_single_revocation(self):
        store, repo = self._store()

        store.update_user(
            username="analyst",
            update=UserUpdate(email="analyst.new@example.com", full_name="Ana"),
            allow_admin_fields=True,
            revoke_sessions=True,
        )

        repo.get_user_by_email.assert_called_once_with("analyst.new@example.com")
        repo.update_user.assert_called_once_with(
            "user_analyst",
            increment_auth_version=True,
            email="analyst.new@example.com",
            full_name="Ana",
        )

    def test_self_service_full_name_does_not_revoke(self):
        store, repo = self._store()

        store.update_user(
            username="analyst",
            update=UserUpdate(full_name="Ana"),
            allow_admin_fields=False,
        )

        repo.update_user.assert_called_once_with(
            "user_analyst", increment_auth_version=False, full_name="Ana"
        )

    def test_precheck_conflict_raises_without_update(self):
        store, repo = self._store(owner=MagicMock(id="user_viewer"))

        with pytest.raises(ValueError):
            store.update_user(
                username="analyst",
                update=UserUpdate(email="viewer@example.com"),
                allow_admin_fields=True,
                revoke_sessions=True,
            )
        repo.update_user.assert_not_called()

    def test_unique_constraint_race_rolls_back_and_raises(self):
        race = IntegrityError("UPDATE user_accounts", {}, Exception("duplicate"))
        store, repo = self._store(update_error=race)

        with pytest.raises(ValueError):
            store.update_user(
                username="analyst",
                update=UserUpdate(email="viewer@example.com"),
                allow_admin_fields=True,
                revoke_sessions=True,
            )
        repo.db.rollback.assert_called_once()


class TestPasswordReset:
    def test_admin_resets_password_and_revokes_sessions(self, client, stores):
        users, _ = stores
        old_access = _access(users, "analyst")
        old_refresh = _refresh(users, "analyst")
        before = users.get_user(username="analyst").auth_version

        response = _reset(
            client,
            "analyst",
            {"new_password": NEW_PASSWORD},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        assert response.json() == {"message": "Password reset"}
        assert NEW_PASSWORD not in response.text
        assert users.get_user(username="analyst").auth_version == before + 1
        assert users.authenticate(username="analyst", password=NEW_PASSWORD)
        assert users.authenticate(username="analyst", password=ANALYST_PASSWORD) is None
        assert client.get("/auth/me", headers=_bearer(old_access)).status_code == 401
        refreshed = client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert refreshed.status_code == 401

    def test_policy_boundary_is_accepted(self, client, stores):
        users, _ = stores
        response = _reset(
            client,
            "analyst",
            {"new_password": "Abcdefghij1k"},
            _bearer(_access(users, "admin")),
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "secret",
        [
            "Abcdefghi1k",  # 11 characters
            "abcdefghijkl",  # one character class
            "abcdefghij12",  # two character classes
            "My-Password-2026",  # placeholder marker
            "Ä" * 37 + "a1",  # > 72 UTF-8 bytes
            "Aa1-" * 26,  # > 100 characters
        ],
    )
    def test_policy_violations_rejected_without_echo(self, client, stores, secret):
        users, _ = stores
        before = users.get_user(username="analyst")

        response = _reset(
            client,
            "analyst",
            {"new_password": secret},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 422
        assert secret not in response.text
        assert users.get_user(username="analyst") == before

    @pytest.mark.parametrize(
        "body",
        [{}, {"new_password": None}, {"new_password": NEW_PASSWORD, "role": "admin"}],
    )
    def test_malformed_body_rejected_without_echo(self, client, stores, body):
        users, _ = stores
        response = _reset(client, "analyst", body, _bearer(_access(users, "admin")))
        assert response.status_code == 422
        assert NEW_PASSWORD not in response.text

    def test_self_reset_rejected(self, client, stores):
        users, _ = stores
        response = _reset(
            client,
            "admin",
            {"new_password": NEW_PASSWORD},
            _bearer(_access(users, "admin")),
        )
        assert response.status_code == 400
        assert users.authenticate(username="admin", password=ADMIN_PASSWORD)

    @pytest.mark.parametrize("username", ["analyst", "viewer"])
    def test_non_admin_forbidden(self, client, stores, username):
        users, _ = stores
        target = "viewer" if username == "analyst" else "analyst"
        response = _reset(
            client,
            target,
            {"new_password": NEW_PASSWORD},
            _bearer(_access(users, username)),
        )
        assert response.status_code == 403

    def test_admin_api_key_forbidden(self, client, stores):
        users, keys = stores
        admin = users.get_user(username="admin")
        key = keys.create_api_key(
            user_id=admin.id,
            request=APIKeyCreate(
                name="admin-key", permissions=[Permission.MANAGE_USERS]
            ),
        ).key
        response = _reset(
            client, "analyst", {"new_password": NEW_PASSWORD}, {"X-API-Key": key}
        )
        assert response.status_code == 403

    def test_unknown_user_404(self, client, stores):
        users, _ = stores
        response = _reset(
            client,
            "nobody",
            {"new_password": NEW_PASSWORD},
            _bearer(_access(users, "admin")),
        )
        assert response.status_code == 404

    def test_inactive_target_reset_stays_inactive(self, client, stores):
        users, _ = stores
        users.update_user(
            username="viewer",
            update=UserUpdate(is_active=False),
            allow_admin_fields=True,
        )

        response = _reset(
            client,
            "viewer",
            {"new_password": NEW_PASSWORD},
            _bearer(_access(users, "admin")),
        )

        assert response.status_code == 200
        assert users.get_user(username="viewer").is_active is False

    def test_lost_concurrent_update_is_409(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        store = MagicMock(wraps=users)
        store.admin_set_password.side_effect = None
        store.admin_set_password.return_value = None
        app.dependency_overrides[get_user_store] = lambda: store

        response = _reset(
            client, "analyst", {"new_password": NEW_PASSWORD}, _bearer(token)
        )

        assert response.status_code == 409

    def test_target_vanished_during_reset_is_404(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        target = users.get_user(username="analyst")
        store = MagicMock(wraps=users)
        store.admin_set_password.side_effect = None
        store.admin_set_password.return_value = None
        store.get_user.side_effect = [target, None]
        app.dependency_overrides[get_user_store] = lambda: store

        response = _reset(
            client, "analyst", {"new_password": NEW_PASSWORD}, _bearer(token)
        )

        assert response.status_code == 404


class TestDatabasePasswordReset:
    def test_cas_forwarded_with_observed_version(self):
        record = MagicMock(id="user_analyst", auth_version=4)
        repo = MagicMock()
        repo.get_user_by_username.return_value = record
        updated = MagicMock()
        repo.set_password_hash_if_version.return_value = updated
        store = DatabaseUserStore.__new__(DatabaseUserStore)
        store._repo = repo
        store._to_user = lambda r: r

        assert (
            store.admin_set_password(username="analyst", new_password=NEW_PASSWORD)
            is updated
        )
        kwargs = repo.set_password_hash_if_version.call_args.kwargs
        assert kwargs["user_id"] == "user_analyst"
        assert kwargs["observed_auth_version"] == 4
        assert kwargs["password_hash"] != NEW_PASSWORD
        assert jwt_handler.verify_password(NEW_PASSWORD, kwargs["password_hash"])

    def test_lost_cas_returns_none(self):
        repo = MagicMock()
        repo.get_user_by_username.return_value = MagicMock(
            id="user_analyst", auth_version=4
        )
        repo.set_password_hash_if_version.return_value = None
        store = DatabaseUserStore.__new__(DatabaseUserStore)
        store._repo = repo
        store._to_user = lambda r: r

        assert (
            store.admin_set_password(username="analyst", new_password=NEW_PASSWORD)
            is None
        )


class TestRateLimits:
    def test_profile_update_limited_to_five_per_minute(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        codes = [
            _put(
                client, "analyst", {"full_name": f"Name {i}"}, _bearer(token)
            ).status_code
            for i in range(6)
        ]
        assert codes[:5] == [200] * 5
        assert codes[5] == 429

    def test_password_reset_limited_to_five_per_minute(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        codes = [
            _reset(
                client, "analyst", {"new_password": NEW_PASSWORD}, _bearer(token)
            ).status_code
            for _ in range(6)
        ]
        assert codes[:5] == [200] * 5
        assert codes[5] == 429


class TestPasswordResetAudit:
    def test_success_and_denied_events_without_secrets(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")

        with capture_logs() as events:
            ok = _reset(
                client, "analyst", {"new_password": NEW_PASSWORD}, _bearer(token)
            )
            denied = _reset(
                client, "admin", {"new_password": NEW_PASSWORD}, _bearer(token)
            )

        assert ok.status_code == 200 and denied.status_code == 400
        succeeded = next(
            e for e in events if e["event"] == "admin_user_password_reset_succeeded"
        )
        assert succeeded["actor_user_id"] == "user_admin"
        assert succeeded["target_user_id"] == "user_analyst"
        assert succeeded["auth_version"] == 1
        refused = next(
            e for e in events if e["event"] == "admin_user_password_reset_denied"
        )
        assert refused["reason"] == "self_target"
        hashed = users._users["analyst"].password_hash
        TestAuditLogging()._assert_no_secrets(events, token, NEW_PASSWORD, hashed)

    def test_profile_update_logs_no_email_or_name_values(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")

        with capture_logs() as events:
            _put(
                client,
                "analyst",
                {"email": "private.person@example.com", "full_name": "Private Person"},
                _bearer(token),
            )

        rendered = repr(events)
        assert "private.person@example.com" not in rendered
        assert "Private Person" not in rendered

    def test_failure_event_has_no_exception_text(self, client, stores):
        users, _ = stores
        token = _access(users, "admin")
        store = MagicMock(wraps=users)
        store.admin_set_password.side_effect = RuntimeError("db exploded host=secret")
        app.dependency_overrides[get_user_store] = lambda: store

        with capture_logs() as events:
            response = _reset(
                client, "analyst", {"new_password": NEW_PASSWORD}, _bearer(token)
            )

        assert response.status_code == 500
        failed = next(
            e for e in events if e["event"] == "admin_user_password_reset_failed"
        )
        assert failed["error_type"] == "RuntimeError"
        TestAuditLogging()._assert_no_secrets(
            events, token, NEW_PASSWORD, "db exploded", "host=secret"
        )
