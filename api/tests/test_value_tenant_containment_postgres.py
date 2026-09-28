"""H01 application containment on migrated, explicitly disposable PostgreSQL.

Run serially with other reset tests. Principals enter at the authenticated store
provider; JWT/HTTP coverage lives in test_value_tenant_containment.py. This does
not assert PostgreSQL role/RLS policy. Teardown leaves the server running.
"""

import os
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import TypedDict

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic.script import ScriptDirectory
from src.api.routers.values import get_authenticated_value_store
from src.auth.models import User, UserRole
from src.config.settings import settings
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.database.models import CurrentValuePointer, ValueContext, ValueRevision
from src.services.value_idempotency_store import (
    DatabaseValueIdempotencyStore,
    IdempotencyClaim,
    IdempotencyReplay,
)
from src.services.value_ingest import PreparedValueRecord
from src.services.value_store import DatabaseValueStore, get_value_store


class _IdempotencyAuthority(TypedDict):
    tenant_id: str
    user_id: str
    scope: str
    idempotency_key: str


@pytest.fixture
def disposable_postgres():
    database_url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.skip("H01 containment requires disposable PostgreSQL")

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


def _user(company_id):
    return User(
        id="h01-user",
        username="h01-user",
        email="h01@example.com",
        role=UserRole.DATA_MANAGER,
        company_id=company_id,
        created_at=datetime(2026, 9, 11),
        updated_at=datetime(2026, 9, 11),
    )


def test_disposable_postgres_idempotency_terminal_cas_is_tenant_bound(
    disposable_postgres,
):
    with Session(disposable_postgres) as db:
        store = DatabaseValueIdempotencyStore(db)
        identity = _IdempotencyAuthority(
            tenant_id="company-a",
            user_id="user-a",
            scope="values:create",
            idempotency_key="shared-key",
        )
        claim = store.claim(**identity, request_hash="a" * 64)
        assert isinstance(claim, IdempotencyClaim)
        foreign: _IdempotencyAuthority = {**identity, "tenant_id": "company-b"}
        with pytest.raises(ValueError, match="not in progress"):
            store.complete(
                **foreign,
                record_id=claim.record_id,
                response_status=201,
                response_body={"id": "foreign"},
            )
        store.abandon(**foreign, record_id=claim.record_id)
        store.complete(
            **identity,
            record_id=claim.record_id,
            response_status=201,
            response_body={"id": "original"},
        )
        with pytest.raises(ValueError, match="not in progress"):
            store.complete(
                **identity,
                record_id=claim.record_id,
                response_status=200,
                response_body={"id": "overwritten"},
            )
        store.abandon(**identity, record_id=claim.record_id)
        assert store.claim(**identity, request_hash="a" * 64) == IdempotencyReplay(
            status_code=201, body={"id": "original"}
        )


def _create(store, value_id, period, amount, *, commit=True):
    return store.create(
        value_id=value_id,
        concept="urn:sds:reg:test:water",
        entity="shared-plant",
        period=period,
        value=Decimal(amount),
        unit="kg",
        original_unit=None,
        conversion_applied=False,
        metadata=None,
        created_by="h01-user",
        commit=commit,
    )


def test_disposable_postgres_normal_user_tenant_containment(
    disposable_postgres, monkeypatch
):
    engine = disposable_postgres
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "company-b")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    filters = dict(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
    )
    periods = (date(2026, 1, 31), date(2026, 2, 28))

    # Hold both connections throughout: even commits cannot recycle one backend
    # between tenants. Writes are committed before the other session reads them.
    with engine.connect() as conn_a, engine.connect() as conn_b:
        assert conn_a.execute(text("SELECT pg_backend_pid()")).scalar_one() != (
            conn_b.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        conn_a.commit()
        conn_b.commit()
        with Session(bind=conn_a) as db_a, Session(bind=conn_b) as db_b:
            tenants = ("company-a", "company-b")
            stores = [
                get_authenticated_value_store(
                    request=request, db=db, current_user=_user(tenant)
                )
                for db, tenant in zip((db_a, db_b), tenants)
            ]
            ids = [set(), set()]
            for index, store in enumerate(stores):
                for period in periods:
                    value_id = f"h01-{index}-{period}"
                    result = _create(store, value_id, period, ("11", "29")[index])
                    assert result.id == value_id
                    ids[index].add(value_id)

            # A later configuration change cannot rebind either explicit tenant.
            monkeypatch.setattr(
                settings, "value_revision_default_tenant_id", "unrelated-default"
            )
            for index, store in enumerate(stores):
                own, foreign = ids[index], ids[1 - index]
                items, total = store.list(**filters, limit=10, offset=0)
                assert total == 2 and {item.id for item in items} == own
                assert {item.value for item in items} == {Decimal(("11", "29")[index])}
                first, total, cursor, more = store.list_cursor(
                    **filters, limit=1, cursor=None
                )
                assert total == 2 and more and cursor
                second, total, cursor, more = store.list_cursor(
                    **filters, limit=1, cursor=cursor
                )
                assert total == 2 and not more and cursor is None
                assert {item.id for item in first + second} == own
                for value_id in own:
                    assert store.get(value_id).id == value_id
                for value_id in foreign:
                    assert store.get(value_id) is None
                changes, _, more = store.list_changes(**filters, limit=10, cursor=None)
                assert not more and {item.id for item in changes} == own
                assert {item.id for item in store.iterate(**filters)} == own
                assert isinstance(store, DatabaseValueStore)
                assert {
                    item.id
                    for item in store.iterate_export_snapshot(**filters, limit=501)
                } == own
                assert (
                    store.latest(
                        concept="urn:sds:reg:test:water",
                        entity="shared-plant",
                        period_start=periods[0],
                        period_end=periods[1],
                    ).id
                    in own
                )

            # Inspect persisted context/revision/pointer ownership independently
            # of the filtered store responses, including the other session's commit.
            rows = (
                db_a.query(ValueContext, ValueRevision, CurrentValuePointer)
                .join(ValueRevision, ValueRevision.context_id == ValueContext.id)
                .join(
                    CurrentValuePointer,
                    CurrentValuePointer.revision_id == ValueRevision.id,
                )
                .all()
            )
            assert len(rows) == 4
            assert len({context.id for context, _, _ in rows}) == 4
            for context, revision, pointer in rows:
                index = tenants.index(context.tenant_id)
                assert revision.tenant_id == pointer.tenant_id == tenants[index]
                assert revision.source_record_id in ids[index]
            db_a.rollback()

            def reject_db_access(*_args, **_kwargs):
                pytest.fail("unbound operation attempted database access")

            # Instrument the real session and engine, without replacing them.
            # ORM access, flushing, or SQL execution must not precede rejection.
            listeners = (
                (db_a, "do_orm_execute"),
                (db_a, "before_flush"),
                (conn_a, "before_cursor_execute"),
            )
            for target, name in listeners:
                event.listen(target, name, reject_db_access)
            try:
                for tenant in (None, "", " \t"):
                    with pytest.raises(HTTPException) as denied:
                        get_authenticated_value_store(
                            request=request, db=db_a, current_user=_user(tenant)
                        )
                    assert denied.value.status_code == 403
                    for factory in (False, True):
                        store = (
                            get_value_store(request=request, db=db_a, tenant_id=tenant)
                            if factory
                            else DatabaseValueStore(db_a, tenant_id=tenant)
                        )
                        record = PreparedValueRecord(
                            value_id="h01-unbound",
                            concept="urn:sds:reg:test:water",
                            entity="shared-plant",
                            period=periods[0],
                            value=Decimal("99"),
                            value_type="numeric",
                            unit="kg",
                        )
                        operations = (
                            lambda: store.get(next(iter(ids[1]))),
                            lambda: store.list(**filters, limit=10, offset=0),
                            lambda: store.list_cursor(**filters, limit=1, cursor=None),
                            lambda: store.list_changes(
                                **filters, limit=10, cursor=None
                            ),
                            lambda: list(store.iterate(**filters)),
                            lambda: store.latest(
                                concept=record.concept,
                                entity=record.entity,
                                period_start=periods[0],
                                period_end=periods[1],
                            ),
                            lambda: _create(
                                store,
                                record.value_id,
                                record.period,
                                "99",
                                commit=False,
                            ),
                            lambda: store.save(
                                records=[record], created_by="h01-user", commit=False
                            ),
                            lambda: store.bulk_create(
                                records=[record], created_by="h01-user", commit=False
                            ),
                        )
                        for operation in operations:
                            with pytest.raises(HTTPException) as unavailable:
                                operation()
                            assert unavailable.value.status_code == 503
                            assert (
                                unavailable.value.detail == "Value service unavailable"
                            )
                        assert not db_a.in_transaction()
                        assert not db_a.new and not db_a.dirty and not db_a.deleted
            finally:
                for target, name in listeners:
                    event.remove(target, name, reject_db_access)
