"""Repository for indicator import validation and apply jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import IndicatorImportJob

INDICATOR_IMPORT_SUBMISSION_LOCK_KEY = 6401040401


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class IndicatorImportJobRepository:
    """Persist and query indicator import jobs."""

    def __init__(self, db: Session):
        self.db = db

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
    ) -> IndicatorImportJob:
        now = datetime.now(timezone.utc)
        persisted_source_payload = source_payload
        if job_type == "import" and status in {"completed", "failed"}:
            persisted_source_payload = None
        job = IndicatorImportJob(
            id=job_id,
            job_type=job_type,
            source_format=source_format,
            status=status,
            submitted_by=submitted_by,
            source_filename=source_filename,
            source_sha256=source_sha256,
            source_size_bytes=source_size_bytes,
            source_payload=persisted_source_payload,
            validation_job_id=validation_job_id,
            request_metadata=request_metadata or {},
            result_body=result_body,
            total_rows=total_rows,
            accepted_rows=accepted_rows,
            rejected_rows=rejected_rows,
            committed=committed,
            error_message=error_message,
            completed_at=now if status in {"completed", "failed"} else None,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def acquire_import_submission_lock(self) -> None:
        """Serialize indicator import submissions on PostgreSQL.

        The integer is reserved for the SDS indicator-import namespace so future
        advisory locks do not accidentally share the same key.
        """
        bind = self.db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "postgresql":
            return
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": INDICATOR_IMPORT_SUBMISSION_LOCK_KEY},
        )

    def get(self, job_id: str) -> Optional[IndicatorImportJob]:
        return (
            self.db.query(IndicatorImportJob)
            .filter(IndicatorImportJob.id == job_id)
            .first()
        )

    def has_active_import(self) -> bool:
        return (
            self.db.query(IndicatorImportJob.id)
            .filter(
                IndicatorImportJob.job_type == "import",
                IndicatorImportJob.status.in_(("pending", "running")),
            )
            .first()
            is not None
        )

    def fail_stale_active_imports(
        self,
        *,
        max_age_minutes: int,
        now: datetime | None = None,
        commit: bool = True,
    ) -> int:
        if max_age_minutes <= 0:
            return 0

        reference_now = now or datetime.now(timezone.utc)
        cutoff = _utc(reference_now) - timedelta(minutes=max_age_minutes)
        active_jobs = (
            self.db.query(IndicatorImportJob)
            .filter(
                IndicatorImportJob.job_type == "import",
                IndicatorImportJob.status.in_(("pending", "running")),
            )
            .all()
        )
        recovered = 0
        for job in active_jobs:
            age_reference = job.started_at or job.created_at
            if age_reference is None or _utc(age_reference) > cutoff:
                continue
            job.status = "failed"
            job.error_message = (
                "Indicator import job marked failed after exceeding "
                f"the active timeout of {max_age_minutes} minutes."
            )
            job.committed = False
            job.source_payload = None
            job.completed_at = reference_now
            job.updated_at = reference_now
            recovered += 1

        if recovered:
            if commit:
                self.db.commit()
            else:
                self.db.flush()
        return recovered

    def get_source_payload(
        self,
        job_id: str,
        *,
        validation_payload_retention_hours: int | None = None,
        now: datetime | None = None,
    ) -> Optional[str]:
        job = self.get(job_id)
        if job is None or job.source_payload is None:
            return None
        if (
            job.job_type == "validation"
            and validation_payload_retention_hours is not None
        ):
            reference_now = now or datetime.now(timezone.utc)
            reference = job.completed_at or job.created_at
            cutoff = _utc(reference_now) - timedelta(
                hours=validation_payload_retention_hours
            )
            if reference is not None and _utc(reference) <= cutoff:
                job.source_payload = None
                job.updated_at = reference_now
                self.db.commit()
                self.db.refresh(job)
                return None
        return job.source_payload

    def mark_running(self, job_id: str) -> Optional[IndicatorImportJob]:
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
    ) -> Optional[IndicatorImportJob]:
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
        job.error_message = None
        if job.job_type == "import":
            job.source_payload = None
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
    ) -> Optional[IndicatorImportJob]:
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
        if job.job_type == "import":
            job.source_payload = None
        job.completed_at = now
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job
