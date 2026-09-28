"""Additional tests for RBAC coverage and edge cases."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.auth.models import Permission, User, UserRole
from src.auth.rbac import (
    create_permission_dependency,
    create_role_dependency,
    rbac,
    require_company_access,
    require_permission,
    require_role,
)


def _make_user(
    role: UserRole,
    permissions: list[Permission] | None = None,
    company_id: str | None = "company_1",
    is_active: bool = True,
) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id="user_1",
        username="user",
        email="user@example.com",
        full_name=None,
        company_id=company_id,
        role=role,
        auth_method="bearer",
        is_active=is_active,
        created_at=now,
        updated_at=now,
        last_login=None,
        permissions=permissions or [],
    )


def test_rbac_company_access_admin_override():
    admin = _make_user(
        UserRole.ADMIN, permissions=[Permission.READ_VALUES], company_id="a"
    )
    assert (
        rbac.can_access_company_data(admin, "other_company", allow_admin_override=True)
        is True
    )
    assert (
        rbac.can_access_company_data(admin, "other_company", allow_admin_override=False)
        is False
    )


def test_rbac_accessible_companies_regular_user_without_company():
    user = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], company_id=None
    )
    assert rbac.get_accessible_companies(user) == []


def test_rbac_permission_helpers_and_admin_accessible_companies():
    user = _make_user(
        UserRole.ANALYST,
        permissions=[Permission.READ_VALUES, Permission.CONVERT_UNITS],
    )
    admin = _make_user(UserRole.ADMIN)

    assert rbac.has_any_permission(
        user, [Permission.MANAGE_USERS, Permission.READ_VALUES]
    )
    assert rbac.has_all_permissions(
        user, [Permission.READ_VALUES, Permission.CONVERT_UNITS]
    )
    assert Permission.READ_VALUES in rbac.get_user_permissions(UserRole.VIEWER)
    assert rbac.get_accessible_companies(admin) == ["*"]


def test_rbac_check_resource_access_unknown_action_returns_false():
    user = _make_user(UserRole.ADMIN, permissions=[Permission.MANAGE_SYSTEM])
    assert (
        rbac.check_resource_access(user, resource_type="unknown", action="read")
        is False
    )


def test_rbac_check_resource_access_permission_and_company_checks():
    user = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], company_id="c1"
    )
    assert rbac.check_resource_access(user, "values", "read") is True
    assert rbac.check_resource_access(user, "values", "delete") is False
    assert (
        rbac.check_resource_access(user, "values", "read", resource_company_id="c2")
        is False
    )

    admin = _make_user(
        UserRole.ADMIN, permissions=[Permission.READ_VALUES], company_id="c1"
    )
    assert (
        rbac.check_resource_access(admin, "values", "read", resource_company_id="c2")
        is True
    )


def test_rbac_filter_by_company_access():
    admin = _make_user(
        UserRole.ADMIN, permissions=[Permission.READ_VALUES], company_id="c1"
    )
    user = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], company_id="c1"
    )
    items = [{"company_id": "c1"}, {"company_id": "c2"}]

    assert rbac.filter_by_company_access(admin, items) == items
    assert rbac.filter_by_company_access(user, items) == [{"company_id": "c1"}]

    no_company = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], company_id=None
    )
    assert rbac.filter_by_company_access(no_company, items) == []


@pytest.mark.asyncio
async def test_require_role_decorator_missing_user_raises_401():
    @require_role(UserRole.ADMIN)
    async def fn() -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn()
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_role_decorator_forbidden_and_allowed():
    viewer = _make_user(UserRole.VIEWER, permissions=[])
    admin = _make_user(UserRole.ADMIN, permissions=[])

    @require_role(UserRole.ADMIN)
    async def fn(current_user: User) -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn(viewer)
    assert exc.value.status_code == 403

    assert await fn(admin) == "ok"


@pytest.mark.asyncio
async def test_require_permission_decorator_forbidden_and_allowed():
    viewer = _make_user(UserRole.VIEWER, permissions=[])
    allowed = _make_user(UserRole.VIEWER, permissions=[Permission.READ_VALUES])

    @require_permission(Permission.READ_VALUES)
    async def fn(current_user: User) -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn(viewer)
    assert exc.value.status_code == 403

    assert await fn(allowed) == "ok"


@pytest.mark.asyncio
async def test_require_permission_decorator_missing_user_raises_401():
    @require_permission(Permission.READ_VALUES)
    async def fn() -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn()
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_company_access_decorator_cases():
    user = _make_user(
        UserRole.VIEWER, permissions=[Permission.READ_VALUES], company_id="c1"
    )
    admin = _make_user(
        UserRole.ADMIN, permissions=[Permission.READ_VALUES], company_id="c1"
    )

    @require_company_access()
    async def fn(current_user: User, company_id: str) -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn(user)  # company_id missing
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await fn(user, company_id="c2")
    assert exc.value.status_code == 403

    assert await fn(admin, company_id="c2") == "ok"


@pytest.mark.asyncio
async def test_require_company_access_decorator_missing_user_raises_401():
    @require_company_access()
    async def fn(company_id: str) -> str:
        return "ok"

    with pytest.raises(HTTPException) as exc:
        await fn(company_id="c1")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_dependency_factories_role_and_permission():
    viewer = _make_user(UserRole.VIEWER, permissions=[])
    admin = _make_user(UserRole.ADMIN, permissions=[Permission.MANAGE_USERS])

    role_dep = create_role_dependency(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await role_dep(current_user=viewer)
    assert exc.value.status_code == 403
    assert await role_dep(current_user=admin) == admin

    perm_dep = create_permission_dependency(Permission.MANAGE_USERS)
    with pytest.raises(HTTPException) as exc:
        await perm_dep(current_user=viewer)
    assert exc.value.status_code == 403
    assert await perm_dep(current_user=admin) == admin
