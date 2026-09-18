"""Repository for async value import jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import ValueImportJob

_TERMINAL_STATUSES = {"completed", "failed"}


class ValueImportJobRepository:
    """Persist and query async value import jobs."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        job_id: str,
        source_format: str,
        submitted_by: str,
        source_filename: Optional[str] = None,
        request_metadata: Optional[dict] = None,
    ) -> ValueImportJob:
        job = ValueImportJob(
            id=job_id,
            source_format=source_format,
            status="pending",
            submitted_by=submitted_by,
            source_filename=source_filename,
            request_metadata=request_metadata or {},
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get(self, job_id: str) -> Optional[ValueImportJob]:
        return self.db.query(ValueImportJob).filter(ValueImportJob.id == job_id).first()

    def list_recent_for_user(
        self, submitted_by: str, limit: int = 20
    ) -> list[ValueImportJob]:
        return (
            self.db.query(ValueImportJob)
            .filter(ValueImportJob.submitted_by == submitted_by)
            .order_by(ValueImportJob.created_at.desc(), ValueImportJob.id.desc())
            .limit(limit)
            .all()
        )

    def list_active_for_recovery(self, limit: int) -> list[ValueImportJob]:
        """Inspect active jobs without flushing changes in the caller's session.

        Returns ordinary session objects, not an isolated snapshot or a job claim.
        """
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self.db.no_autoflush:
            return (
                self.db.query(ValueImportJob)
                .filter(ValueImportJob.status.in_(("pending", "running")))
                .order_by(ValueImportJob.created_at.asc(), ValueImportJob.id.asc())
                .limit(limit)
                .all()
            )

    def mark_running(self, job_id: str) -> Optional[ValueImportJob]:
        job = (
            self.db.query(ValueImportJob)
            .filter(ValueImportJob.id == job_id)
            .with_for_update()
            .first()
        )
        if job is None or job.status != "pending":
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
    ) -> Optional[ValueImportJob]:
        job = (
            self.db.query(ValueImportJob)
            .filter(ValueImportJob.id == job_id)
            .with_for_update()
            .first()
        )
        if job is None or job.status in _TERMINAL_STATUSES:
            return None
        now = datetime.now(timezone.utc)
        job.status = "completed"
        job.result_body = result_body
        job.total_rows = total_rows
        job.accepted_rows = accepted_rows
        job.rejected_rows = rejected_rows
        job.committed = committed
        job.error_message = None
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
    ) -> Optional[ValueImportJob]:
        job = (
            self.db.query(ValueImportJob)
            .filter(ValueImportJob.id == job_id)
            .with_for_update()
            .first()
        )
        if job is None or job.status in _TERMINAL_STATUSES:
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
