"""H15 reconciliation on explicitly disposable PostgreSQL. Run serially.

Both SDS_MIGRATION_TEST_DATABASE_URL and SDS_MIGRATION_TEST_ALLOW_RESET=true
are required before engine creation. This resets public and runs real migrations.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.database.init_db import init_db_for_engine
from src.services.value_import_job_store import DatabaseValueImportJobStore
from tests.value_job_reconciliation_support import (
    assert_reconciled,
    seed_evidence,
    snapshot,
)


@pytest.fixture
def disposable_postgres():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("set SDS_MIGRATION_TEST_ALLOW_RESET=true for disposable DB reset")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("H15 reconciliation requires disposable PostgreSQL")
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
        with Session(engine) as db:
            seed_evidence(db)
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url,reset",
    [
        (None, None),
        (None, "true"),
        ("postgresql://unused.invalid/disposable", None),
        ("postgresql://unused.invalid/disposable", "false"),
        ("postgresql://unused.invalid/disposable", "TRUE"),
        ("sqlite://", "true"),
    ],
)
def test_disposable_gate_precedes_engine_creation(monkeypatch, url, reset):
    for name, value in (
        ("SDS_MIGRATION_TEST_DATABASE_URL", url),
        ("SDS_MIGRATION_TEST_ALLOW_RESET", reset),
    ):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    create = lambda *_args, **_kwargs: pytest.fail("closed gate created an engine")
    monkeypatch.setitem(globals(), "create_engine", create)
    with pytest.raises(pytest.skip.Exception):
        next(disposable_postgres.__wrapped__())


def test_postgres_reconciliation_changes_only_active_value_jobs(disposable_postgres):
    engine = disposable_postgres
    before = snapshot(engine)
    # All non-job tables are compared, including migration-only revision,
    # catalog, mapping, snapshot, payload and registry tables.
    for name in (
        "indicator_import_jobs",
        "esg_values",
        "value_revisions",
        "indicators",
        "standard_mappings",
        "dataset_snapshots",
    ):
        assert before[name], f"missing nonempty sentinel: {name}"
    with Session(engine) as db:
        assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 6
    after = snapshot(engine)
    assert_reconciled(before, after)
    with Session(engine) as db:
        assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 0
    assert snapshot(engine) == after


@pytest.mark.parametrize("first_rolls_back", [False, True])
def test_postgres_concurrent_reconciliation_waits_and_rechecks(
    disposable_postgres, first_rolls_back
):
    engine = disposable_postgres
    before = snapshot(engine)
    first_updated = threading.Event()
    release_first = threading.Event()
    second_connected = threading.Event()
    pids = {}

    def first():
        with Session(engine) as db:
            pids["first"] = db.execute(text("SELECT pg_backend_pid()")).scalar_one()

            def pause_before_commit(_db):
                first_updated.set()
                assert release_first.wait(10), "test did not release first writer"
                if first_rolls_back:
                    raise RuntimeError("synthetic commit failure")

            event.listen(db, "before_commit", pause_before_commit)
            return DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs()

    def second():
        with Session(engine) as db:
            pids["second"] = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
            second_connected.set()
            return DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first)
        try:
            assert first_updated.wait(5)
            second_future = executor.submit(second)
            assert second_connected.wait(5)
            # Observe a real PostgreSQL lock wait, not just two sequential calls
            # or scheduler timing. SKIP LOCKED would fail this assertion.
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
            assert blocked, "second reconciliation did not wait on first writer"
            assert not second_future.done()
        finally:
            release_first.set()
        if first_rolls_back:
            with pytest.raises(RuntimeError, match="synthetic commit failure"):
                first_future.result(timeout=10)
            assert second_future.result(timeout=10) == 6
        else:
            assert first_future.result(timeout=10) == 6
            assert second_future.result(timeout=10) == 0
    after = snapshot(engine)
    assert_reconciled(before, after)
    with Session(engine) as db:
        assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 0
    assert snapshot(engine) == after
