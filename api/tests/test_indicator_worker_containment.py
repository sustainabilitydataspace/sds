"""Stale/duplicate runner containment, not durable H15 recovery qualification."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from src.database.models import IndicatorImportJob
from src.database.repositories.indicator_import_job_repository import (
    IndicatorImportJobRepository,
)
from src.services import indicator_import_job_runner as runner
from src.services.indicator_import import IndicatorImportValidationError
from src.services.indicator_import_job_store import (
    DatabaseIndicatorImportJobStore,
    InMemoryIndicatorImportJobStore,
)

PAYLOAD = "identifier,title\nsynthetic,Retained evidence\n"
TENANT = "synthetic-tenant"
RESULT = dict(
    result_body={"accepted": 1},
    total_rows=1,
    accepted_rows=1,
    rejected_rows=0,
    committed=True,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def engine(tmp_path):
    # Independent connections exercise SQL CAS. This is not live PostgreSQL QA.
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    IndicatorImportJob.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(params=["memory", "sqlite"])
def store(request, engine):
    with Session(engine) as db:
        yield (
            InMemoryIndicatorImportJobStore()
            if request.param == "memory"
            else DatabaseIndicatorImportJobStore(db)
        )


def _create(store, status="pending"):
    store.create(
        job_id="job",
        job_type="import",
        source_format="csv",
        submitted_by="synthetic-admin",
        tenant_id=TENANT,
        owner_user_id="synthetic-admin-id",
        source_payload=PAYLOAD,
        status=status,
    )


def _run(app=None):
    runner.run_indicator_import_job(
        app=app or FastAPI(),
        job_id="job",
        csv_text=PAYLOAD,
        source_ref=None,
        source_hash=None,
        submitted_by="synthetic-admin",
    )


def test_quarantined_indicator_jobs_are_never_runtime_active(engine):
    with Session(engine) as db:
        db.add(
            IndicatorImportJob(
                id="legacy-job",
                tenant_id=None,
                owner_user_id=None,
                ownership_state="quarantined",
                job_type="import",
                source_format="csv",
                status="pending",
                submitted_by="legacy-user",
            )
        )
        db.commit()

        repo = IndicatorImportJobRepository(db)
        assert repo.has_active_import() is False
        assert repo.get("legacy-job", tenant_id=TENANT) is None
        assert repo.mark_running("legacy-job") is None


@pytest.mark.parametrize("status", ["running", "failed", "completed", "missing"])
@pytest.mark.parametrize("database_mode", [True, False])
def test_non_pending_runner_never_applies_or_terminalizes(
    store, monkeypatch, status, database_mode
):
    if status != "missing":
        _create(store, status)
    before = store.get("job", tenant_id=TENANT)
    payload = store.get_source_payload("job", tenant_id=TENANT)
    apply = MagicMock()
    fail = MagicMock(wraps=store.fail)
    complete = MagicMock(wraps=store.complete)
    monkeypatch.setattr(store, "fail", fail)
    monkeypatch.setattr(store, "complete", complete)
    monkeypatch.setattr(runner.settings, "require_database", database_mode)
    monkeypatch.setattr(runner, "SessionLocal", MagicMock)
    monkeypatch.setattr(runner, "DatabaseIndicatorImportJobStore", lambda _db: store)
    monkeypatch.setattr(runner, "apply_indicator_import_transactional", apply)
    app = FastAPI()
    app.state.indicator_import_job_store = store

    _run(app)
    _run(app)

    apply.assert_not_called()
    fail.assert_not_called()
    complete.assert_not_called()
    assert store.get("job", tenant_id=TENANT) == before
    assert store.get_source_payload("job", tenant_id=TENANT) == payload


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_terminal_evidence_cannot_be_overwritten(store, terminal):
    _create(store)
    assert store.mark_running("job") is not None
    if terminal == "completed":
        store.complete(job_id="job", **RESULT)
    else:
        store.fail(job_id="job", error_message="original failure", committed=False)
    before = store.get("job", tenant_id=TENANT)
    assert store.mark_running("job") is None
    assert store.complete(job_id="job", **RESULT) is None
    assert store.fail(job_id="job", error_message="late failure") is None
    assert store.get("job", tenant_id=TENANT) == before
    assert store.get_source_payload("job", tenant_id=TENANT) == PAYLOAD
    assert "source_payload" not in before.model_dump()


def test_pending_requires_claim_before_completion(store):
    _create(store)
    before = store.get("job", tenant_id=TENANT)
    assert store.complete(job_id="job", **RESULT) is None
    assert store.get("job", tenant_id=TENANT) == before
    assert store.mark_running("job").status.value == "running"
    assert store.mark_running("job") is None
    assert store.complete(job_id="job", **RESULT).status.value == "completed"
    assert store.get_source_payload("job", tenant_id=TENANT) == PAYLOAD


def test_memory_pending_runner_preserves_existing_database_requirement(monkeypatch):
    store = InMemoryIndicatorImportJobStore()
    _create(store)
    app = FastAPI()
    app.state.indicator_import_job_store = store
    apply = MagicMock()
    monkeypatch.setattr(runner.settings, "require_database", False)
    monkeypatch.setattr(runner, "apply_indicator_import_transactional", apply)
    _run(app)
    job = store.get("job", tenant_id=TENANT)
    assert job.status.value == "failed"
    assert job.error_message == "Indicator imports require database-backed mode"
    assert job.started_at is not None
    assert store.get_source_payload("job", tenant_id=TENANT) == PAYLOAD
    apply.assert_not_called()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_concurrent_and_repeated_runner_only_one_applies(engine, monkeypatch, backend):
    memory = InMemoryIndicatorImportJobStore()
    with Session(engine) as db:
        _create(memory if backend == "memory" else DatabaseIndicatorImportJobStore(db))
    start = Barrier(2)
    applying = Event()
    release = Event()
    finished = Event()
    sessions = []

    def session_factory():
        db = Session(engine) if backend == "sqlite" else MagicMock()
        sessions.append(db)
        return db

    def apply(**_kwargs):
        applying.set()
        assert release.wait(10), "test did not release the winning worker"
        return SimpleNamespace(**RESULT, to_result_body=lambda: RESULT["result_body"])

    apply_mock = MagicMock(side_effect=apply)
    monkeypatch.setattr(runner.settings, "require_database", True)
    monkeypatch.setattr(runner, "SessionLocal", session_factory)
    if backend == "memory":
        monkeypatch.setattr(
            runner, "DatabaseIndicatorImportJobStore", lambda _db: memory
        )
    monkeypatch.setattr(runner, "apply_indicator_import_transactional", apply_mock)

    def run():
        start.wait(timeout=10)
        try:
            _run()
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        try:
            assert applying.wait(10)
            # The loser must exit while the winning worker is still applying.
            assert finished.wait(10)
            assert apply_mock.call_count == 1
        finally:
            release.set()
        for future in futures:
            future.result(timeout=10)
    _run()
    assert apply_mock.call_count == 1
    with Session(engine) as db:
        store = memory if backend == "memory" else DatabaseIndicatorImportJobStore(db)
        assert store.get("job", tenant_id=TENANT).status.value == "completed"
        assert store.get_source_payload("job", tenant_id=TENANT) == PAYLOAD
    if backend == "memory":
        assert all(db.close.call_count == 1 for db in sessions)


@pytest.mark.parametrize(
    "failure", [RuntimeError("claim failed"), IndicatorImportValidationError(None)]
)
def test_claim_exception_cannot_apply_or_fail_another_workers_job(monkeypatch, failure):
    db = MagicMock()
    store = MagicMock()
    store.mark_running.side_effect = failure
    apply = MagicMock()
    monkeypatch.setattr(runner.settings, "require_database", True)
    monkeypatch.setattr(runner, "SessionLocal", lambda: db)
    monkeypatch.setattr(runner, "DatabaseIndicatorImportJobStore", lambda _db: store)
    monkeypatch.setattr(runner, "apply_indicator_import_transactional", apply)
    _run()
    apply.assert_not_called()
    store.fail.assert_not_called()
    store.complete.assert_not_called()
    db.rollback.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize("status", ["failed", "completed", "unknown"])
def test_sql_claim_ignores_stale_identity_map_and_unknown_status(engine, status):
    with Session(engine) as db:
        _create(DatabaseIndicatorImportJobStore(db))
    with Session(engine, expire_on_commit=False) as stale:
        repo = IndicatorImportJobRepository(stale)
        cached = repo.get("job", tenant_id=TENANT)
        stale.commit()
        with Session(engine) as current:
            current.query(IndicatorImportJob).filter_by(id="job").update(
                {"status": status, "source_payload": PAYLOAD}
            )
            current.commit()
        assert cached.status == "pending"
        assert repo.mark_running("job") is None
        assert repo.complete(job_id="job", **RESULT) is None
        assert repo.fail(job_id="job", error_message="late failure") is None
        assert repo.get("job", tenant_id=TENANT).status == status
        assert repo.get("job", tenant_id=TENANT).source_payload == PAYLOAD


def test_claim_is_one_conditional_sql_update(engine):
    with Session(engine) as db:
        _create(DatabaseIndicatorImportJobStore(db))
    statements = []

    def capture(_conn, _cursor, statement, params, _context, _many):
        statements.append((statement, params))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as db:
            assert IndicatorImportJobRepository(db).mark_running("job") is not None
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    statement, params = statements[0]
    assert statement.startswith("UPDATE indicator_import_jobs SET ")
    assert "indicator_import_jobs.id =" in statement
    assert "indicator_import_jobs.status IN" in statement
    assert "indicator_import_jobs.ownership_state =" in statement
    assert "job" in params
    assert "pending" in params
    assert "resolved" in params
    assert sum(sql.startswith("UPDATE") for sql, _ in statements) == 1


def test_timeout_helper_reloads_terminal_state_and_preserves_payload(engine):
    with Session(engine) as db:
        _create(DatabaseIndicatorImportJobStore(db))
        job = db.get(IndicatorImportJob, "job")
        job.created_at = datetime.now() - timedelta(days=2)
        db.commit()
    with Session(engine, expire_on_commit=False) as stale:
        repo = IndicatorImportJobRepository(stale)
        cached = repo.get("job", tenant_id=TENANT)
        stale.commit()
        with Session(engine) as current:
            other = IndicatorImportJobRepository(current)
            other.mark_running("job")
            other.complete(job_id="job", **RESULT)
        assert cached.status == "pending"
        assert repo.fail_stale_active_imports(max_age_minutes=1) == 0
        stale.refresh(cached)
        assert cached.status == "completed"
        assert cached.source_payload == PAYLOAD


def test_claim_commit_failure_rolls_back_without_apply_or_terminalization(
    engine, monkeypatch
):
    with Session(engine) as db:
        _create(DatabaseIndicatorImportJobStore(db))
    worker_db = Session(engine)
    monkeypatch.setattr(
        worker_db, "commit", MagicMock(side_effect=RuntimeError("claim commit failed"))
    )
    apply = MagicMock()
    monkeypatch.setattr(runner.settings, "require_database", True)
    monkeypatch.setattr(runner, "SessionLocal", lambda: worker_db)
    monkeypatch.setattr(runner, "apply_indicator_import_transactional", apply)
    _run()
    apply.assert_not_called()
    with Session(engine) as db:
        job = db.get(IndicatorImportJob, "job")
        assert job.status == "pending"
        assert job.started_at is None
        assert job.completed_at is None
        assert job.error_message is None
        assert job.source_payload == PAYLOAD
