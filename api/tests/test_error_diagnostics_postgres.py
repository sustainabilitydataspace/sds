"""Admin error diagnostics on an explicitly disposable PostgreSQL.

Run with SDS_MIGRATION_TEST_DATABASE_URL and SDS_MIGRATION_TEST_ALLOW_RESET=true.
"""

from __future__ import annotations

import json
import os
import threading
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.models import AdminErrorDiagnostic
from src.services import error_diagnostics

SECRET = "SECRET-91c2"


@pytest.fixture
def maker(monkeypatch):
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("requires disposable PostgreSQL")
    engine = create_engine(url, pool_size=12)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine, tables=[AdminErrorDiagnostic.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(error_diagnostics, "session_factory", factory)
    yield factory
    engine.dispose()


def _request(request_id):
    request = MagicMock()
    request.state.request_id = request_id
    request.method = "POST"
    request.scope = {"route": MagicMock(path="/api/v1/values")}
    return request


def _record(request_id, exc=None):
    return error_diagnostics.build_record(
        _request(request_id), exc or RuntimeError(SECRET), 500
    )


def _count(maker):
    with maker() as session:
        return session.query(AdminErrorDiagnostic).count()


def test_store_ignores_duplicates_and_reads_back(maker):
    assert error_diagnostics.store_record(_record("req-1")) is True
    assert error_diagnostics.store_record(_record("req-1")) is True
    assert _count(maker) == 1
    with maker() as session:
        record = error_diagnostics.get_error_diagnostic(session, "req-1")
        assert record["classification"] == "unknown"
        assert record["route_template"] == "/api/v1/values"
        assert error_diagnostics.get_error_diagnostic(session, "absent") is None
        assert [
            r["request_id"]
            for r in error_diagnostics.list_error_diagnostics(session, limit=20)
        ] == ["req-1"]


def _real_error(maker, sql):
    with maker() as session:
        try:
            session.execute(text(sql), {"value": SECRET})
        except DBAPIError as exc:
            session.rollback()
            return exc
    raise AssertionError("statement did not fail")


def test_real_postgres_errors_keep_only_allow_listed_fields(maker):
    with maker() as session:
        session.execute(text("CREATE TABLE probe (id text PRIMARY KEY)"))
        session.execute(
            text(
                "CREATE FUNCTION raise_static() RETURNS void AS $$ BEGIN "
                "RAISE EXCEPTION 'value source identity is immutable'; END $$ "
                "LANGUAGE plpgsql"
            )
        )
        session.execute(
            text(
                "CREATE FUNCTION raise_dynamic(v text) RETURNS void AS $$ BEGIN "
                "RAISE EXCEPTION 'bad value %', v; END $$ LANGUAGE plpgsql"
            )
        )
        session.execute(text("INSERT INTO probe VALUES ('taken')"))
        session.commit()

    unique = _real_error(maker, "INSERT INTO probe VALUES ('taken'), (:value)")
    assert isinstance(unique, IntegrityError)
    record = _record("req-unique", unique)
    assert record["classification"] == "db_unique_violation"
    assert record["db"]["constraint_name"] == "probe_pkey"
    assert record["db"]["table_name"] == "probe"

    static = _record("req-static", _real_error(maker, "SELECT raise_static()"))
    assert static["classification"] == "db_trigger_exception"
    assert static["db"]["trigger_message"] == "value source identity is immutable"

    dynamic = _record("req-dynamic", _real_error(maker, "SELECT raise_dynamic(:value)"))
    assert dynamic["classification"] == "db_trigger_exception"
    assert dynamic["db"]["trigger_message"] is None

    for record in (record, static, dynamic):
        assert error_diagnostics.store_record(record) is True
    with maker() as session:
        stored = session.execute(
            text("SELECT row_to_json(d)::text FROM admin_error_diagnostics d")
        ).scalars()
        dumped = json.dumps(list(stored))
    assert SECRET not in dumped
    assert "INSERT" not in dumped


def test_retention_cap_holds_under_concurrent_captures(maker, monkeypatch):
    monkeypatch.setattr(error_diagnostics, "RETENTION_ROWS", 20)
    observed = []
    errors = []

    def worker(index):
        try:
            for n in range(10):
                assert error_diagnostics.store_record(_record(f"req-{index}-{n}"))
                observed.append(_count(maker))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors
    assert max(observed) <= 20
    with maker() as session:
        rows = session.execute(
            text(
                "SELECT id FROM admin_error_diagnostics "
                "ORDER BY created_at DESC, id DESC"
            )
        ).scalars()
        kept = list(rows)
    assert len(kept) == 20


def test_expired_rows_are_never_served_without_a_capture(maker, monkeypatch):
    for request_id in ("old-1", "old-2", "new-1"):
        error_diagnostics.store_record(_record(request_id))
    with maker() as session:
        session.execute(
            text(
                "UPDATE admin_error_diagnostics SET created_at = "
                "now() - interval '15 days' WHERE request_id LIKE 'old-%'"
            )
        )
        session.commit()

    # Even when the read-side purge fails, the filter hides expired rows.
    monkeypatch.setattr(
        error_diagnostics,
        "purge_error_diagnostics",
        MagicMock(side_effect=RuntimeError("purge down")),
    )
    with maker() as session:
        assert error_diagnostics.get_error_diagnostic(session, "old-1") is None
    assert _count(maker) == 3
    monkeypatch.undo()
    monkeypatch.setattr(error_diagnostics, "session_factory", maker)

    with maker() as session:
        assert error_diagnostics.get_error_diagnostic(session, "old-1") is None
        listed = error_diagnostics.list_error_diagnostics(session, limit=50)
    assert [r["request_id"] for r in listed] == ["new-1"]
    assert _count(maker) == 1
