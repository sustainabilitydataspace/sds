"""Repository for canonical mapping package validation/import jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.services.canonical_mapping_import_lock import CANONICAL_MAPPING_IMPORT_LOCK_KEY

from ..models import CanonicalMappingPackageJob


class CanonicalMappingPackageJobRepository:
    """Persist and query canonical mapping package jobs."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        job_type: str,
        package_id: str,
        status: str,
        submitted_by: str,
        request_metadata: Optional[dict] = None,
        result_body: Optional[dict] = None,
        total_rows: Optional[int] = None,
        accepted_rows: Optional[int] = None,
        rejected_rows: Optional[int] = None,
        committed: Optional[bool] = None,
        error_message: Optional[str] = None,
        validation_job_id: Optional[str] = None,
    ) -> CanonicalMappingPackageJob:
        now = datetime.now(timezone.utc)
        job = CanonicalMappingPackageJob(
            id=job_id,
            job_type=job_type,
            package_id=package_id,
            status=status,
            submitted_by=submitted_by,
            validation_job_id=validation_job_id,
            request_metadata=request_metadata or {},
            result_body=result_body,
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
            error_message=error_message,
            started_at=now if status in {"running", "completed", "failed"} else None,
            completed_at=now if status in {"completed", "failed"} else None,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def acquire_import_submission_lock(self) -> None:
        """Serialize canonical mapping import submission checks on PostgreSQL."""

        bind = self.db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "postgresql":
            return
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
        )

    def get(self, job_id: str) -> Optional[CanonicalMappingPackageJob]:
        return (
            self.db.query(CanonicalMappingPackageJob)
            .filter(CanonicalMappingPackageJob.id == job_id)
            .first()
        )

    def has_active_import(self) -> bool:
        return (
            self.db.query(CanonicalMappingPackageJob.id)
            .filter(
                CanonicalMappingPackageJob.job_type == "import",
                CanonicalMappingPackageJob.status.in_(("pending", "running")),
            )
            .first()
            is not None
        )

    def mark_running(self, job_id: str) -> Optional[CanonicalMappingPackageJob]:
        job = self.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        job.status = "running"
        job.started_at = now
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job

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
    ) -> Optional[CanonicalMappingPackageJob]:
        job = self.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        job.status = "completed"
        job.result_body = result_body
        job.total_rows = total_rows
        job.accepted_rows = accepted_rows
        job.rejected_rows = rejected_rows
        job.committed = committed
        job.error_message = error_message
        job.completed_at = now
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job

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
    ) -> Optional[CanonicalMappingPackageJob]:
        job = self.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        job.status = "failed"
        job.result_body = result_body
        job.total_rows = total_rows
        job.accepted_rows = accepted_rows
        job.rejected_rows = rejected_rows
        job.committed = committed
        job.error_message = error_message
        job.completed_at = now
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job
