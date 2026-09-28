"""Indicator CAS on explicitly disposable PostgreSQL; run this module serially.

Both SDS_MIGRATION_TEST_DATABASE_URL and exact SDS_MIGRATION_TEST_ALLOW_RESET=true
are required before engine creation. Resets public and migrates to head. This
qualifies state containment only, without running imports or enabling recovery.
"""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic.script import ScriptDirectory
from src.database.init_db import init_db_for_engine
from src.database.migrations import _alembic_config
from src.database.models import IndicatorImportJob, UserAccount
from src.services.indicator_import_job_store import DatabaseIndicatorImportJobStore

PAYLOAD = "identifier,title\nsynthetic,Retained H15 evidence\n"
TENANT = "synthetic-tenant"
RESULT = dict(
    result_body={"accepted": 1},
    total_rows=1,
    accepted_rows=1,
    rejected_rows=0,
    committed=True,
)


@pytest.fixture
def disposable_postgres():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("H15 indicator CAS requires disposable PostgreSQL")
    if os.environ.get("PYTEST_XDIST_WORKER") is not None:
        pytest.skip("run disposable PostgreSQL schema resets serially, without xdist")
    engine = create_engine(
        url,
        isolation_level="READ COMMITTED",
        connect_args={
            "connect_timeout": 3,
            "options": "-c lock_timeout=5000 -c statement_timeout=10000",
        },
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        init_db_for_engine(engine)
        with engine.connect() as connection:
            head = ScriptDirectory.from_config(
                _alembic_config(connection)
            ).get_current_head()
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == head
            )
        with Session(engine) as db:
            db.add(
                UserAccount(
                    id="synthetic-admin-id",
                    username="synthetic-admin",
                    email="synthetic-admin@example.invalid",
                    company_id=TENANT,
                    role="admin",
                    is_active=True,
                    password_hash="not-a-login-hash",
                )
            )
            db.flush()
            # Direct fixture rows retain historical terminal payloads, avoiding
            # the existing creation-time payload cleanup policy in store.create.
            for status in ("pending", "completed", "failed"):
                terminal = status != "pending"
                stamp = datetime(2026, 9, 1, 12)
                db.add(
                    IndicatorImportJob(
                        id=status,
                        tenant_id=TENANT,
                        owner_user_id="synthetic-admin-id",
                        ownership_state="resolved",
                        job_type="import",
                        source_format="csv",
                        status=status,
                        submitted_by="synthetic-admin",
                        source_filename="synthetic.csv",
                        source_sha256=hashlib.sha256(PAYLOAD.encode()).hexdigest(),
                        source_size_bytes=len(PAYLOAD.encode()),
                        source_payload=PAYLOAD,
                        request_metadata={"fixture": "h15-indicator-cas"},
                        created_at=stamp,
                        started_at=stamp if terminal else None,
                        completed_at=stamp if terminal else None,
                        updated_at=stamp,
                        result_body={"original": status} if terminal else None,
                        total_rows=3 if terminal else None,
                        accepted_rows=2 if terminal else None,
                        rejected_rows=1 if terminal else None,
                        committed=(status == "completed") if terminal else None,
                        error_message=(
                            "original failure" if status == "failed" else None
                        ),
                    )
                )
            db.commit()
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url,reset,worker",
    [
        (None, None, None),
        (None, "true", None),
        ("", "true", None),
        ("postgresql://unused.invalid/disposable", None, None),
        ("postgresql://unused.invalid/disposable", "false", None),
        ("postgresql://unused.invalid/disposable", "TRUE", None),
        ("postgresql://unused.invalid/disposable", "true ", None),
        ("sqlite://", "true", None),
        ("postgresql://unused.invalid/disposable", "true", "gw0"),
    ],
)
def test_disposable_gate_precedes_engine_creation(monkeypatch, url, reset, worker):
    for name, value in (
        ("SDS_MIGRATION_TEST_DATABASE_URL", url),
        ("SDS_MIGRATION_TEST_ALLOW_RESET", reset),
        ("PYTEST_XDIST_WORKER", worker),
    ):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)

    def forbidden(*_args, **_kwargs):
        pytest.fail("closed gate created an engine or ran migrations")

    monkeypatch.setitem(globals(), "create_engine", forbidden)
    monkeypatch.setitem(globals(), "init_db_for_engine", forbidden)
    with pytest.raises(pytest.skip.Exception):
        next(disposable_postgres.__wrapped__())


def _snapshot(engine):
    with engine.connect() as connection:
        return {
            row["id"]: dict(row)
            for row in connection.execute(
                select(IndicatorImportJob.__table__)
            ).mappings()
        }


def _terminalize(store, job_id, terminal):
    if terminal == "completed":
        return store.complete(job_id=job_id, **RESULT)
    return store.fail(
        job_id=job_id,
        error_message="synthetic failure",
        **{**RESULT, "committed": False},
    )


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_postgres_concurrent_claim_and_terminal_containment(
    disposable_postgres, terminal
):
    engine = disposable_postgres
    before = _snapshot(engine)
    with Session(engine) as db:
        assert (
            DatabaseIndicatorImportJobStore(db).complete(job_id="pending", **RESULT)
            is None
        )
    assert _snapshot(engine) == before
    first_updated, release_first, second_connected = Event(), Event(), Event()
    pids = {}

    def claim(name):
        # Pin two physical connections across the store's internal commits.
        with engine.connect() as connection, Session(bind=connection) as db:
            pids[name] = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
            store = DatabaseIndicatorImportJobStore(db)
            for job_id in ("completed", "failed", "missing"):
                assert store.mark_running(job_id) is None
            if name == "first":

                def pause_before_commit(_db):
                    first_updated.set()
                    assert release_first.wait(10), "first claimant was not released"

                event.listen(db, "before_commit", pause_before_commit, once=True)
            else:
                second_connected.set()
            return store.mark_running("pending")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim, "first")
        try:
            assert first_updated.wait(5), "first claimant did not reach commit"
            second = pool.submit(claim, "second")
            assert second_connected.wait(5), "second claimant did not connect"
            assert pids["first"] != pids["second"]
            # Require a real overlapping UPDATE / row lock wait, not merely
            # concurrent Python scheduling that might execute claims serially.
            deadline = time.monotonic() + 3
            blocked = False
            with engine.connect() as connection:
                while time.monotonic() < deadline:
                    blockers = connection.execute(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["second"]}
                    ).scalar_one()
                    if pids["first"] in blockers:
                        blocked = True
                        break
                    time.sleep(0.02)
            assert blocked, "second claimant did not wait on the first UPDATE"
            assert not second.done()
        finally:
            release_first.set()
        claims = [first.result(timeout=10), second.result(timeout=10)]
    assert sum(claim is not None for claim in claims) == 1
    assert claims[0].id == "pending"
    assert claims[0].status.value == "running"
    assert claims[1] is None
    running = _snapshot(engine)
    assert running["pending"]["source_payload"] == PAYLOAD
    assert running["pending"]["started_at"] is not None
    for job_id in ("completed", "failed"):
        assert running[job_id] == before[job_id]

    # Retain a genuinely stale running ORM object while another connection
    # terminalizes; neither late terminal operation may overwrite any evidence.
    with (
        Session(engine, expire_on_commit=False) as stale_complete,
        Session(engine, expire_on_commit=False) as stale_fail,
    ):
        stale_jobs = []
        for db in (stale_complete, stale_fail):
            stale_jobs.append(db.get(IndicatorImportJob, "pending"))
            db.commit()
        with Session(engine) as current:
            store = DatabaseIndicatorImportJobStore(current)
            assert store.mark_running("pending") is None
            result = _terminalize(store, "pending", terminal)
            assert result.status.value == terminal
            assert result.result_body == RESULT["result_body"]
            assert result.total_rows == 1
            assert result.accepted_rows == 1
            assert result.rejected_rows == 0
            assert result.committed is (terminal == "completed")
            assert result.error_message == (
                None if terminal == "completed" else "synthetic failure"
            )
            assert result.completed_at is not None
            assert store.get_source_payload("pending", tenant_id=TENANT) == PAYLOAD
        after = _snapshot(engine)
        for db, cached, operation in zip(
            (stale_complete, stale_fail), stale_jobs, ("completed", "failed")
        ):
            assert cached.status == "running"
            assert (
                _terminalize(DatabaseIndicatorImportJobStore(db), "pending", operation)
                is None
            )
            assert _snapshot(engine) == after
        assert after["pending"]["source_payload"] == PAYLOAD

    for operation in ("completed", "failed"):
        with Session(engine) as db:
            store = DatabaseIndicatorImportJobStore(db)
            for job_id in ("pending", "completed", "failed", "missing"):
                assert _terminalize(store, job_id, operation) is None
                assert store.mark_running(job_id) is None
    assert _snapshot(engine) == after


def test_postgres_pending_failure_preserves_existing_contract(disposable_postgres):
    # fail() deliberately permits pending as well as running. Keep this
    # pre-existing contract explicit; this qualification does not tighten it.
    engine = disposable_postgres
    before = _snapshot(engine)
    with Session(engine) as db:
        result = _terminalize(DatabaseIndicatorImportJobStore(db), "pending", "failed")
        assert result.status.value == "failed"
        assert result.started_at is None
        assert result.completed_at is not None
    after = _snapshot(engine)
    assert after["pending"]["source_payload"] == PAYLOAD
    for job_id in ("completed", "failed"):
        assert after[job_id] == before[job_id]
