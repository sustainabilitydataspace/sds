"""H02 auth dependencies on migrated, explicitly disposable PostgreSQL.

Run serially with other schema-reset tests. Each authentication uses fresh DB
stores/session; this is not HTTP, RLS or in-flight revocation qualification.
"""

import os
import secrets
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic.script import ScriptDirectory
from src.api.routers.values import _authorized_revision_tenant_id
from src.auth import dependencies
from src.auth.authorization import has_cross_tenant_admin_access
from src.auth.models import APIKeyCreate, Permission, UserCreate, UserRole, UserUpdate
from src.config.settings import settings
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.services.api_key_store import DatabaseAPIKeyStore, get_api_key_store
from src.services.user_store import DatabaseUserStore, get_user_store


@contextmanager
def _disposable_postgres():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.skip("H02 containment requires disposable PostgreSQL")

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        init_db_for_engine(engine)
        with engine.connect() as connection:
            expected_head = ScriptDirectory.from_config(
                _alembic_config(connection)
            ).get_current_head()
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == expected_head
            )
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "database_url,allow_reset",
    [
        (None, None),
        (None, "true"),
        ("postgresql://unused.invalid/disposable", None),
        ("postgresql://unused.invalid/disposable", "false"),
        ("postgresql://unused.invalid/disposable", "TRUE"),
        ("sqlite://", "true"),
    ],
)
def test_disposable_postgres_skips_before_engine_creation(
    monkeypatch, database_url, allow_reset
):
    for name, value in (
        ("SDS_MIGRATION_TEST_DATABASE_URL", database_url),
        ("SDS_MIGRATION_TEST_ALLOW_RESET", allow_reset),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    def reject_engine(*args, **kwargs):
        pytest.fail("ungated test attempted engine creation")

    monkeypatch.setattr(__name__ + ".create_engine", reject_engine)
    with pytest.raises(pytest.skip.Exception):
        with _disposable_postgres():
            pytest.fail("ungated test reached database work")


@pytest.mark.asyncio
async def test_disposable_postgres_admin_key_containment_and_authority_reload(
    monkeypatch,
):
    with _disposable_postgres() as engine:
        monkeypatch.setattr(settings, "require_database", True)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        permissions = [Permission.READ_VALUES, Permission.CREATE_VALUES]
        with Session(engine) as db:
            users = get_user_store(request=request, db=db)
            keys = get_api_key_store(request=request, db=db)
            assert isinstance(users, DatabaseUserStore)
            assert isinstance(keys, DatabaseAPIKeyStore)
            owner = users.create_user(
                UserCreate(
                    username="h02-admin",
                    email="h02-admin@example.com",
                    password=secrets.token_urlsafe(32),
                    role=UserRole.ADMIN,
                    company_id="company-a",
                )
            )
            assert set(permissions) < set(owner.permissions)
            key = keys.create_api_key(
                user_id=owner.id,
                request=APIKeyCreate(name="h02-narrowed", permissions=permissions),
            )

        async def authenticate():
            # A new session models the next request after committed changes.
            with Session(engine) as db:
                users = get_user_store(request=request, db=db)
                token = await dependencies.get_token_data(
                    request=request,
                    credentials=None,
                    api_key=key.key,
                    api_keys=get_api_key_store(request=request, db=db),
                    users=users,
                )
                assert token.auth_method == "api_key" and token.api_key_id == key.id
                user = await dependencies.get_current_user(token, users=users)
                assert user.auth_method == "api_key" and user.api_key_id == key.id
                stored = users.get_user_by_id(user_id=owner.id)
                assert stored.auth_method is None and stored.api_key_id is None
                return await dependencies.get_current_active_user(user)

        principal = await authenticate()
        assert principal.id == owner.id and principal.role == UserRole.ADMIN
        assert principal.company_id == "company-a"
        assert principal.permissions == permissions
        assert not has_cross_tenant_admin_access(principal)
        company_access = dependencies.require_company_access()
        assert await company_access("company-a", principal) is principal
        with pytest.raises(HTTPException) as denied:
            await company_access("company-b", principal)
        assert denied.value.status_code == 403
        assert _authorized_revision_tenant_id(None, principal) == "company-a"
        with pytest.raises(HTTPException) as denied:
            _authorized_revision_tenant_id("company-b", principal)
        assert denied.value.status_code == 403

        # Administrative setup, not a key-authorized mutation endpoint.
        with Session(engine) as db:
            DatabaseUserStore(db).update_user(
                username=owner.username,
                update=UserUpdate(role=UserRole.VIEWER, company_id="company-b"),
                allow_admin_fields=True,
            )
        reloaded = await authenticate()
        assert reloaded.role == UserRole.VIEWER
        assert reloaded.company_id == "company-b"
        assert reloaded.permissions == [Permission.READ_VALUES]
        assert await company_access("company-b", reloaded) is reloaded
        with pytest.raises(HTTPException) as denied:
            await company_access("company-a", reloaded)
        assert denied.value.status_code == 403

        with Session(engine) as db:
            assert DatabaseAPIKeyStore(db).revoke_api_key(
                user_id=owner.id, key_id=key.id
            )
        with pytest.raises(HTTPException) as revoked:
            await authenticate()
        assert revoked.value.status_code == 401
