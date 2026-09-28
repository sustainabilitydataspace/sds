"""H02 containment through real HTTP auth, persisted keys/users and tenant data.

SQLite qualifies application call paths only; no revocation race or PostgreSQL
linearization claim. Catalog setup is reduced by the shared tenant fixture.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routers import auth, calculations, hierarchies, values
from src.auth import dependencies
from src.auth.authorization import has_cross_tenant_admin_access
from src.auth.jwt_handler import jwt_handler
from src.auth.models import Permission, TokenData, User, UserRole
from src.auth.rbac import create_role_dependency, rbac
from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    ContractResolutionMetadata,
)
from src.config.settings import settings
from src.database.models import APIKeyRecord, UserAccount, ValueRevision
from tests.test_value_tenant_containment import tenant_api as tenant_api


@pytest.fixture
def key_api(tenant_api, monkeypatch):
    client, db, tokens = tenant_api
    APIKeyRecord.__table__.create(db.get_bind())
    owner = db.query(UserAccount).filter_by(id="company-a").one()
    owner.role = "admin"
    db.commit()
    tokens["company-a"] = jwt_handler.create_access_token(
        user_id=owner.id,
        username=owner.username,
        role=UserRole.ADMIN,
        company_id=owner.company_id,
    )
    client.app.include_router(auth.router, prefix="/auth")
    client.app.include_router(hierarchies.router, prefix="/api/v1/hierarchies")
    client.app.include_router(calculations.router, prefix="/api/v1")
    monkeypatch.setattr(settings, "value_revision_api_enabled", True)
    bearer = {"Authorization": f"Bearer {tokens['company-a']}"}
    created = client.post(
        "/auth/api-keys",
        headers=bearer,
        json={
            "name": "scoped",
            "permissions": [
                "read_values",
                "create_values",
                "execute_calculations",
                "view_hierarchies",
                "manage_hierarchies",
            ],
        },
    )
    assert created.status_code == 200, created.text
    key = created.json()
    yield client, db, tokens, bearer, {"X-API-Key": key["key"]}, key["id"]


def _seed_values(client, tokens):
    ids = {}
    for company, amount in (("company-a", 11), ("company-b", 29)):
        response = client.post(
            "/api/v1/values",
            headers={"Authorization": f"Bearer {tokens[company]}"},
            json={
                "concept": "urn:sds:reg:test:water",
                "entity": "shared-plant",
                "period": "2026-01-31",
                "value": amount,
                "unit": "kg",
            },
        )
        assert response.status_code == 201, response.text
        ids[company] = response.json()["id"]
    return ids


def test_admin_key_value_reads_are_tenant_local_bearer_revision_override_retained(
    key_api,
):
    client, db, tokens, bearer, key, _ = key_api
    ids = _seed_values(client, tokens)
    for path in ("/values", "/values?tenant_id=company-b", "/values/revisions"):
        response = client.get("/api/v1" + path, headers=key)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
    assert (
        client.get(f"/api/v1/values/{ids['company-a']}", headers=key).status_code == 200
    )
    assert (
        client.get(f"/api/v1/values/{ids['company-b']}", headers=key).status_code == 404
    )
    export = client.get("/api/v1/values/export?format=json", headers=key)
    assert export.status_code == 200
    assert {item["id"] for item in export.json()} == {ids["company-a"]}
    changes = client.get("/api/v1/values/changes", headers=key)
    assert changes.status_code == 200
    assert {item["record"]["id"] for item in changes.json()["items"]} == {
        ids["company-a"]
    }
    foreign = db.query(ValueRevision).filter_by(tenant_id="company-b").one()
    for path in (
        "/values/revisions?tenant_id=company-b",
        "/values/revisions/changes?tenant_id=company-b",
        f"/values/revisions/{foreign.id}/lineage",
        f"/values/contexts/{foreign.context_id}/lineage",
    ):
        denied = client.get("/api/v1" + path, headers=key)
        assert denied.status_code == (404 if "/lineage" in path else 403), denied.text
        allowed = client.get("/api/v1" + path, headers=bearer)
        assert allowed.status_code == 200, allowed.text
    assert client.get("/api/v1/values/revisions", headers=bearer).json()["total"] == 2


def _hierarchy(company):
    return {
        "company_id": company,
        "hierarchy_type": "organizational",
        "name": company,
        "active": True,
        "levels": [{"id": "shared-plant", "name": "Shared", "level": 0}],
    }


def test_admin_key_hierarchy_crud_cannot_cross_tenant_bearer_retains_override(key_api):
    client, db, tokens, bearer, key, _ = key_api
    path = "/api/v1/hierarchies"
    assert client.get(path, headers=key).json()["total"] == 1
    assert client.get(path, headers=bearer).json()["total"] == 2
    assert client.get(path + "?company_id=company-b", headers=key).status_code == 403
    assert client.get(path + "?company_id=company-b", headers=bearer).status_code == 200
    assert (
        client.post(path, headers=key, json=_hierarchy("company-b")).status_code == 403
    )
    for method, suffix, body in (
        ("GET", "", None),
        ("PUT", "", _hierarchy("company-b")),
        ("POST", "/activate", None),
        ("DELETE", "", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        denied = client.request(
            method, path + "/company-b" + suffix, headers=key, **kwargs
        )
        assert denied.status_code == 404, denied.text
        allowed = client.request(
            method, path + "/company-b" + suffix, headers=bearer, **kwargs
        )
        assert allowed.status_code == 200, allowed.text
    own = client.post(path, headers=key, json=_hierarchy("company-a"))
    assert own.status_code == 201, own.text
    own_path = path + "/" + own.json()["id"]
    assert client.get(own_path, headers=key).status_code == 200
    assert (
        client.put(own_path, headers=key, json=_hierarchy("company-a")).status_code
        == 200
    )
    assert client.post(own_path + "/activate", headers=key).status_code == 200
    assert client.delete(own_path, headers=key).status_code == 200


def test_admin_key_calculation_uses_own_values_and_hierarchy(key_api, monkeypatch):
    client, db, tokens, bearer, key, _ = key_api
    ids = _seed_values(client, tokens)
    contract = CalculationContract(
        contract_id="h02-test",
        concept="urn:sds:reg:test:total",
        contract_version="1",
        contract_hash="sha256:h02-test",
        runtime_status="executable",
        formula="water",
        result_unit="kg",
        inputs=(
            CalculationContractInput("water", "urn:sds:reg:test:water", unit="kg"),
        ),
        resolution=ContractResolutionMetadata(source="test"),
    )
    monkeypatch.setattr(
        calculations,
        "RuntimeCalculationContractResolver",
        lambda db: SimpleNamespace(resolve=lambda _: contract),
    )
    monkeypatch.setattr(calculations, "StandardMappingStore", lambda db: None)
    payload = {
        "concept": contract.concept,
        "entity": "shared-plant",
        "period": "2026",
        "granularity": "annual",
        "include_trace": True,
    }
    response = client.post("/api/v1/calculate", headers=key, json=payload)
    assert response.status_code == 200, response.text
    assert float(response.json()["value"]) == 11
    assert response.json()["source_value_ids"] == [ids["company-a"]]
    # A foreign-only entity exists and is executable for the bearer admin, but
    # the narrowed admin key cannot use the foreign hierarchy to resolve it.
    foreign = _hierarchy("company-b")
    foreign["levels"][0]["id"] = "foreign-plant"
    assert (
        client.put(
            "/api/v1/hierarchies/company-b", headers=bearer, json=foreign
        ).status_code
        == 200
    )
    foreign_payload = dict(payload, entity="foreign-plant")
    denied = client.post("/api/v1/calculate", headers=key, json=foreign_payload)
    assert denied.status_code == 400, denied.text
    assert "Unknown entity" in denied.text
    # Scope at the real calculation boundary: bearer sees the foreign hierarchy.
    # Supply an optional-input default to establish success without foreign inputs;
    # revision compatibility reads remain tenant-bound even for bearer ADMIN.
    constant = CalculationContract(
        contract_id="h02-constant",
        concept=contract.concept,
        contract_version="1",
        contract_hash="sha256:h02-constant",
        runtime_status="executable",
        formula="water",
        result_unit="kg",
        inputs=(
            CalculationContractInput(
                "water",
                "urn:sds:reg:test:water",
                unit="kg",
                required=False,
                default_value=1,
            ),
        ),
        resolution=ContractResolutionMetadata(source="test"),
    )
    monkeypatch.setattr(
        calculations,
        "RuntimeCalculationContractResolver",
        lambda db: SimpleNamespace(resolve=lambda _: constant),
    )
    allowed = client.post("/api/v1/calculate", headers=bearer, json=foreign_payload)
    assert allowed.status_code == 200, allowed.text
    assert float(allowed.json()["value"]) == 1
    batch = client.get(
        "/api/v1/calculate/batch",
        headers=key,
        params={
            "entity": "foreign-plant",
            "period": "2026",
            "concepts": contract.concept,
        },
    )
    assert batch.status_code == 200
    assert batch.json()[0]["status"] == "failed"
    assert "Unknown entity" in batch.json()[0]["error"]


@pytest.mark.parametrize(
    "permissions", [[], ["read_values"], [p.value for p in Permission]]
)
def test_api_key_cannot_chain_revoke_or_mutate_identity(key_api, permissions):
    client, db, tokens, bearer, _, _ = key_api
    created = client.post(
        "/auth/api-keys",
        headers=bearer,
        json={"name": "mutation-probe", "permissions": permissions},
    )
    assert created.status_code == 200
    key = {"X-API-Key": created.json()["key"]}
    key_count = db.query(APIKeyRecord).count()
    owner_before = client.get("/auth/me", headers=bearer).json()
    for method, path, body in (
        ("POST", "/auth/api-keys", {"name": "child", "permissions": []}),
        ("POST", "/auth/api-keys", {"name": "child", "permissions": ["read_values"]}),
        ("DELETE", f"/auth/api-keys/{created.json()['id']}", None),
        ("PUT", "/auth/me", {"full_name": "Changed"}),
        ("PUT", "/auth/me", {"email": "changed@example.com"}),
        ("PUT", "/auth/me", {"company_id": "company-b"}),
        ("PUT", "/auth/me", {"role": "viewer", "is_active": False}),
        (
            "POST",
            "/auth/change-password",
            {"current_password": "unused", "new_password": "changed-password"},
        ),
        (
            "POST",
            "/auth/users",
            {
                "username": "child",
                "email": "child@example.com",
                "password": "child-password",
                "role": "admin",
                "company_id": "company-b",
            },
        ),
    ):
        response = client.request(
            method, path, headers=key, **({"json": body} if body else {})
        )
        assert response.status_code == 403, (path, response.text)
    db.expire_all()
    assert db.query(APIKeyRecord).count() == key_count
    assert db.query(APIKeyRecord).filter_by(id=created.json()["id"]).one().is_active
    assert db.query(UserAccount).count() == 2
    assert client.get("/auth/me", headers=bearer).json() == owner_before
    # Read-only key metadata and current-user views are still supported.
    assert client.get("/auth/api-keys", headers=key).status_code == 200
    me = client.get("/auth/me", headers=key)
    assert me.status_code == 200
    assert set(me.json()["permissions"]) == set(permissions)
    assert "auth_method" not in me.json() and "api_key_id" not in me.json()
    assert (
        client.delete(
            f"/auth/api-keys/{created.json()['id']}", headers=bearer
        ).status_code
        == 200
    )
    assert client.get("/auth/me", headers=key).status_code == 401


@pytest.mark.parametrize("database", [False, True])
@pytest.mark.asyncio
async def test_provenance_immutable_private_and_not_written_into_shared_user(
    monkeypatch, database
):
    monkeypatch.setattr(settings, "require_database", database)
    now = datetime.now(timezone.utc)
    stored = User(
        id="owner",
        username="owner",
        email="owner@example.com",
        role=UserRole.ADMIN,
        company_id="company-a",
        created_at=now,
        updated_at=now,
        permissions=[Permission.READ_VALUES, Permission.MANAGE_USERS],
    )
    users = SimpleNamespace(get_user_by_id=lambda user_id: stored)
    data = await dependencies.get_token_data(
        credentials=None,
        api_key="test-only",
        api_keys=SimpleNamespace(
            authenticate=lambda _: SimpleNamespace(
                user_id=stored.id,
                key_id="key-id",
                permissions=[Permission.READ_VALUES, Permission.DELETE_VALUES],
            )
        ),
        users=users,
    )
    user = await dependencies.get_current_user(token_data=data, users=users)
    assert user is not stored
    assert stored.auth_method is None and stored.api_key_id is None
    assert stored.permissions == [Permission.READ_VALUES, Permission.MANAGE_USERS]
    assert user.permissions == [Permission.READ_VALUES]
    for model in (data, user):
        assert model.auth_method == "api_key" and model.api_key_id == "key-id"
        for field, value in (("auth_method", "bearer"), ("api_key_id", "other")):
            with pytest.raises(ValidationError, match="frozen"):
                setattr(model, field, value)
            assert field not in model.model_dump()
            assert field not in model.model_json_schema()["properties"]
            assert field not in repr(model)
    bearer = await dependencies.get_current_user(
        TokenData(
            user_id=stored.id,
            username=stored.username,
            role=stored.role,
            company_id=stored.company_id,
            permissions=stored.permissions,
        ),
        users=users,
    )
    assert bearer.auth_method == "bearer" and bearer.api_key_id is None
    assert has_cross_tenant_admin_access(bearer)
    assert not has_cross_tenant_admin_access(user)
    assert not has_cross_tenant_admin_access(stored)


@pytest.mark.asyncio
async def test_generic_helpers_deny_api_key_role_override():
    user = SimpleNamespace(
        auth_method="api_key",
        role=UserRole.ADMIN,
        company_id="company-a",
        permissions=[Permission.READ_VALUES],
        id="owner",
        username="owner",
    )
    assert not rbac.has_role(user, UserRole.VIEWER)
    assert not rbac.can_access_company_data(user, "company-b")
    assert rbac.can_access_company_data(user, "company-a")
    assert rbac.get_accessible_companies(user) == ["company-a"]
    assert rbac.filter_by_company_access(user, [{"company_id": "company-b"}]) == []
    assert not rbac.check_resource_access(user, "values", "read", "company-b")
    for checker in (
        dependencies.require_role(UserRole.ADMIN),
        create_role_dependency(UserRole.ADMIN),
    ):
        with pytest.raises(HTTPException) as denied:
            await checker(current_user=user)
        assert denied.value.status_code == 403
    checker = dependencies.require_company_access()
    with pytest.raises(HTTPException) as denied:
        await checker(company_id="company-b", current_user=user)
    assert denied.value.status_code == 403
    assert await checker(company_id="company-a", current_user=user) is user


@pytest.mark.parametrize("router", [values, calculations])
def test_legacy_provider_key_has_no_admin_wildcard(monkeypatch, router):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "legacy")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    key = SimpleNamespace(
        auth_method="api_key", role=UserRole.ADMIN, company_id="company-a"
    )
    bearer = SimpleNamespace(
        auth_method="bearer", role=UserRole.ADMIN, company_id="company-a"
    )
    assert router._value_store_tenant_id(key) == "company-a"
    assert router._value_store_tenant_id(bearer) is None
    key.company_id = None
    with pytest.raises(HTTPException) as denied:
        router._value_store_tenant_id(key)
    assert denied.value.status_code == 403
    monkeypatch.setattr(settings, "require_database", False)
    assert router._value_store_tenant_id(key) is None  # Existing offline policy.
    with pytest.raises(HTTPException) as denied:
        calculations._hierarchy_scope_company_id(key)
    assert denied.value.status_code == 403


def test_bearer_identity_mutations_and_credential_precedence_retained(key_api):
    client, db, tokens, bearer, key, _ = key_api
    owner = db.query(UserAccount).filter_by(id="company-a").one()
    owner.password_hash = jwt_handler.hash_password("old-test-password")
    db.commit()
    changed = client.post(
        "/auth/change-password",
        headers=bearer,
        json={
            "current_password": "old-test-password",
            "new_password": "new-test-password",
        },
    )
    assert changed.status_code == 200, changed.text
    db.expire_all()
    assert jwt_handler.verify_password("new-test-password", owner.password_hash)
    assert client.get("/auth/me", headers=bearer).status_code == 401
    login = client.post(
        "/auth/login",
        json={"username": owner.username, "password": "new-test-password"},
    )
    assert login.status_code == 200, login.text
    fresh_bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = client.post(
        "/auth/users",
        headers=fresh_bearer,
        json={
            "username": "new-user",
            "password": "test-password",
            "email": "new@example.com",
            "company_id": "company-b",
            "role": "viewer",
        },
    )
    assert created.status_code == 200, created.text
    updated = client.put(
        "/auth/me",
        headers=fresh_bearer,
        json={"company_id": "company-b", "full_name": "Updated"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["company_id"] == "company-b"
    assert client.get("/auth/me", headers=fresh_bearer).status_code == 401
    relogin = client.post(
        "/auth/login",
        json={"username": owner.username, "password": "new-test-password"},
    )
    assert relogin.status_code == 200, relogin.text
    current_bearer = {"Authorization": f"Bearer {relogin.json()['access_token']}"}
    # Key requests follow current company membership, without gaining wildcard.
    assert (
        client.get("/api/v1/hierarchies?company_id=company-a", headers=key).status_code
        == 403
    )
    assert (
        client.get("/api/v1/hierarchies?company_id=company-b", headers=key).status_code
        == 200
    )
    # Existing precedence: a valid bearer wins; an invalid bearer cannot fall
    # back to an otherwise valid key.
    both = dict(key, **current_bearer)
    child = client.post("/auth/api-keys", headers=both, json={"name": "bearer-child"})
    assert child.status_code == 200
    invalid = dict(key, Authorization="Bearer invalid")
    assert client.get("/auth/me", headers=invalid).status_code == 401


def test_offline_key_mutations_are_contained(client, admin_token):
    bearer = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/auth/api-keys",
        headers=bearer,
        json={"name": "offline-key", "permissions": [p.value for p in Permission]},
    )
    assert created.status_code == 200
    key = {"X-API-Key": created.json()["key"]}
    for method, path, body in (
        ("POST", "/auth/api-keys", {"name": "child"}),
        ("DELETE", f"/auth/api-keys/{created.json()['id']}", None),
        ("PUT", "/auth/me", {"company_id": "company-b"}),
        (
            "POST",
            "/auth/change-password",
            {"current_password": "admin123", "new_password": "changed-password"},
        ),
        (
            "POST",
            "/auth/users",
            {
                "username": "child",
                "email": "child@example.com",
                "password": "child-password",
            },
        ),
    ):
        response = client.request(
            method, path, headers=key, **({"json": body} if body else {})
        )
        assert response.status_code == 403, (path, response.text)
    assert client.get("/auth/me", headers=key).status_code == 200
