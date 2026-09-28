"""Tenant-bound value operations; these regressions do not define admin policy."""

import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rdflib import RDF, Graph, URIRef
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routers import calculations, values
from src.auth.jwt_handler import jwt_handler
from src.auth.models import UserRole
from src.config.settings import settings
from src.database import session as database_session
from src.database.models import (
    CurrentValuePointer,
    ESGValue,
    HierarchyConfiguration,
    ReportedValuePointer,
    RevokedToken,
    UserAccount,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.services.change_feed import decode_change_feed_cursor
from src.services.value_ingest import PreparedValueRecord
from src.services.value_pagination import encode_value_cursor
from src.services.value_store import DatabaseValueStore, get_value_store


@compiles(JSONB, "sqlite")
@compiles(ARRAY, "sqlite")
def _compile_json_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def _record(value_id="value-1", period=date(2026, 1, 31)):
    return PreparedValueRecord(
        value_id=value_id,
        concept="urn:sds:reg:test:water",
        entity="shared-plant",
        period=period,
        value=Decimal("7"),
        value_type="numeric",
        unit="kg",
    )


@pytest.mark.parametrize("tenant", [None, "", " \t"])
@pytest.mark.parametrize(
    "read_path,dual_write", [("revision", False), ("legacy", True)]
)
@pytest.mark.parametrize("factory", [False, True])
def test_unbound_operations_fail_before_database_access(
    monkeypatch, tenant, read_path, dual_write, factory
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "company-b")
    # Unbound construction remains usable by preparation-only CLI paths.
    db = MagicMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    kwargs = {} if tenant is None else {"tenant_id": tenant}
    store = (
        get_value_store(request=request, db=db, **kwargs)
        if factory
        else DatabaseValueStore(db, **kwargs)
    )
    # Also cover enabling revisions after an unbound store was constructed.
    monkeypatch.setattr(settings, "value_revision_primary_read_path", read_path)
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", dual_write)
    filters = dict(
        concept=None,
        entity=None,
        period_start=None,
        period_end=None,
        unit=None,
        changed_since=None,
    )
    record = _record()
    operations = [
        lambda: store.create(
            value_id=record.value_id,
            concept=record.concept,
            entity=record.entity,
            period=record.period,
            value=record.value,
            unit=record.unit,
            original_unit=None,
            conversion_applied=False,
            metadata=None,
            created_by="test",
            commit=False,
        ),
        lambda: store.save(records=[record], created_by="test", commit=False),
        lambda: store.bulk_create(records=[record], created_by="test", commit=False),
        lambda: store.get(record.value_id),
        lambda: store.list(**filters, limit=10, offset=0),
        lambda: store.list_cursor(**filters, limit=10, cursor=None),
        lambda: store.list_changes(**filters, limit=10, cursor=None),
        lambda: list(store.iterate(**filters)),
        lambda: store.latest(
            concept=record.concept,
            entity=record.entity,
            period_start=record.period,
            period_end=record.period,
        ),
    ]
    for operation in operations:
        with pytest.raises(HTTPException) as unavailable:
            operation()
        assert unavailable.value.status_code == 503
        assert unavailable.value.detail == "Value service unavailable"
    assert db.mock_calls == []


@pytest.fixture
def tenant_api(monkeypatch):
    """Real JWT verification, persisted users/revocations and SQL value queries."""
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    monkeypatch.setattr(settings, "value_revision_dual_write_enabled", False)
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "company-b")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (
        UserAccount,
        RevokedToken,
        HierarchyConfiguration,
        ESGValue,
        ValueContext,
        ValueRevision,
        ValueRevisionEvent,
        CurrentValuePointer,
        ReportedValuePointer,
    ):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database_session, "SessionLocal", sessions)
    db = sessions()
    tokens = {}
    for company in ("company-a", "company-b"):
        db.add(
            UserAccount(
                id=company,
                username=company,
                email=f"{company}@example.com",
                company_id=company,
                role=UserRole.DATA_MANAGER.value,
                password_hash="unused-test-password-hash",
                is_active=True,
            )
        )
        db.add(
            HierarchyConfiguration(
                id=company,
                company_id=company,
                hierarchy_type="organizational",
                name=company,
                is_active=True,
                configuration=json.dumps(
                    dict(
                        id=company,
                        company_id=company,
                        hierarchy_type="organizational",
                        name=company,
                        active=True,
                        levels=[dict(id="shared-plant", name="Shared label", level=0)],
                    )
                ),
            )
        )
        tokens[company] = jwt_handler.create_access_token(
            user_id=company,
            username=company,
            role=UserRole.DATA_MANAGER,
            company_id=company,
        )
    db.commit()
    app = FastAPI()
    app.include_router(values.router, prefix="/api/v1")

    def local_db():
        with sessions() as request_db:
            yield request_db

    app.dependency_overrides[database_session.get_db_optional] = local_db
    # Keep catalog/unit setup small; auth, hierarchy, ingestion and values are real.
    graph = Graph()
    graph.add((URIRef(_record().concept), RDF.type, URIRef("urn:test:Concept")))
    app.dependency_overrides[values.get_ontology_graph] = lambda: graph
    app.dependency_overrides[values.get_unit_converter] = lambda: SimpleNamespace()
    monkeypatch.setattr(values, "_canonical_concept_store_for", lambda *_: None)
    monkeypatch.setattr(values, "_indicator_store_for", lambda *_: None)
    monkeypatch.setattr(values, "build_conversion_engine", lambda *_: None)
    try:
        with TestClient(app) as client:
            yield client, db, tokens
    finally:
        db.close()
        engine.dispose()


def _headers(tokens, company):
    return {"Authorization": f"Bearer {tokens[company]}"}


def _post_value(client, tokens, company, period, amount):
    return client.post(
        "/api/v1/values",
        headers=_headers(tokens, company),
        json=dict(
            concept=_record().concept,
            entity="shared-plant",
            period=period,
            value=amount,
            unit="kg",
        ),
    )


def test_authenticated_two_tenant_value_paths(tenant_api):
    client, db, tokens = tenant_api
    ids = {company: set() for company in tokens}
    for company, amount in (("company-a", 11), ("company-b", 29)):
        for period in ("2026-01-31", "2026-02-28"):
            response = _post_value(client, tokens, company, period, amount)
            assert response.status_code == 201, response.text
            ids[company].add(response.json()["id"])
    # SQLite CURRENT_TIMESTAMP omits fractional seconds, unlike SQLAlchemy's
    # bound datetime comparisons. Use one explicit representation for cursor QA.
    for revision in db.query(ValueRevision).all():
        revision.created_at = datetime(2026, 3, 1, 12, 0, 0, 123456)
    db.commit()
    # Identical concept/entity/period coordinates still belong to distinct tenants.
    for company in tokens:
        own, foreign = ids[company], ids[next(c for c in tokens if c != company)]
        headers = _headers(tokens, company)
        rows = db.query(ValueRevision).filter_by(tenant_id=company).all()
        assert {row.source_record_id for row in rows} == own
        assert {row.context.tenant_id for row in rows} == {company}
        first = client.get("/api/v1/values?limit=1", headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()
        assert page["total"] == 2 and page["has_more"] is True
        second = client.get(
            "/api/v1/values",
            headers=headers,
            params=dict(limit=1, cursor=page["next_cursor"]),
        )
        assert second.status_code == 200, second.text
        assert second.json()["has_more"] is False
        assert {item["id"] for item in page["items"] + second.json()["items"]} == own
        for value_id in own | foreign:
            response = client.get(f"/api/v1/values/{value_id}", headers=headers)
            assert response.status_code == (200 if value_id in own else 404)
        changes = client.get("/api/v1/values/changes", headers=headers)
        assert changes.status_code == 200, changes.text
        assert {item["record"]["id"] for item in changes.json()["items"]} == own
        exported = client.get("/api/v1/values/export?format=json", headers=headers)
        assert exported.status_code == 200, exported.text
        assert {item["id"] for item in exported.json()} == own
        # Caller-controlled tenant query parameters cannot rebind compatibility reads.
        response = client.get("/api/v1/values?tenant_id=company-b", headers=headers)
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == own
    assert client.get("/api/v1/values").status_code == 403
    assert (
        client.get(
            "/api/v1/values", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )


def test_revision_offset_page_issues_usable_next_cursor(tenant_api):
    client, db, tokens = tenant_api
    for period in ("2026-01-31", "2026-02-28", "2026-03-31"):
        assert _post_value(client, tokens, "company-a", period, 7).status_code == 201
    for revision in db.query(ValueRevision).all():
        revision.created_at = datetime(2026, 4, 1, 12, 0, 0, 123456)
    db.commit()
    headers = _headers(tokens, "company-a")
    page = client.get(
        "/api/v1/values", headers=headers, params={"offset": 1, "limit": 1}
    )
    assert page.status_code == 200, page.text
    cursor = page.json()["next_cursor"]
    assert cursor
    next_page = client.get(
        "/api/v1/values", headers=headers, params={"cursor": cursor, "limit": 1}
    )
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["size"] == 1


def test_revision_change_feed_item_cursor_resumes_without_skipping_ties(tenant_api):
    client, db, tokens = tenant_api
    for period in ("2026-01-31", "2026-02-28"):
        assert _post_value(client, tokens, "company-a", period, 7).status_code == 201
    for revision in db.query(ValueRevision).all():
        revision.created_at = datetime(2026, 4, 1, 12, 0, 0, 123456)
        revision.source_record_id = "source-z"
    db.commit()
    headers = _headers(tokens, "company-a")
    first = client.get("/api/v1/values/changes", headers=headers, params={"limit": 1})
    assert first.status_code == 200, first.text
    item = first.json()["items"][0]
    revision_id = item["record"]["metadata"]["value_revision"]["revision_id"]
    assert decode_change_feed_cursor(item["cursor"]).event_id == revision_id
    next_page = client.get(
        "/api/v1/values/changes",
        headers=headers,
        params={"limit": 1, "cursor": item["cursor"]},
    )
    assert next_page.status_code == 200, next_page.text
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["items"][0]["event_id"] != item["event_id"]


def test_values_cursor_rejects_cross_tenant_filter_replay_and_tampering(tenant_api):
    client, db, tokens = tenant_api
    for company in tokens:
        for period in ("2026-01-31", "2026-02-28"):
            assert _post_value(client, tokens, company, period, 7).status_code == 201
    for revision in db.query(ValueRevision).all():
        revision.created_at = datetime(2026, 3, 1, 12, 0, 0, 123456)
    db.commit()

    headers_a = _headers(tokens, "company-a")
    first = client.get("/api/v1/values", headers=headers_a, params={"limit": 1})
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor
    continuation = client.get(
        "/api/v1/values", headers=headers_a, params={"limit": 2, "cursor": cursor}
    )
    assert continuation.status_code == 200
    assert continuation.json()["size"] == 1

    replay = client.get(
        "/api/v1/values",
        headers=_headers(tokens, "company-b"),
        params={"limit": 1, "cursor": cursor},
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "Invalid values cursor"
    for changed_filter in (
        {"concept": "urn:sds:reg:test:other"},
        {"entity": "different-plant"},
        {"unit": "MWh"},
        {"period_start": "2026-02-01"},
        {"period_end": "2026-01-31"},
        {"changed_since": "2026-03-01T00:00:00"},
    ):
        changed = client.get(
            "/api/v1/values",
            headers=headers_a,
            params={"limit": 1, "cursor": cursor, **changed_filter},
        )
        assert changed.status_code == 400, changed_filter
        assert changed.json()["detail"] == "Invalid values cursor"

    altered_cursor = cursor[:5] + ("A" if cursor[5] != "A" else "B") + cursor[6:]
    legacy_cursor = encode_value_cursor(
        period=date(2026, 2, 28),
        created_at=datetime(2026, 3, 1, 12, 0, 0, 123456),
        value_id=first.json()["items"][0]["id"],
    )
    for candidate in (altered_cursor, legacy_cursor):
        response = client.get(
            "/api/v1/values",
            headers=headers_a,
            params={"limit": 1, "cursor": candidate},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid values cursor"


@pytest.mark.parametrize("company_id", [None, "", "   ", "string"])
def test_authenticated_user_without_valid_company_cannot_inherit_default(
    tenant_api, company_id
):
    client, db, tokens = tenant_api
    # The signed token still carries company-a; DB-backed auth must reload the user.
    user = db.query(UserAccount).filter_by(id="company-a").one()
    user.company_id = company_id
    db.commit()
    response = client.get("/api/v1/values", headers=_headers(tokens, "company-a"))
    assert response.status_code == 403, response.text
    assert (
        response.json()["detail"] == "Access denied: user is not assigned to a tenant"
    )
    assert _post_value(client, tokens, "company-a", "2026-01-31", 1).status_code == 403
    assert db.query(ESGValue).count() == 0
    assert db.query(ValueRevision).count() == 0


@pytest.mark.parametrize("router", [values, calculations])
def test_authenticated_provider_binds_company_even_when_default_changes(
    monkeypatch, router
):
    monkeypatch.setattr(settings, "require_database", True)
    monkeypatch.setattr(settings, "value_revision_primary_read_path", "revision")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    store = router.get_authenticated_value_store(
        request=request,
        db=MagicMock(),
        current_user=SimpleNamespace(role=UserRole.ANALYST, company_id="company-a"),
    )
    monkeypatch.setattr(settings, "value_revision_default_tenant_id", "company-b")
    assert store._tenant_id() == "company-a"
