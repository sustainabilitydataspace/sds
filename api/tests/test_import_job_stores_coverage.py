from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.models import (
    IndicatorImportJobStatus,
    IndicatorImportJobType,
    ValueImportJobStatus,
)
from src.database.repositories.indicator_import_job_repository import (
    IndicatorImportJobRepository,
)
from src.services.indicator_import_job_store import (
    DatabaseIndicatorImportJobStore,
    InMemoryIndicatorImportJobStore,
    get_indicator_import_job_store,
)
from src.services.value_import_job_store import (
    DatabaseValueImportJobStore,
    InMemoryValueImportJobStore,
    get_value_import_job_store,
)


def _state():
    return SimpleNamespace()


def test_in_memory_value_import_job_store_lifecycle_and_misses() -> None:
    store = InMemoryValueImportJobStore()

    created = store.create(
        job_id="value-job-1",
        source_format="json",
        submitted_by="user-1",
        source_filename="values.json",
        request_metadata={"mode": "strict"},
    )
    assert created.status == ValueImportJobStatus.PENDING
    assert store.get("value-job-1").id == "value-job-1"
    assert store.get("missing") is None

    running = store.mark_running("value-job-1")
    assert running.status == ValueImportJobStatus.RUNNING
    assert running.started_at is not None
    assert store.mark_running("missing") is None

    completed = store.complete(
        job_id="value-job-1",
        result_body={"accepted": 2},
        total_rows=2,
        accepted_rows=2,
        rejected_rows=0,
        committed=True,
    )
    assert completed.status == ValueImportJobStatus.COMPLETED
    assert completed.error_message is None
    assert completed.completed_at is not None
    assert store.mark_running("value-job-1") is None
    assert store.fail(job_id="value-job-1", error_message="late failure") is None
    assert (
        store.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )

    store.create(
        job_id="value-job-fail",
        source_format="json",
        submitted_by="user-1",
    )
    assert store.mark_running("value-job-fail").status == ValueImportJobStatus.RUNNING
    failed = store.fail(
        job_id="value-job-fail",
        error_message="bad file",
        result_body={"accepted": 1},
        total_rows=2,
        accepted_rows=1,
        rejected_rows=1,
        committed=False,
    )
    assert failed.status == ValueImportJobStatus.FAILED
    assert failed.error_message == "bad file"
    assert (
        store.complete(
            job_id="value-job-fail",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert store.fail(job_id="missing", error_message="missing") is None


def test_in_memory_indicator_import_job_store_lifecycle_active_and_payload() -> None:
    store = InMemoryIndicatorImportJobStore()

    validation = store.create(
        job_id="validation-1",
        job_type="validation",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="admin-id",
        source_payload="identifier\n",
        status="completed",
        result_body={"valid": True},
        total_rows=1,
        accepted_rows=1,
        rejected_rows=0,
        committed=False,
    )
    assert validation.status == IndicatorImportJobStatus.COMPLETED
    assert (
        store.get_source_payload("validation-1", tenant_id="tenant-a") == "identifier\n"
    )
    assert store.get_source_payload("missing", tenant_id="tenant-a") is None
    assert store.has_active_import() is False

    created = store.create(
        job_id="import-1",
        job_type="import",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="admin-id",
    )
    assert created.job_type == IndicatorImportJobType.IMPORT
    assert store.has_active_import() is True

    running = store.mark_running("import-1")
    assert running.status == IndicatorImportJobStatus.RUNNING
    assert running.started_at is not None
    assert store.mark_running("missing") is None

    completed = store.complete(
        job_id="import-1",
        result_body={"accepted": 1},
        total_rows=1,
        accepted_rows=1,
        rejected_rows=0,
        committed=True,
    )
    assert completed.status == IndicatorImportJobStatus.COMPLETED
    assert store.has_active_import() is False
    assert store.get_source_payload("import-1", tenant_id="tenant-a") is None
    assert (
        store.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )

    assert store.fail(job_id="import-1", error_message="late failure") is None
    store.create(
        job_id="import-2",
        job_type="import",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="admin-id",
    )
    failed = store.fail(
        job_id="import-2",
        error_message="invalid",
        result_body={"accepted": 0},
        total_rows=1,
        accepted_rows=0,
        rejected_rows=1,
        committed=False,
    )
    assert failed.status == IndicatorImportJobStatus.FAILED
    assert failed.error_message == "invalid"
    assert store.fail(job_id="missing", error_message="missing") is None


def test_indicator_job_reads_are_tenant_scoped() -> None:
    store = InMemoryIndicatorImportJobStore()
    store.create(
        job_id="tenant-a-job",
        job_type="validation",
        source_format="csv",
        submitted_by="manager-a",
        tenant_id="tenant-a",
        owner_user_id="user-a",
        status="completed",
    )

    assert store.get("tenant-a-job", tenant_id="tenant-a") is not None
    assert store.get("tenant-a-job", tenant_id="tenant-b") is None
    assert store.get_source_payload("tenant-a-job", tenant_id="tenant-b") is None


def _db_value_job(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": "value-job-db",
        "source_format": "json",
        "status": "pending",
        "submitted_by": "user-1",
        "source_filename": "values.json",
        "request_metadata": {"strict": True},
        "total_rows": None,
        "accepted_rows": None,
        "rejected_rows": None,
        "committed": None,
        "result_body": None,
        "error_message": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _db_indicator_job(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": "indicator-job-db",
        "job_type": "import",
        "source_format": "csv",
        "status": "pending",
        "submitted_by": "admin",
        "source_filename": "register.csv",
        "source_sha256": "sha",
        "source_size_bytes": 10,
        "source_payload": "identifier\n",
        "validation_job_id": None,
        "request_metadata": {"strict": True},
        "total_rows": None,
        "accepted_rows": None,
        "rejected_rows": None,
        "committed": None,
        "result_body": None,
        "error_message": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_database_value_import_job_store_delegates_to_repository(monkeypatch) -> None:
    repo = MagicMock()
    job = _db_value_job()
    active_jobs = [
        _db_value_job(id="value-job-a", status="pending"),
        _db_value_job(id="value-job-b", status="running", request_metadata=None),
    ]
    repo.create.return_value = job
    repo.get.side_effect = [job, None]
    repo.mark_running.side_effect = [_db_value_job(status="running"), None]
    repo.complete.side_effect = [job, None]
    repo.fail.side_effect = [job, None]
    repo.list_active_for_recovery.return_value = active_jobs
    monkeypatch.setattr(
        "src.services.value_import_job_store.ValueImportJobRepository",
        lambda _db: repo,
    )

    store = DatabaseValueImportJobStore(MagicMock())

    assert (
        store.create(
            job_id="value-job-db",
            source_format="json",
            submitted_by="user-1",
            source_filename="values.json",
            request_metadata={"strict": True},
        ).id
        == "value-job-db"
    )
    assert store.get("value-job-db").id == "value-job-db"
    assert store.get("missing") is None
    assert store.mark_running("value-job-db").status == ValueImportJobStatus.RUNNING
    assert store.mark_running("missing") is None
    assert (
        store.complete(
            job_id="value-job-db",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        ).id
        == "value-job-db"
    )
    assert (
        store.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert store.fail(job_id="value-job-db", error_message="bad").id == "value-job-db"
    assert store.fail(job_id="missing", error_message="bad") is None
    active = store.list_active_for_recovery(limit=2)
    assert [job.id for job in active] == ["value-job-a", "value-job-b"]
    assert [job.status for job in active] == [
        ValueImportJobStatus.PENDING,
        ValueImportJobStatus.RUNNING,
    ]
    assert active[1].request_metadata == {}
    repo.list_active_for_recovery.assert_called_once_with(2)


def test_database_indicator_import_job_store_delegates_to_repository(
    monkeypatch,
) -> None:
    repo = MagicMock()
    job = _db_indicator_job()
    repo.create.return_value = job
    repo.get.side_effect = [job, None]
    repo.get_source_payload.return_value = "identifier\n"
    repo.has_active_import.return_value = True
    repo.mark_running.side_effect = [job, None]
    repo.complete.side_effect = [job, None]
    repo.fail.side_effect = [job, None]
    monkeypatch.setattr(
        "src.services.indicator_import_job_store.IndicatorImportJobRepository",
        lambda _db: repo,
    )

    store = DatabaseIndicatorImportJobStore(MagicMock())

    assert (
        store.create(
            job_id="indicator-job-db",
            job_type="import",
            source_format="csv",
            submitted_by="admin",
            tenant_id="tenant-a",
            owner_user_id="admin-id",
        ).id
        == "indicator-job-db"
    )
    assert store.get("indicator-job-db", tenant_id="tenant-a").id == "indicator-job-db"
    assert (
        store.get_source_payload("indicator-job-db", tenant_id="tenant-a")
        == "identifier\n"
    )
    assert store.get("missing", tenant_id="tenant-a") is None
    assert store.has_active_import() is True
    repo.fail_stale_active_imports.assert_called()
    assert (
        store.mark_running("indicator-job-db").job_type == IndicatorImportJobType.IMPORT
    )
    assert store.mark_running("missing") is None
    assert (
        store.complete(
            job_id="indicator-job-db",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        ).id
        == "indicator-job-db"
    )
    assert (
        store.complete(
            job_id="missing",
            result_body={},
            total_rows=0,
            accepted_rows=0,
            rejected_rows=0,
            committed=False,
        )
        is None
    )
    assert (
        store.fail(job_id="indicator-job-db", error_message="bad").id
        == "indicator-job-db"
    )
    assert store.fail(job_id="missing", error_message="bad") is None


def test_database_indicator_validation_never_persists_source_payload(monkeypatch):
    repo = MagicMock()
    repo.create.return_value = _db_indicator_job(job_type="validation")
    monkeypatch.setattr(
        "src.services.indicator_import_job_store.IndicatorImportJobRepository",
        lambda _db: repo,
    )
    store = DatabaseIndicatorImportJobStore(MagicMock())

    store.create(
        job_id="validation-db",
        job_type="validation",
        source_format="csv",
        submitted_by="manager-a",
        tenant_id="tenant-a",
        owner_user_id="user-a",
        source_payload="sensitive,csv\n",
    )

    assert repo.create.call_args.kwargs["source_payload"] is None


def test_indicator_job_repository_drops_validation_payload_defensively():
    db = MagicMock()
    repository = IndicatorImportJobRepository(db)

    repository.create(
        job_id="validation-db",
        job_type="validation",
        source_format="csv",
        submitted_by="manager-a",
        tenant_id="tenant-a",
        owner_user_id="user-a",
        source_payload="sensitive,csv\n",
    )

    persisted = db.add.call_args.args[0]
    assert persisted.source_payload is None


def test_database_indicator_import_job_store_lock_and_stale_recovery(monkeypatch):
    repo = MagicMock()
    repo.has_active_import.return_value = False
    monkeypatch.setattr(
        "src.services.indicator_import_job_store.IndicatorImportJobRepository",
        lambda _db: repo,
    )

    store = DatabaseIndicatorImportJobStore(MagicMock())
    store.acquire_import_submission_lock()
    recovered = store.recover_stale_active_imports(commit=False)
    active = store.has_active_import(recover_stale=False)

    repo.acquire_import_submission_lock.assert_called_once()
    repo.fail_stale_active_imports.assert_called_once()
    assert repo.fail_stale_active_imports.call_args.kwargs["commit"] is False
    assert recovered is repo.fail_stale_active_imports.return_value
    assert active is False


def test_import_job_store_dependencies_select_expected_backend(monkeypatch) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=_state()))

    monkeypatch.setattr(
        "src.services.value_import_job_store.settings.require_database", False
    )
    assert get_value_import_job_store(
        request=request, db=None
    ) is get_value_import_job_store(request=request, db=None)

    monkeypatch.setattr(
        "src.services.indicator_import_job_store.settings.require_database", False
    )
    assert get_indicator_import_job_store(
        request=request, db=None
    ) is get_indicator_import_job_store(request=request, db=None)

    monkeypatch.setattr(
        "src.services.value_import_job_store.settings.require_database", True
    )
    with pytest.raises(HTTPException) as value_unavailable:
        get_value_import_job_store(request=request, db=None)
    assert value_unavailable.value.status_code == 503
    assert isinstance(
        get_value_import_job_store(request=request, db=MagicMock()),
        DatabaseValueImportJobStore,
    )

    monkeypatch.setattr(
        "src.services.indicator_import_job_store.settings.require_database", True
    )
    with pytest.raises(HTTPException) as indicator_unavailable:
        get_indicator_import_job_store(request=request, db=None)
    assert indicator_unavailable.value.status_code == 503
    assert isinstance(
        get_indicator_import_job_store(request=request, db=MagicMock()),
        DatabaseIndicatorImportJobStore,
    )
