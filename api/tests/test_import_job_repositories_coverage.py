from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from src.config.settings import settings
from src.database.models import (
    CanonicalMappingPackageJob,
    IndicatorImportJob,
    ValueImportJob,
)
from src.database.repositories.canonical_mapping_package_job_repository import (
    CanonicalMappingPackageJobRepository,
)
from src.database.repositories.indicator_import_job_repository import (
    IndicatorImportJobRepository,
)
from src.database.repositories.value_import_job_repository import (
    ValueImportJobRepository,
)
from src.services.canonical_mapping_package_job_store import (
    DatabaseCanonicalMappingPackageJobStore,
    InMemoryCanonicalMappingPackageJobStore,
    get_canonical_mapping_package_job_store,
)


def _query_returning(result):
    query = MagicMock()
    query.filter.return_value = query
    query.with_for_update.return_value = query
    query.populate_existing.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.first.return_value = result
    query.all.return_value = result if isinstance(result, list) else [result]
    return query


def test_value_import_job_repository_create_get_list_and_state_transitions() -> None:
    db = MagicMock()
    repo = ValueImportJobRepository(db)

    created = repo.create(
        job_id="job-1",
        source_format="csv",
        submitted_by="user-1",
        source_filename="values.csv",
        request_metadata={"delimiter": ","},
    )

    assert created.id == "job-1"
    assert created.status == "pending"
    assert created.request_metadata == {"delimiter": ","}
    db.add.assert_called_once_with(created)
    db.commit.assert_called()
    db.refresh.assert_called_with(created)

    existing = ValueImportJob(
        id="job-2", source_format="json", status="pending", submitted_by="user-1"
    )
    db.query.return_value = _query_returning(existing)
    assert repo.get("job-2") is existing
    assert repo.list_recent_for_user("user-1", limit=5) == [existing]

    repo.get = MagicMock(return_value=existing)
    assert repo.mark_running("job-2") is existing
    assert existing.status == "running"
    assert existing.started_at is not None
    assert repo.mark_running("job-2") is None

    completed = repo.complete(
        job_id="job-2",
        result_body={"ok": True},
        total_rows=2,
        accepted_rows=2,
        rejected_rows=0,
        committed=True,
    )
    assert completed is existing
    assert existing.status == "completed"
    assert existing.error_message is None
    assert existing.completed_at is not None
    assert repo.mark_running("job-2") is None
    assert (
        repo.fail(
            job_id="job-2",
            error_message="late failure",
            result_body={"ok": False},
            total_rows=2,
            accepted_rows=1,
            rejected_rows=1,
            committed=False,
        )
        is None
    )

    failed_target = ValueImportJob(
        id="job-3", source_format="json", status="running", submitted_by="user-1"
    )
    db.query.return_value = _query_returning(failed_target)
    failed = repo.fail(
        job_id="job-3",
        error_message="bad csv",
        result_body={"ok": False},
        total_rows=2,
        accepted_rows=1,
        rejected_rows=1,
        committed=False,
    )
    assert failed is failed_target
    assert failed_target.status == "failed"
    assert failed_target.error_message == "bad csv"
    assert (
        repo.complete(
            job_id="job-3",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )

    repo.get = MagicMock(return_value=None)
    db.query.return_value = _query_returning(None)
    assert repo.mark_running("missing") is None
    assert (
        repo.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert repo.fail(job_id="missing", error_message="missing") is None


def test_value_import_job_repository_lists_active_for_recovery_read_only() -> None:
    pending = ValueImportJob(
        id="job-a", source_format="json", status="pending", submitted_by="user-1"
    )
    running = ValueImportJob(
        id="job-b", source_format="csv", status="running", submitted_by="user-1"
    )
    db = MagicMock()
    query = _query_returning([pending, running])
    db.query.return_value = query
    repo = ValueImportJobRepository(db)

    result = repo.list_active_for_recovery(limit=25)

    assert result == [pending, running]
    db.query.assert_called_once_with(ValueImportJob)
    assert query.filter.call_count == 1
    status_filter = str(
        query.filter.call_args.args[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert "value_import_jobs.status IN" in status_filter
    assert "'pending'" in status_filter
    assert "'running'" in status_filter
    assert query.order_by.call_count == 1
    order_by_args = [str(arg) for arg in query.order_by.call_args.args]
    assert order_by_args == [
        "value_import_jobs.created_at ASC",
        "value_import_jobs.id ASC",
    ]
    query.limit.assert_called_once_with(25)
    query.all.assert_called_once_with()
    query.with_for_update.assert_not_called()
    db.commit.assert_not_called()
    db.refresh.assert_not_called()
    db.add.assert_not_called()
    db.execute.assert_not_called()


def test_indicator_import_job_repository_create_and_query() -> None:
    db = MagicMock()
    repo = IndicatorImportJobRepository(db)

    created = repo.create(
        job_id="indicator-job-1",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="user-1",
        source_filename="register.csv",
        source_sha256="abc",
        source_size_bytes=123,
        source_payload="id,name\n1,Water",
        validation_job_id="validation-1",
        request_metadata={"strict": True},
        status="completed",
        result_body={"accepted": 1},
        total_rows=1,
        accepted_rows=1,
        rejected_rows=0,
        committed=False,
    )

    assert created.status == "completed"
    assert created.completed_at is not None
    assert created.request_metadata == {"strict": True}
    assert created.ownership_state == "resolved"
    db.add.assert_called_once_with(created)
    db.refresh.assert_called_with(created)

    existing = IndicatorImportJob(
        id="indicator-job-2",
        tenant_id="tenant-a",
        owner_user_id="user-1",
        ownership_state="resolved",
        job_type="import",
        source_format="csv",
        status="pending",
        submitted_by="admin",
    )
    db.query.return_value = _query_returning(existing)
    assert repo.get("indicator-job-2", tenant_id="tenant-a") is existing
    assert repo.has_active_import() is True

    db.query.return_value = _query_returning(None)
    assert repo.has_active_import() is False


def test_indicator_import_repository_uses_namespaced_postgres_lock() -> None:
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    repo = IndicatorImportJobRepository(db)

    repo.acquire_import_submission_lock()

    statement = str(db.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in statement
    assert db.execute.call_args.kwargs == {}
    assert "key" in db.execute.call_args.args[1]


def test_indicator_import_repository_skips_advisory_lock_outside_postgres() -> None:
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "sqlite"
    repo = IndicatorImportJobRepository(db)

    repo.acquire_import_submission_lock()

    db.execute.assert_not_called()


def test_indicator_import_repository_recovers_stale_active_imports() -> None:
    now = datetime.now(timezone.utc)
    stale_pending = IndicatorImportJob(
        id="stale-pending",
        job_type="import",
        source_format="csv",
        status="pending",
        submitted_by="admin",
        source_payload="identifier,title\n",
        created_at=now - timedelta(hours=25),
        updated_at=now - timedelta(hours=25),
    )
    stale_running = IndicatorImportJob(
        id="stale-running",
        job_type="import",
        source_format="csv",
        status="running",
        submitted_by="admin",
        source_payload="identifier,title\n",
        created_at=now - timedelta(hours=26),
        started_at=now - timedelta(hours=25),
        updated_at=now - timedelta(hours=25),
    )
    fresh_running = IndicatorImportJob(
        id="fresh-running",
        job_type="import",
        source_format="csv",
        status="running",
        submitted_by="admin",
        source_payload="identifier,title\n",
        created_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
    )
    db = MagicMock()
    db.query.return_value = _query_returning(
        [stale_pending, stale_running, fresh_running]
    )
    repo = IndicatorImportJobRepository(db)

    recovered = repo.fail_stale_active_imports(
        max_age_minutes=24 * 60,
        now=now,
    )

    assert recovered == 2
    assert stale_pending.status == "failed"
    assert stale_running.status == "failed"
    assert fresh_running.status == "running"
    assert stale_pending.source_payload == "identifier,title\n"
    assert stale_running.source_payload == "identifier,title\n"
    assert "active timeout" in stale_pending.error_message
    db.commit.assert_called_once()


def test_indicator_import_repository_stale_recovery_can_defer_commit() -> None:
    now = datetime.now(timezone.utc)
    stale = IndicatorImportJob(
        id="stale",
        job_type="import",
        source_format="csv",
        status="pending",
        submitted_by="admin",
        created_at=now - timedelta(hours=25),
        updated_at=now - timedelta(hours=25),
    )
    db = MagicMock()
    db.query.return_value = _query_returning([stale])
    repo = IndicatorImportJobRepository(db)

    recovered = repo.fail_stale_active_imports(
        max_age_minutes=24 * 60,
        now=now,
        commit=False,
    )

    assert recovered == 1
    db.flush.assert_called_once()
    db.commit.assert_not_called()


def test_indicator_import_repository_payload_retention() -> None:
    now = datetime.now(timezone.utc)
    validation = IndicatorImportJob(
        id="validation-expired",
        tenant_id="tenant-a",
        owner_user_id="user-1",
        ownership_state="resolved",
        job_type="validation",
        source_format="csv",
        status="completed",
        submitted_by="admin",
        source_payload="identifier,title\n",
        completed_at=now - timedelta(hours=25),
        created_at=now - timedelta(hours=25),
        updated_at=now - timedelta(hours=25),
    )
    db = MagicMock()
    repo = IndicatorImportJobRepository(db)
    repo.get = MagicMock(return_value=validation)

    payload = repo.get_source_payload(
        "validation-expired",
        tenant_id="tenant-a",
        validation_payload_retention_hours=24,
        now=now,
    )

    assert payload is None
    assert validation.source_payload is None
    db.commit.assert_called_once()


def test_canonical_mapping_package_job_repository_create_get_and_active_import():
    db = MagicMock()
    repo = CanonicalMappingPackageJobRepository(db)

    created = repo.create(
        job_id="mapping-job-1",
        job_type="import",
        package_id="pkg",
        status="completed",
        submitted_by="admin",
        request_metadata={"mode": "partial"},
        result_body={"ok": True},
        total_rows=3,
        accepted_rows=2,
        rejected_rows=1,
        committed=True,
        validation_job_id="validation-1",
    )

    assert created.id == "mapping-job-1"
    assert created.started_at is not None
    assert created.completed_at is not None
    db.add.assert_called_once_with(created)
    db.commit.assert_called()
    db.refresh.assert_called_with(created)

    existing = CanonicalMappingPackageJob(
        id="mapping-job-2",
        job_type="import",
        package_id="pkg",
        status="pending",
        submitted_by="admin",
    )
    db.query.return_value = _query_returning(existing)
    assert repo.get("mapping-job-2") is existing
    assert repo.has_active_import() is True

    db.query.return_value = _query_returning(None)
    assert repo.has_active_import() is False


def test_canonical_mapping_package_job_repository_lock_and_state_transitions():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    repo = CanonicalMappingPackageJobRepository(db)

    repo.acquire_import_submission_lock()

    statement = str(db.execute.call_args.args[0])
    assert "pg_advisory_xact_lock" in statement
    assert "key" in db.execute.call_args.args[1]

    db.execute.reset_mock()
    db.get_bind.return_value.dialect.name = "sqlite"
    repo.acquire_import_submission_lock()
    db.execute.assert_not_called()

    existing = CanonicalMappingPackageJob(
        id="mapping-job-3",
        job_type="import",
        package_id="pkg",
        status="pending",
        submitted_by="admin",
    )
    repo.get = MagicMock(return_value=existing)

    def apply_mock_update(values, *, synchronize_session):
        assert synchronize_session is False
        for name, value in values.items():
            setattr(existing, name, value)
        return 1

    db.query.return_value.filter.return_value.update.side_effect = apply_mock_update

    running = repo.mark_running("mapping-job-3")
    assert running is existing
    assert existing.status == "running"
    assert existing.started_at is not None
    assert existing.updated_at is not None

    completed = repo.complete(
        job_id="mapping-job-3",
        result_body={"accepted": 10},
        total_rows=12,
        accepted_rows=10,
        rejected_rows=2,
        committed=True,
        error_message=None,
    )
    assert completed is existing
    assert existing.status == "completed"
    assert existing.result_body == {"accepted": 10}
    assert existing.completed_at is not None

    existing.status = "pending"
    failed = repo.fail(
        job_id="mapping-job-3",
        error_message="invalid canonical package",
        result_body={"accepted": 1},
        total_rows=3,
        accepted_rows=1,
        rejected_rows=2,
        committed=False,
    )
    assert failed is existing
    assert existing.status == "failed"
    assert existing.error_message == "invalid canonical package"

    repo.get = MagicMock(return_value=None)
    assert repo.mark_running("missing") is None
    assert (
        repo.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert repo.fail(job_id="missing", error_message="missing") is None


def test_canonical_mapping_recovery_fails_only_active_imports_under_shared_lock():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.side_effect = [
        SimpleNamespace(scalar_one=lambda: True),
        SimpleNamespace(scalar_one=lambda: True),
        SimpleNamespace(rowcount=2),
    ]
    repo = CanonicalMappingPackageJobRepository(db)
    assert repo.reconcile_legacy_active_jobs() == 2
    lifecycle_call, lock_call, update_call = db.execute.call_args_list
    assert "pg_try_advisory_xact_lock" in str(lock_call.args[0])
    from src.services.canonical_mapping_import_lock import (
        CANONICAL_MAPPING_IMPORT_LOCK_KEY,
        CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY,
    )

    assert lifecycle_call.args[1] == {"key": CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY}
    assert lock_call.args[1] == {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY}
    compiled = update_call.args[0].compile(dialect=postgresql.dialect())
    where = str(compiled).split(" WHERE ", 1)[1]
    assert "canonical_mapping_package_jobs.job_type = " in where
    assert "canonical_mapping_package_jobs.status IN " in where
    assert "import" in compiled.params.values()
    assert ["pending", "running"] in compiled.params.values()
    assert "failed" in compiled.params.values()
    assert "result_body" not in str(compiled).split(" WHERE ", 1)[0]
    db.commit.assert_called_once_with()


def test_canonical_mapping_recovery_refuses_startup_when_lock_unavailable():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.return_value.scalar_one.return_value = False
    repo = CanonicalMappingPackageJobRepository(db)
    with pytest.raises(RuntimeError, match="reconciliation lock unavailable"):
        repo.reconcile_legacy_active_jobs()
    db.commit.assert_not_called()
    assert db.execute.call_count == 1


def test_canonical_mapping_lifecycle_guard_rolls_back_or_invalidates_connection():
    db = MagicMock()
    bind = db.get_bind.return_value
    bind.dialect.name = "postgresql"
    guard = bind.connect.return_value.__enter__.return_value
    repo = CanonicalMappingPackageJobRepository(db)

    with repo.import_lifecycle_lock():
        guard.execute.assert_called_once()
        assert guard.rollback.call_count == 0
    guard.rollback.assert_called_once_with()

    guard.reset_mock()
    guard.rollback.side_effect = RuntimeError("connection rollback failed")
    with pytest.raises(RuntimeError, match="connection rollback failed"):
        with repo.import_lifecycle_lock():
            pass
    guard.invalidate.assert_called_once_with()

    bind.dialect.name = "sqlite"
    bind.connect.reset_mock()
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        with repo.import_lifecycle_lock():
            pass
    bind.connect.assert_not_called()


def test_canonical_mapping_recovery_rejects_import_lock_even_with_lifecycle_lock():
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.side_effect = [
        SimpleNamespace(scalar_one=lambda: True),
        SimpleNamespace(scalar_one=lambda: False),
    ]
    with pytest.raises(RuntimeError, match="reconciliation lock unavailable"):
        CanonicalMappingPackageJobRepository(db).reconcile_legacy_active_jobs()
    db.commit.assert_not_called()


def test_canonical_mapping_terminal_cas_does_not_commit_on_status_conflict():
    db = MagicMock()
    repo = CanonicalMappingPackageJobRepository(db)
    repo.get = MagicMock(return_value=SimpleNamespace(status="running"))
    db.query.return_value.filter.return_value.update.return_value = 0
    assert repo.mark_running("raced-job") is None
    assert (
        repo.complete(
            job_id="raced-job",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
            commit=False,
        )
        is None
    )
    db.commit.assert_not_called()
    db.flush.assert_not_called()

    db.query.return_value.filter.return_value.update.return_value = 1
    assert (
        repo.complete(
            job_id="raced-job",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
            commit=False,
        )
        is not None
    )
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()


def test_canonical_mapping_package_job_stores_and_dependency(monkeypatch):
    in_memory = InMemoryCanonicalMappingPackageJobStore()
    pending = in_memory.create(
        job_id="job-1",
        job_type="import",
        package_id="pkg",
        submitted_by="admin",
    )
    completed = in_memory.create(
        job_id="job-2",
        job_type="validation",
        package_id="pkg",
        submitted_by="admin",
        status="completed",
        result_body={"valid": True},
    )

    assert pending.status == "pending"
    assert completed.completed_at is not None
    assert in_memory.get("job-1").id == "job-1"
    assert in_memory.get("missing") is None
    assert in_memory.has_active_import() is True
    assert in_memory.mark_running("missing") is None
    assert (
        in_memory.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert in_memory.fail(job_id="missing", error_message="missing") is None

    running = in_memory.mark_running("job-1")
    assert running.status == "running"
    assert running.started_at is not None
    finished = in_memory.complete(
        job_id="job-1",
        result_body={"accepted": 1},
        total_rows=1,
        accepted_rows=1,
        rejected_rows=0,
        committed=True,
    )
    assert finished.status == "completed"
    assert finished.result_body == {"accepted": 1}
    failed = in_memory.fail(
        job_id="job-2",
        error_message="validation failed",
        result_body={"accepted": 0},
        total_rows=1,
        accepted_rows=0,
        rejected_rows=1,
        committed=False,
    )
    assert failed.status == "failed"
    assert failed.error_message == "validation failed"

    class _Repo:
        def __init__(self, _db):
            pass

        def create(self, **_kwargs):
            now = datetime.now(timezone.utc)
            return CanonicalMappingPackageJob(
                id="db-job",
                job_type="validation",
                package_id="pkg",
                status="failed",
                submitted_by="admin",
                request_metadata=None,
                result_body={"valid": False},
                created_at=now,
                updated_at=now,
            )

        def get(self, job_id):
            if job_id == "db-job":
                return self.create()
            return None

        def has_active_import(self):
            return False

        def acquire_import_submission_lock(self):
            self.locked = True

        def mark_running(self, job_id):
            if job_id != "db-job":
                return None
            job = self.create()
            job.status = "running"
            return job

        def complete(self, **kwargs):
            if kwargs["job_id"] != "db-job":
                return None
            job = self.create()
            job.status = "completed"
            job.result_body = kwargs["result_body"]
            return job

        def fail(self, **kwargs):
            if kwargs["job_id"] != "db-job":
                return None
            job = self.create()
            job.status = "failed"
            job.error_message = kwargs["error_message"]
            return job

    monkeypatch.setattr(
        "src.services.canonical_mapping_package_job_store.CanonicalMappingPackageJobRepository",
        _Repo,
    )
    db_store = DatabaseCanonicalMappingPackageJobStore(object())
    assert db_store.create().id == "db-job"
    assert db_store.get("db-job").status == "failed"
    assert db_store.get("missing") is None
    assert db_store.has_active_import() is False
    assert db_store.acquire_import_submission_lock() is None
    assert db_store.mark_running("db-job").status == "running"
    assert db_store.mark_running("missing") is None
    assert (
        db_store.complete(
            job_id="db-job",
            result_body={"accepted": 1},
            total_rows=1,
            accepted_rows=1,
            rejected_rows=0,
            committed=True,
        ).status
        == "completed"
    )
    assert (
        db_store.fail(job_id="db-job", error_message="db failed").error_message
        == "db failed"
    )
    assert db_store.complete(job_id="missing", result_body={}) is None
    assert db_store.fail(job_id="missing", error_message="missing") is None

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(settings, "require_database", False)
    first = get_canonical_mapping_package_job_store(request, db=None)
    second = get_canonical_mapping_package_job_store(request, db=None)
    assert first is second

    monkeypatch.setattr(settings, "require_database", True)
    with pytest.raises(HTTPException, match="Database session unavailable"):
        get_canonical_mapping_package_job_store(request, db=None)
    assert isinstance(
        get_canonical_mapping_package_job_store(request, db=object()),
        DatabaseCanonicalMappingPackageJobStore,
    )
