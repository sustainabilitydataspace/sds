"""Serial, destructive tests against an explicitly disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.api.routers import mapping_assertions
from src.database.init_db import init_db_for_engine
from src.database.repositories import (
    canonical_mapping_package_job_repository as job_repo_module,
)
from src.database.repositories.canonical_mapping_package_job_repository import (
    CanonicalMappingPackageJobRepository,
)
from src.services.canonical_mapping_db_import import (
    import_canonical_mapping_package_to_db,
)
from src.services.canonical_mapping_import_lock import CANONICAL_MAPPING_IMPORT_LOCK_KEY
from src.services.canonical_mapping_package_job_store import (
    DatabaseCanonicalMappingPackageJobStore,
)
from tests.test_canonical_mapping_db_import import _write_package


@pytest.fixture
def disposable_mapping_postgres():
    url = os.environ.get("SDS_MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SDS_MIGRATION_TEST_DATABASE_URL is not set")
    if os.environ.get("SDS_MIGRATION_TEST_ALLOW_RESET") != "true":
        pytest.skip("disposable PostgreSQL reset is not explicitly enabled")
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("canonical mapping recovery requires disposable PostgreSQL")
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("run destructive PostgreSQL tests serially")
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        init_db_for_engine(engine)
        yield engine
    finally:
        engine.dispose()


def test_pinned_import_session_uses_one_connection_and_rejects_contention(
    disposable_mapping_postgres,
):
    url = os.environ["SDS_MIGRATION_TEST_DATABASE_URL"]
    bounded = create_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 3},
    )
    rival = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with bounded.connect() as unrelated:
            assert unrelated.execute(text("SELECT 1")).scalar_one() == 1
            with job_repo_module.canonical_mapping_import_session(bounded) as db:
                repo = CanonicalMappingPackageJobRepository(db)
                repo.create(
                    job_id="single-connection",
                    job_type="import",
                    package_id="pkg",
                    status="pending",
                    submitted_by="test-operator",
                )
                assert repo.mark_running("single-connection") is not None
                assert repo.has_active_import()
                with pytest.raises(RuntimeError, match="busy"):
                    with job_repo_module.canonical_mapping_import_session(rival):
                        pass
            with job_repo_module.canonical_mapping_import_session(bounded) as db:
                assert (
                    CanonicalMappingPackageJobRepository(db).get("single-connection")
                    is not None
                )
    finally:
        bounded.dispose()
        rival.dispose()


def test_abandoned_running_import_is_reconciled_on_next_admission(
    disposable_mapping_postgres, monkeypatch, tmp_path
):
    with Session(disposable_mapping_postgres) as db:
        CanonicalMappingPackageJobRepository(db).create(
            job_id="abandoned",
            job_type="import",
            package_id="previous",
            status="running",
            submitted_by="previous-worker",
        )

    class Report:
        valid = True
        blocked = False
        blockers = []
        compatibility = {"status_counts": {}, "importable_assertion_group_count": 0}
        committed = False

        def as_dict(self):
            return {"committed": self.committed}

    monkeypatch.setattr(
        mapping_assertions,
        "import_canonical_mapping_package_to_db",
        lambda **_: Report(),
    )
    with job_repo_module.canonical_mapping_import_session(
        disposable_mapping_postgres
    ) as db:
        result = mapping_assertions._execute_mapping_import_job(
            "new",
            mapping_assertions.CanonicalMappingPackageImportRequest(
                installed_standard_releases=[]
            ),
            tmp_path,
            set(),
            False,
            SimpleNamespace(username="operator"),
            db,
            DatabaseCanonicalMappingPackageJobStore(db),
        )
    assert result.status == "completed"
    with Session(disposable_mapping_postgres) as db:
        old = CanonicalMappingPackageJobRepository(db).get("abandoned")
        assert old is not None
        assert old.status == "failed"
        assert "Interrupted" in old.error_message
        assert old.committed is not True


def test_router_import_releases_request_connection_and_rejects_second_submission(
    disposable_mapping_postgres,
    monkeypatch,
    tmp_path,
):
    url = os.environ["SDS_MIGRATION_TEST_DATABASE_URL"]
    bounded = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=1)
    rival = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=1)
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _: tmp_path
    )
    request = mapping_assertions.CanonicalMappingPackageImportRequest(
        installed_standard_releases=[],
    )
    user = SimpleNamespace(username="test-operator")
    calls = []

    class Report:
        valid = True
        blocked = False
        blockers = []
        compatibility = {"status_counts": {}, "importable_assertion_group_count": 0}
        committed = False

        def as_dict(self):
            return {"committed": self.committed}

    def import_stub(**kwargs):
        calls.append("first")
        with Session(rival) as other:
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    mapping_assertions.submit_mapping_package_import_job(
                        "second",
                        request,
                        db=other,
                        job_store=DatabaseCanonicalMappingPackageJobStore(other),
                        user=user,
                    )
                )
        assert exc.value.status_code == 409
        assert kwargs["db"].get_bind().dialect.name == "postgresql"
        return Report()

    monkeypatch.setattr(
        mapping_assertions, "import_canonical_mapping_package_to_db", import_stub
    )
    try:
        with bounded.connect() as unrelated:
            unrelated.execute(text("SELECT 1"))
            with Session(bounded) as request_session:
                request_session.execute(text("SELECT 1"))  # auth/preflight checkout
                result = asyncio.run(
                    mapping_assertions.submit_mapping_package_import_job(
                        "first",
                        request,
                        db=request_session,
                        job_store=DatabaseCanonicalMappingPackageJobStore(
                            request_session
                        ),
                        user=user,
                    )
                )
                assert result.status == "completed"
        assert calls == ["first"]
    finally:
        bounded.dispose()
        rival.dispose()


def test_router_import_reports_capacity_failure_without_creating_job(
    disposable_mapping_postgres,
    monkeypatch,
    tmp_path,
):
    url = os.environ["SDS_MIGRATION_TEST_DATABASE_URL"]
    bounded = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=1)
    verifier = create_engine(url)
    monkeypatch.setattr(
        mapping_assertions, "_resolve_allowed_package_id", lambda _: tmp_path
    )
    request = mapping_assertions.CanonicalMappingPackageImportRequest(
        installed_standard_releases=[]
    )
    try:
        with bounded.connect() as first, bounded.connect() as second:
            first.execute(text("SELECT 1"))
            second.execute(text("SELECT 1"))
            with Session(bounded) as request_session:
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(
                        mapping_assertions.submit_mapping_package_import_job(
                            "capacity",
                            request,
                            db=request_session,
                            job_store=DatabaseCanonicalMappingPackageJobStore(
                                request_session
                            ),
                            user=SimpleNamespace(username="test-operator"),
                        )
                    )
                assert exc.value.status_code == 503
        with Session(verifier) as session:
            assert not CanonicalMappingPackageJobRepository(session).has_active_import()
    finally:
        bounded.dispose()
        verifier.dispose()


def test_mapping_recovery_is_locked_exactly_scoped_and_idempotent(
    disposable_mapping_postgres,
):
    engine = disposable_mapping_postgres
    with Session(engine) as db:
        repo = CanonicalMappingPackageJobRepository(db)
        for job_id, job_type, status in (
            ("pending-import", "import", "pending"),
            ("running-import", "import", "running"),
            ("completed-import", "import", "completed"),
            ("pending-validation", "validation", "pending"),
        ):
            repo.create(
                job_id=job_id,
                job_type=job_type,
                package_id="test-package",
                status=status,
                submitted_by="test-operator",
                request_metadata={"original": job_id},
                result_body={"preserved": job_id},
            )

        with engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
            )
            with pytest.raises(RuntimeError, match="lock unavailable"):
                repo.reconcile_legacy_active_jobs()
            db.rollback()
            pending = repo.get("pending-import")
            assert pending is not None and pending.status == "pending"
            lock_connection.rollback()

        assert repo.reconcile_legacy_active_jobs() == 2
        assert repo.reconcile_legacy_active_jobs() == 0
        for job_id, expected in (
            ("pending-import", "failed"),
            ("running-import", "failed"),
            ("completed-import", "completed"),
            ("pending-validation", "pending"),
        ):
            job = repo.get(job_id)
            assert job is not None
            assert job.status == expected
            assert job.request_metadata == {"original": job_id}
            assert job.result_body == {"preserved": job_id}
            if expected == "failed":
                assert job.completed_at is not None
                assert job.error_message == (
                    "Interrupted canonical mapping import; retry requires new validation"
                )

        assert repo.mark_running("pending-import") is None
        assert (
            repo.complete(
                job_id="running-import",
                result_body={"incorrect": True},
                total_rows=1,
                accepted_rows=1,
                rejected_rows=0,
                committed=True,
            )
            is None
        )
        assert (
            repo.fail(job_id="completed-import", error_message="late failure") is None
        )
        for job_id, status in (
            ("pending-import", "failed"),
            ("running-import", "failed"),
            ("completed-import", "completed"),
        ):
            persisted = repo.get(job_id)
            assert persisted is not None and persisted.status == status


def test_lifecycle_lock_and_terminal_status_share_mapping_commit(
    disposable_mapping_postgres,
):
    engine = disposable_mapping_postgres
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mapping_effect_probe (id integer PRIMARY KEY)"))

    with Session(engine) as db:
        repo = CanonicalMappingPackageJobRepository(db)
        with repo.import_lifecycle_lock():
            repo.create(
                job_id="lifecycle-import",
                job_type="import",
                package_id="fixture",
                status="pending",
                submitted_by="fixture",
            )
            assert repo.mark_running("lifecycle-import") is not None
            with Session(engine) as recovery:
                with pytest.raises(RuntimeError, match="lock unavailable"):
                    CanonicalMappingPackageJobRepository(
                        recovery
                    ).reconcile_legacy_active_jobs()
                recovery.rollback()

            db.execute(text("INSERT INTO mapping_effect_probe (id) VALUES (1)"))
            completed = repo.complete(
                job_id="lifecycle-import",
                result_body={"proof": "combined"},
                total_rows=1,
                accepted_rows=1,
                rejected_rows=0,
                committed=True,
                commit=False,
            )
            assert completed is not None and completed.status == "completed"
            with Session(engine) as recovery:
                with pytest.raises(RuntimeError, match="lock unavailable"):
                    CanonicalMappingPackageJobRepository(
                        recovery
                    ).reconcile_legacy_active_jobs()
                recovery.rollback()
            db.commit()

        with Session(engine) as recovery:
            assert (
                CanonicalMappingPackageJobRepository(
                    recovery
                ).reconcile_legacy_active_jobs()
                == 0
            )
            assert (
                recovery.execute(
                    text("SELECT count(*) FROM mapping_effect_probe")
                ).scalar_one()
                == 1
            )
            assert repo.get("lifecycle-import").status == "completed"


def test_actual_import_rolls_back_when_completion_is_interrupted(
    disposable_mapping_postgres, tmp_path
):
    _write_package(tmp_path)
    engine = disposable_mapping_postgres
    with Session(engine) as db:
        repo = CanonicalMappingPackageJobRepository(db)
        with repo.import_lifecycle_lock():
            repo.create(
                job_id="interrupted-import",
                job_type="import",
                package_id="fixture",
                status="pending",
                submitted_by="fixture",
            )
            assert repo.mark_running("interrupted-import") is not None
            report = import_canonical_mapping_package_to_db(
                package_dir=tmp_path, db=db, created_by="fixture", commit=False
            )
            assert report.valid and not report.blocked
            with Session(engine) as observer:
                assert (
                    observer.execute(
                        text("SELECT count(*) FROM standard_releases")
                    ).scalar_one()
                    == 0
                )
                with pytest.raises(RuntimeError, match="lock unavailable"):
                    CanonicalMappingPackageJobRepository(
                        observer
                    ).reconcile_legacy_active_jobs()
                observer.rollback()
            db.rollback()

        with Session(engine) as recovery:
            assert (
                CanonicalMappingPackageJobRepository(
                    recovery
                ).reconcile_legacy_active_jobs()
                == 1
            )
            assert (
                recovery.execute(
                    text("SELECT count(*) FROM standard_releases")
                ).scalar_one()
                == 0
            )
            assert repo.get("interrupted-import").status == "failed"


def test_actual_import_and_completion_commit_together(
    disposable_mapping_postgres, tmp_path
):
    _write_package(tmp_path)
    engine = disposable_mapping_postgres
    with Session(engine) as db:
        repo = CanonicalMappingPackageJobRepository(db)
        with repo.import_lifecycle_lock():
            repo.create(
                job_id="committed-import",
                job_type="import",
                package_id="fixture",
                status="pending",
                submitted_by="fixture",
            )
            assert repo.mark_running("committed-import") is not None
            report = import_canonical_mapping_package_to_db(
                package_dir=tmp_path, db=db, created_by="fixture", commit=False
            )
            assert report.valid and not report.blocked and not report.committed
            report.committed = True
            assert (
                repo.complete(
                    job_id="committed-import",
                    result_body=report.as_dict(),
                    total_rows=1,
                    accepted_rows=1,
                    rejected_rows=0,
                    committed=True,
                    commit=False,
                )
                is not None
            )
            with Session(engine) as observer:
                assert (
                    observer.execute(
                        text("SELECT count(*) FROM standard_releases")
                    ).scalar_one()
                    == 0
                )
                persisted = CanonicalMappingPackageJobRepository(observer).get(
                    "committed-import"
                )
                assert persisted is not None and persisted.status == "running"
            db.commit()
            with Session(engine) as recovery:
                with pytest.raises(RuntimeError, match="lock unavailable"):
                    CanonicalMappingPackageJobRepository(
                        recovery
                    ).reconcile_legacy_active_jobs()
                recovery.rollback()

        with Session(engine) as observer:
            assert (
                CanonicalMappingPackageJobRepository(
                    observer
                ).reconcile_legacy_active_jobs()
                == 0
            )
            assert (
                observer.execute(
                    text("SELECT count(*) FROM standard_releases")
                ).scalar_one()
                == 1
            )
            persisted = CanonicalMappingPackageJobRepository(observer).get(
                "committed-import"
            )
            assert persisted is not None and persisted.status == "completed"
