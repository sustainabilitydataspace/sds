"""Persistence backends for indicator import validation and apply jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.models import (
    IndicatorImportJobResponse,
    IndicatorImportJobStatus,
    IndicatorImportJobType,
)
from src.config.settings import settings
from src.database.repositories.indicator_import_job_repository import (
    IndicatorImportJobRepository,
)
from src.database.session import get_db_optional


@dataclass(frozen=True)
class _StoredIndicatorImportJob:
    id: str
    job_type: IndicatorImportJobType
    source_format: str
    status: IndicatorImportJobStatus
    submitted_by: str
    source_filename: Optional[str]
    source_sha256: Optional[str]
    source_size_bytes: Optional[int]
    source_payload: Optional[str]
    validation_job_id: Optional[str]
    request_metadata: dict
    total_rows: Optional[int]
    accepted_rows: Optional[int]
    rejected_rows: Optional[int]
    committed: Optional[bool]
    result_body: Optional[dict]
    error_message: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    updated_at: datetime


class InMemoryIndicatorImportJobStore:
    """Offline-safe job store used by tests and non-strict validation flows."""

    def __init__(self):
        self._jobs: Dict[str, _StoredIndicatorImportJob] = {}

    def create(
        self,
        *,
        job_id: str,
        job_type: str,
        source_format: str,
        submitted_by: str,
        source_filename: Optional[str] = None,
        source_sha256: Optional[str] = None,
        source_size_bytes: Optional[int] = None,
        source_payload: Optional[str] = None,
        validation_job_id: Optional[str] = None,
        request_metadata: Optional[dict] = None,
        status: str = "pending",
        result_body: Optional[dict] = None,
        total_rows: Optional[int] = None,
        accepted_rows: Optional[int] = None,
        rejected_rows: Optional[int] = None,
        committed: Optional[bool] = None,
        error_message: Optional[str] = None,
    ) -> IndicatorImportJobResponse:
        now = datetime.now(timezone.utc)
        stored = _StoredIndicatorImportJob(
            id=job_id,
            job_type=IndicatorImportJobType(job_type),
            source_format=source_format,
            status=IndicatorImportJobStatus(status),
            submitted_by=submitted_by,
            source_filename=source_filename,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            source_payload=source_payload,
            validation_job_id=validation_job_id,
            request_metadata=request_metadata or {},
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
            result_body=result_body,
            error_message=error_message,
            created_at=now,
            started_at=None,
            completed_at=now if status in {"completed", "failed"} else None,
            updated_at=now,
        )
        self._jobs[job_id] = stored
        return self._to_response(stored)

    def get(self, job_id: str) -> Optional[IndicatorImportJobResponse]:
        stored = self._jobs.get(job_id)
        return self._to_response(stored) if stored else None

    def get_source_payload(self, job_id: str) -> Optional[str]:
        stored = self._jobs.get(job_id)
        return stored.source_payload if stored else None

    def acquire_import_submission_lock(self) -> None:
        return None

    def recover_stale_active_imports(self, *, commit: bool = True) -> int:
        return 0

    def has_active_import(
        self, *, recover_stale: bool = True, commit_recovery: bool = True
    ) -> bool:
        return any(
            job.job_type == IndicatorImportJobType.IMPORT
            and job.status
            in {IndicatorImportJobStatus.PENDING, IndicatorImportJobStatus.RUNNING}
            for job in self._jobs.values()
        )

    def mark_running(self, job_id: str) -> Optional[IndicatorImportJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredIndicatorImportJob(
            **{
                **stored.__dict__,
                "status": IndicatorImportJobStatus.RUNNING,
                "started_at": now,
                "updated_at": now,
            }
        )
        self._jobs[job_id] = updated
        return self._to_response(updated)

    def complete(
        self,
        *,
        job_id: str,
        result_body: dict,
        total_rows: int,
        accepted_rows: int,
        rejected_rows: int,
        committed: bool,
    ) -> Optional[IndicatorImportJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredIndicatorImportJob(
            **{
                **stored.__dict__,
                "status": IndicatorImportJobStatus.COMPLETED,
                "result_body": result_body,
                "total_rows": total_rows,
                "accepted_rows": accepted_rows,
                "rejected_rows": rejected_rows,
                "committed": committed,
                "error_message": None,
                "source_payload": (
                    None
                    if stored.job_type == IndicatorImportJobType.IMPORT
                    else stored.source_payload
                ),
                "completed_at": now,
                "updated_at": now,
            }
        )
        self._jobs[job_id] = updated
        return self._to_response(updated)

    def fail(
        self,
        *,
        job_id: str,
        error_message: str,
        result_body: Optional[dict] = None,
        total_rows: Optional[int] = None,
        accepted_rows: Optional[int] = None,
        rejected_rows: Optional[int] = None,
        committed: Optional[bool] = None,
    ) -> Optional[IndicatorImportJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredIndicatorImportJob(
            **{
                **stored.__dict__,
                "status": IndicatorImportJobStatus.FAILED,
                "result_body": result_body,
                "total_rows": total_rows,
                "accepted_rows": accepted_rows,
                "rejected_rows": rejected_rows,
                "committed": committed,
                "error_message": error_message,
                "source_payload": (
                    None
                    if stored.job_type == IndicatorImportJobType.IMPORT
                    else stored.source_payload
                ),
                "completed_at": now,
                "updated_at": now,
            }
        )
        self._jobs[job_id] = updated
        return self._to_response(updated)

    @staticmethod
    def _to_response(stored: _StoredIndicatorImportJob) -> IndicatorImportJobResponse:
        payload = stored.__dict__.copy()
        payload.pop("source_payload", None)
        return IndicatorImportJobResponse(**payload)


class DatabaseIndicatorImportJobStore:
    """PostgreSQL-backed indicator import job store."""

    def __init__(self, db: Session):
        self._repo = IndicatorImportJobRepository(db)

    def create(self, **kwargs) -> IndicatorImportJobResponse:
        return self._to_response(self._repo.create(**kwargs))

    def get(self, job_id: str) -> Optional[IndicatorImportJobResponse]:
        job = self._repo.get(job_id)
        return self._to_response(job) if job else None

    def get_source_payload(self, job_id: str) -> Optional[str]:
        return self._repo.get_source_payload(
            job_id,
            validation_payload_retention_hours=(
                settings.indicator_import_validation_payload_retention_hours
            ),
        )

    def acquire_import_submission_lock(self) -> None:
        self._repo.acquire_import_submission_lock()

    def recover_stale_active_imports(self, *, commit: bool = True) -> int:
        return self._repo.fail_stale_active_imports(
            max_age_minutes=settings.indicator_import_active_timeout_minutes,
            commit=commit,
        )

    def has_active_import(
        self, *, recover_stale: bool = True, commit_recovery: bool = True
    ) -> bool:
        if recover_stale:
            self.recover_stale_active_imports(commit=commit_recovery)
        return self._repo.has_active_import()

    def mark_running(self, job_id: str) -> Optional[IndicatorImportJobResponse]:
        job = self._repo.mark_running(job_id)
        return self._to_response(job) if job else None

    def complete(self, **kwargs) -> Optional[IndicatorImportJobResponse]:
        job = self._repo.complete(**kwargs)
        return self._to_response(job) if job else None

    def fail(self, **kwargs) -> Optional[IndicatorImportJobResponse]:
        job = self._repo.fail(**kwargs)
        return self._to_response(job) if job else None

    @staticmethod
    def _to_response(job) -> IndicatorImportJobResponse:
        return IndicatorImportJobResponse(
            id=job.id,
            job_type=IndicatorImportJobType(job.job_type),
            source_format=job.source_format,
            status=IndicatorImportJobStatus(job.status),
            submitted_by=job.submitted_by,
            source_filename=job.source_filename,
            source_sha256=job.source_sha256,
            source_size_bytes=job.source_size_bytes,
            validation_job_id=job.validation_job_id,
            request_metadata=job.request_metadata or {},
            total_rows=job.total_rows,
            accepted_rows=job.accepted_rows,
            rejected_rows=job.rejected_rows,
            committed=job.committed,
            result_body=job.result_body,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            updated_at=job.updated_at,
        )


def get_indicator_import_job_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns DB-backed jobs when DB-first mode is enabled."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseIndicatorImportJobStore(db)

    store = getattr(request.app.state, "indicator_import_job_store", None)
    if store is None:
        store = InMemoryIndicatorImportJobStore()
        request.app.state.indicator_import_job_store = store
    return store
