"""Job stores for canonical mapping package validation/import endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.models import (
    CanonicalMappingPackageJobResponse,
    CanonicalMappingPackageJobStatus,
    CanonicalMappingPackageJobType,
)
from src.config.settings import settings
from src.database.repositories.canonical_mapping_package_job_repository import (
    CanonicalMappingPackageJobRepository,
)
from src.database.session import get_db_optional


@dataclass(frozen=True)
class _StoredCanonicalMappingPackageJob:
    id: str
    job_type: CanonicalMappingPackageJobType
    package_id: str
    status: CanonicalMappingPackageJobStatus
    submitted_by: str
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


class InMemoryCanonicalMappingPackageJobStore:
    """Offline-safe job store used when DB-first mode is not required."""

    def __init__(self) -> None:
        self._jobs: Dict[str, _StoredCanonicalMappingPackageJob] = {}

    def create(
        self,
        *,
        job_id: str,
        job_type: str,
        package_id: str,
        submitted_by: str,
        request_metadata: Optional[dict] = None,
        status: str = "pending",
        result_body: Optional[dict] = None,
        total_rows: Optional[int] = None,
        accepted_rows: Optional[int] = None,
        rejected_rows: Optional[int] = None,
        committed: Optional[bool] = None,
        error_message: Optional[str] = None,
        validation_job_id: Optional[str] = None,
    ) -> CanonicalMappingPackageJobResponse:
        now = datetime.now(timezone.utc)
        stored = _StoredCanonicalMappingPackageJob(
            id=job_id,
            job_type=CanonicalMappingPackageJobType(job_type),
            package_id=package_id,
            status=CanonicalMappingPackageJobStatus(status),
            submitted_by=submitted_by,
            validation_job_id=validation_job_id,
            request_metadata=request_metadata or {},
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
            result_body=result_body,
            error_message=error_message,
            created_at=now,
            started_at=now if status in {"running", "completed", "failed"} else None,
            completed_at=now if status in {"completed", "failed"} else None,
            updated_at=now,
        )
        self._jobs[job_id] = stored
        return self._to_response(stored)

    def get(self, job_id: str) -> Optional[CanonicalMappingPackageJobResponse]:
        stored = self._jobs.get(job_id)
        return self._to_response(stored) if stored else None

    def acquire_import_submission_lock(self) -> None:
        return None

    def has_active_import(self) -> bool:
        return any(
            job.job_type == CanonicalMappingPackageJobType.IMPORT
            and job.status
            in {
                CanonicalMappingPackageJobStatus.PENDING,
                CanonicalMappingPackageJobStatus.RUNNING,
            }
            for job in self._jobs.values()
        )

    def mark_running(self, job_id: str) -> Optional[CanonicalMappingPackageJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredCanonicalMappingPackageJob(
            **{
                **stored.__dict__,
                "status": CanonicalMappingPackageJobStatus.RUNNING,
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
        error_message: Optional[str] = None,
    ) -> Optional[CanonicalMappingPackageJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredCanonicalMappingPackageJob(
            **{
                **stored.__dict__,
                "status": CanonicalMappingPackageJobStatus.COMPLETED,
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
    ) -> Optional[CanonicalMappingPackageJobResponse]:
        stored = self._jobs.get(job_id)
        if stored is None:
            return None
        now = datetime.now(timezone.utc)
        updated = _StoredCanonicalMappingPackageJob(
            **{
                **stored.__dict__,
                "status": CanonicalMappingPackageJobStatus.FAILED,
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
    def _to_response(
        stored: _StoredCanonicalMappingPackageJob,
    ) -> CanonicalMappingPackageJobResponse:
        return CanonicalMappingPackageJobResponse(**stored.__dict__)


class DatabaseCanonicalMappingPackageJobStore:
    """PostgreSQL-backed canonical mapping package job store."""

    def __init__(self, db: Session):
        self._repo = CanonicalMappingPackageJobRepository(db)

    def create(self, **kwargs) -> CanonicalMappingPackageJobResponse:
        return self._to_response(self._repo.create(**kwargs))

    def get(self, job_id: str) -> Optional[CanonicalMappingPackageJobResponse]:
        job = self._repo.get(job_id)
        return self._to_response(job) if job else None

    def acquire_import_submission_lock(self) -> None:
        self._repo.acquire_import_submission_lock()

    def has_active_import(self) -> bool:
        return self._repo.has_active_import()

    def mark_running(self, job_id: str) -> Optional[CanonicalMappingPackageJobResponse]:
        job = self._repo.mark_running(job_id)
        return self._to_response(job) if job else None

    def complete(self, **kwargs) -> Optional[CanonicalMappingPackageJobResponse]:
        job = self._repo.complete(**kwargs)
        return self._to_response(job) if job else None

    def fail(self, **kwargs) -> Optional[CanonicalMappingPackageJobResponse]:
        job = self._repo.fail(**kwargs)
        return self._to_response(job) if job else None

    @staticmethod
    def _to_response(job) -> CanonicalMappingPackageJobResponse:
        return CanonicalMappingPackageJobResponse(
            id=job.id,
            job_type=CanonicalMappingPackageJobType(job.job_type),
            package_id=job.package_id,
            status=CanonicalMappingPackageJobStatus(job.status),
            submitted_by=job.submitted_by,
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


def get_canonical_mapping_package_job_store(
    request: Request,
    db: Optional[Session] = Depends(get_db_optional),
):
    """Dependency returning DB-backed jobs when DB-first mode is enabled."""

    if settings.require_database:
        if db is None:
            raise HTTPException(status_code=503, detail="Database session unavailable")
        return DatabaseCanonicalMappingPackageJobStore(db)

    store = getattr(request.app.state, "canonical_mapping_package_job_store", None)
    if store is None:
        store = InMemoryCanonicalMappingPackageJobStore()
        request.app.state.canonical_mapping_package_job_store = store
    return store
