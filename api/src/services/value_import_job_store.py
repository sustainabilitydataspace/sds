"""Persistence backends for async value import jobs."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.models import ValueImportJobResponse, ValueImportJobStatus
from src.config.settings import settings
from src.database.repositories.value_import_job_repository import (
    ValueImportJobRepository,
)
from src.database.session import get_db_optional

_TERMINAL_STATUSES = {
    ValueImportJobStatus.COMPLETED,
    ValueImportJobStatus.FAILED,
}


@dataclass(frozen=True)
class _StoredValueImportJob:
    id: str
    source_format: str
    status: ValueImportJobStatus
    submitted_by: str
    source_filename: Optional[str]
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


class InMemoryValueImportJobStore:
    """Offline-safe async job store for values imports."""

    def __init__(self):
        self._jobs: Dict[str, _StoredValueImportJob] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        job_id: str,
        source_format: str,
        submitted_by: str,
        source_filename: Optional[str] = None,
        request_metadata: Optional[dict] = None,
    ) -> ValueImportJobResponse:
        now = datetime.now(timezone.utc)
        stored = _StoredValueImportJob(
            id=job_id,
            source_format=source_format,
            status=ValueImportJobStatus.PENDING,
            submitted_by=submitted_by,
            source_filename=source_filename,
            request_metadata=request_metadata or {},
            total_rows=None,
            accepted_rows=None,
            rejected_rows=None,
            committed=None,
            result_body=None,
            error_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = stored
        return self._to_response(stored)

    def get(self, job_id: str) -> Optional[ValueImportJobResponse]:
        with self._lock:
            stored = self._jobs.get(job_id)
            return self._to_response(stored) if stored else None

    def mark_running(self, job_id: str) -> Optional[ValueImportJobResponse]:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None or stored.status != ValueImportJobStatus.PENDING:
                return None
            now = datetime.now(timezone.utc)
            updated = _StoredValueImportJob(
                **{
                    **stored.__dict__,
                    "status": ValueImportJobStatus.RUNNING,
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
    ) -> Optional[ValueImportJobResponse]:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None or stored.status in _TERMINAL_STATUSES:
                return None
            now = datetime.now(timezone.utc)
            updated = _StoredValueImportJob(
                **{
                    **stored.__dict__,
                    "status": ValueImportJobStatus.COMPLETED,
                    "result_body": result_body,
                    "total_rows": total_rows,
                    "accepted_rows": accepted_rows,
                    "rejected_rows": rejected_rows,
                    "committed": committed,
                    "error_message": None,
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
    ) -> Optional[ValueImportJobResponse]:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None or stored.status in _TERMINAL_STATUSES:
                return None
            now = datetime.now(timezone.utc)
            updated = _StoredValueImportJob(
                **{
                    **stored.__dict__,
                    "status": ValueImportJobStatus.FAILED,
                    "result_body": result_body,
                    "total_rows": total_rows,
                    "accepted_rows": accepted_rows,
                    "rejected_rows": rejected_rows,
                    "committed": committed,
                    "error_message": error_message,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = updated
            return self._to_response(updated)

    @staticmethod
    def _to_response(stored: _StoredValueImportJob) -> ValueImportJobResponse:
        return ValueImportJobResponse(**stored.__dict__)


class DatabaseValueImportJobStore:
    """PostgreSQL-backed async job store for value imports."""

    def __init__(self, db: Session):
        self._repo = ValueImportJobRepository(db)

    def create(
        self,
        *,
        job_id: str,
        source_format: str,
        submitted_by: str,
        source_filename: Optional[str] = None,
        request_metadata: Optional[dict] = None,
    ) -> ValueImportJobResponse:
        return self._to_response(
            self._repo.create(
                job_id=job_id,
                source_format=source_format,
                submitted_by=submitted_by,
                source_filename=source_filename,
                request_metadata=request_metadata,
            )
        )

    def get(self, job_id: str) -> Optional[ValueImportJobResponse]:
        job = self._repo.get(job_id)
        return self._to_response(job) if job else None

    def mark_running(self, job_id: str) -> Optional[ValueImportJobResponse]:
        job = self._repo.mark_running(job_id)
        return self._to_response(job) if job else None

    def list_active_for_recovery(self, limit: int) -> list[ValueImportJobResponse]:
        return [
            self._to_response(job) for job in self._repo.list_active_for_recovery(limit)
        ]

    def complete(
        self,
        *,
        job_id: str,
        result_body: dict,
        total_rows: int,
        accepted_rows: int,
        rejected_rows: int,
        committed: bool,
    ) -> Optional[ValueImportJobResponse]:
        job = self._repo.complete(
            job_id=job_id,
            result_body=result_body,
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
        )
        return self._to_response(job) if job else None

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
    ) -> Optional[ValueImportJobResponse]:
        job = self._repo.fail(
            job_id=job_id,
            error_message=error_message,
            result_body=result_body,
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
        )
        return self._to_response(job) if job else None

    @staticmethod
    def _to_response(job) -> ValueImportJobResponse:
        return ValueImportJobResponse(
            id=job.id,
            source_format=job.source_format,
            status=ValueImportJobStatus(job.status),
            submitted_by=job.submitted_by,
            source_filename=job.source_filename,
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


def get_value_import_job_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency that returns the configured store (DB when required)."""
    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseValueImportJobStore(db)

    store = getattr(request.app.state, "value_import_job_store", None)
    if store is None:
        store = InMemoryValueImportJobStore()
        request.app.state.value_import_job_store = store
    return store
