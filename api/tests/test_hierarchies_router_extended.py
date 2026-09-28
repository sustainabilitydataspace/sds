"""Hierarchy router coverage tests (offline-safe)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.models import HierarchyConfiguration, HierarchyLevel
from src.api.routers import hierarchies
from src.auth.models import UserRole


def _admin_user() -> SimpleNamespace:
    """Admin caller so tenant scoping is permissive for generic-path coverage."""
    return SimpleNamespace(
        role=UserRole.ADMIN, company_id="acme", username="t", auth_method="bearer"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _valid_hierarchy_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_id": "acme",
        "hierarchy_type": "organizational",
        "name": "Test Hierarchy",
        "description": "test",
        "levels": [
            {
                "id": "global",
                "name": "Global",
                "parent": None,
                "level": 0,
                "metadata": {},
            },
            {
                "id": "spain",
                "name": "Spain",
                "parent": "global",
                "level": 1,
                "metadata": {},
            },
        ],
        "active": True,
    }
    base.update(overrides)
    return base


def test_hierarchies_create_list_get_update_delete_activate_offline_store(
    client, viewer_token, data_manager_token
):
    payload = _valid_hierarchy_payload(company_id="sds_company")
    create = client.post(
        "/api/v1/hierarchies",
        headers=_auth(data_manager_token),
        json=payload,
    )
    assert create.status_code == 201
    created = create.json()
    assert created["company_id"] == "sds_company"
    assert created["hierarchy_type"] == "organizational"
    assert created["id"] is not None
    hierarchy_id = created["id"]

    listing = client.get(
        "/api/v1/hierarchies?company_id=sds_company&active=true&limit=10&offset=0",
        headers=_auth(viewer_token),
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == hierarchy_id

    get_ok = client.get(
        f"/api/v1/hierarchies/{hierarchy_id}", headers=_auth(viewer_token)
    )
    assert get_ok.status_code == 200
    assert get_ok.json()["name"] == "Test Hierarchy"

    update = client.put(
        f"/api/v1/hierarchies/{hierarchy_id}",
        headers=_auth(data_manager_token),
        json=_valid_hierarchy_payload(company_id="sds_company", name="Updated"),
    )
    assert update.status_code == 200
    assert update.json()["id"] == hierarchy_id
    assert update.json()["name"] == "Updated"

    activate = client.post(
        f"/api/v1/hierarchies/{hierarchy_id}/activate",
        headers=_auth(data_manager_token),
    )
    assert activate.status_code == 200

    delete = client.delete(
        f"/api/v1/hierarchies/{hierarchy_id}", headers=_auth(data_manager_token)
    )
    assert delete.status_code == 200

    # Default listing returns active only; deleted config is now inactive.
    listing_after_delete = client.get(
        "/api/v1/hierarchies?company_id=sds_company", headers=_auth(viewer_token)
    )
    assert listing_after_delete.status_code == 200
    assert listing_after_delete.json()["total"] == 0

    inactive_listing = client.get(
        "/api/v1/hierarchies?company_id=sds_company&active=false",
        headers=_auth(viewer_token),
    )
    assert inactive_listing.status_code == 200
    assert inactive_listing.json()["total"] == 1
    assert inactive_listing.json()["items"][0]["active"] is False


def test_hierarchies_validation_errors(client, data_manager_token):
    # duplicate IDs
    dup = _valid_hierarchy_payload(
        levels=[
            {"id": "x", "name": "X", "parent": None, "level": 0, "metadata": {}},
            {"id": "x", "name": "X2", "parent": None, "level": 0, "metadata": {}},
        ]
    )
    r = client.post("/api/v1/hierarchies", headers=_auth(data_manager_token), json=dup)
    # Duplicate IDs are rejected by the Pydantic model validator (422) before router validation runs.
    assert r.status_code == 422

    # invalid parent reference
    bad_parent = _valid_hierarchy_payload(
        levels=[
            {
                "id": "c",
                "name": "Child",
                "parent": "missing",
                "level": 1,
                "metadata": {},
            },
            {"id": "root", "name": "Root", "parent": None, "level": 0, "metadata": {}},
        ]
    )
    r = client.post(
        "/api/v1/hierarchies", headers=_auth(data_manager_token), json=bad_parent
    )
    assert r.status_code == 400

    # cycle
    cycle = _valid_hierarchy_payload(
        levels=[
            {"id": "a", "name": "A", "parent": "b", "level": 1, "metadata": {}},
            {"id": "b", "name": "B", "parent": "a", "level": 2, "metadata": {}},
        ]
    )
    r = client.post(
        "/api/v1/hierarchies", headers=_auth(data_manager_token), json=cycle
    )
    assert r.status_code == 400

    # parent level must be lower than child
    bad_levels = _valid_hierarchy_payload(
        levels=[
            {"id": "root", "name": "Root", "parent": None, "level": 3, "metadata": {}},
            {
                "id": "child",
                "name": "Child",
                "parent": "root",
                "level": 2,
                "metadata": {},
            },
        ]
    )
    r = client.put(
        "/api/v1/hierarchies/cfg_1", headers=_auth(data_manager_token), json=bad_levels
    )
    assert r.status_code == 400


def test_hierarchies_list_empty_when_no_configs(client, viewer_token):
    listing = client.get("/api/v1/hierarchies", headers=_auth(viewer_token))
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_delete_missing_hierarchy_returns_404(client, data_manager_token):
    response = client.delete(
        "/api/v1/hierarchies/missing-hierarchy",
        headers=_auth(data_manager_token),
    )

    assert response.status_code == 404, response.text
    assert "Hierarchy not found" in response.text


def _config() -> HierarchyConfiguration:
    return HierarchyConfiguration(
        company_id="acme",
        hierarchy_type="organizational",
        name="Hierarchy",
        levels=[
            HierarchyLevel(id="global", name="Global", parent=None, level=0),
            HierarchyLevel(id="site", name="Site", parent="global", level=1),
        ],
        active=True,
    )


class _FailingStore:
    def __init__(self, method_name: str):
        self.method_name = method_name

    def create(self, *_args, **_kwargs):
        if self.method_name == "create":
            raise RuntimeError("create failed")
        return _config()

    def list(self, **_kwargs):
        if self.method_name == "list":
            raise RuntimeError("list failed")
        return [_config()]

    def get(self, _hierarchy_id):
        if self.method_name == "get":
            raise RuntimeError("get failed")
        # Return an existing config so update/delete/activate reach the failing op
        # (the router now pre-fetches for tenant scoping before mutating).
        return _config()

    def update(self, *_args, **_kwargs):
        if self.method_name == "update":
            raise RuntimeError("update failed")
        return None

    def delete(self, _hierarchy_id):
        if self.method_name == "delete":
            raise RuntimeError("delete failed")
        return False

    def activate(self, _hierarchy_id):
        if self.method_name == "activate":
            raise RuntimeError("activate failed")
        return False


@pytest.mark.asyncio
async def test_hierarchy_router_direct_exception_edges():
    user = _admin_user()
    for method_name, call in (
        (
            "create",
            lambda store: hierarchies.create_hierarchy(
                config=_config(), store=store, current_user=user
            ),
        ),
        (
            "list",
            lambda store: hierarchies.list_hierarchies(
                company_id=None,
                hierarchy_type=None,
                active=None,
                limit=10,
                offset=0,
                store=store,
                current_user=user,
            ),
        ),
        (
            "get",
            lambda store: hierarchies.get_hierarchy(
                hierarchy_id="missing", store=store, current_user=user
            ),
        ),
        (
            "update",
            lambda store: hierarchies.update_hierarchy(
                hierarchy_id="missing",
                config=_config(),
                store=store,
                current_user=user,
            ),
        ),
        (
            "delete",
            lambda store: hierarchies.delete_hierarchy(
                hierarchy_id="missing", store=store, current_user=user
            ),
        ),
        (
            "activate",
            lambda store: hierarchies.activate_hierarchy(
                hierarchy_id="missing", store=store, current_user=user
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call(_FailingStore(method_name))
        assert exc.value.status_code == 500


def test_hierarchy_router_validation_direct_edges():
    with pytest.raises(ValueError, match="At least one level"):
        hierarchies._validate_hierarchy_structure([])
    with pytest.raises(ValueError, match="Duplicate"):
        hierarchies._validate_hierarchy_structure(
            [
                HierarchyLevel(id="a", name="A", parent=None, level=0),
                HierarchyLevel(id="a", name="A2", parent=None, level=1),
            ]
        )


# --- tenant isolation regression (codex F05 M1) -------------------------------


def _tenant_user(company_id, *, admin=False):
    return SimpleNamespace(
        role=UserRole.ADMIN if admin else UserRole.DATA_MANAGER,
        auth_method="bearer",
        company_id=company_id,
        username=f"u-{company_id}",
    )


@pytest.mark.asyncio
async def test_hierarchy_tenant_isolation_blocks_cross_company_access():
    from src.services.hierarchy_store import InMemoryHierarchyStore

    store = InMemoryHierarchyStore()
    owner = _tenant_user("acme")
    other = _tenant_user("globex")

    # owner creates a config in their own company
    created = await hierarchies.create_hierarchy(
        config=_config(), store=store, current_user=owner
    )
    hid = created.id
    assert created.company_id == "acme"

    # a different tenant cannot get / update / delete / activate it (404, no leak)
    for call in (
        lambda: hierarchies.get_hierarchy(
            hierarchy_id=hid, store=store, current_user=other
        ),
        lambda: hierarchies.update_hierarchy(
            hierarchy_id=hid, config=_config(), store=store, current_user=other
        ),
        lambda: hierarchies.delete_hierarchy(
            hierarchy_id=hid, store=store, current_user=other
        ),
        lambda: hierarchies.activate_hierarchy(
            hierarchy_id=hid, store=store, current_user=other
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404

    # and a cross-tenant list returns nothing for the other tenant
    items, total = (
        await hierarchies.list_hierarchies(
            company_id=None,
            hierarchy_type=None,
            active=None,
            limit=10,
            offset=0,
            store=store,
            current_user=other,
        )
    ).items, None
    assert items == []

    # the owner still sees it
    owner_view = await hierarchies.get_hierarchy(
        hierarchy_id=hid, store=store, current_user=owner
    )
    assert owner_view.id == hid


@pytest.mark.asyncio
async def test_hierarchy_non_admin_cannot_target_other_company():
    from src.services.hierarchy_store import InMemoryHierarchyStore

    store = InMemoryHierarchyStore()
    user = _tenant_user("acme")

    # creating for another company is forced/denied (cannot specify a foreign company)
    foreign = HierarchyConfiguration(
        company_id="globex",
        hierarchy_type="organizational",
        name="X",
        levels=[HierarchyLevel(id="g", name="G", parent=None, level=0)],
        active=True,
    )
    with pytest.raises(HTTPException) as exc:
        await hierarchies.create_hierarchy(
            config=foreign, store=store, current_user=user
        )
    assert exc.value.status_code == 403

    # listing with a foreign company filter is denied
    with pytest.raises(HTTPException) as exc:
        await hierarchies.list_hierarchies(
            company_id="globex",
            hierarchy_type=None,
            active=None,
            limit=10,
            offset=0,
            store=store,
            current_user=user,
        )
    assert exc.value.status_code == 403
