"""Repository for canonical mapping package validation/import jobs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.services.canonical_mapping_import_lock import (
    CANONICAL_MAPPING_IMPORT_LOCK_KEY,
    CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY,
)

from ..models import CanonicalMappingPackageJob


class CanonicalMappingImportBusy(RuntimeError):
    """A live import owns the nonblocking cross-worker admission lock."""


@contextmanager
def canonical_mapping_import_session(bind: Engine):
    """Keep job and import transactions on one pinned physical connection."""
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Canonical mapping import requires PostgreSQL")
    with bind.connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY},
        ).scalar_one()
        connection.commit()
        if not acquired:
            raise CanonicalMappingImportBusy("Canonical mapping import busy")
        try:
            with Session(bind=connection) as session:
                yield session
        finally:
            try:
                connection.rollback()
                unlocked = connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY},
                ).scalar_one()
                connection.commit()
                if not unlocked:
                    raise RuntimeError(
                        "Canonical mapping import lock release unconfirmed"
                    )
            except Exception:
                connection.invalidate()
                raise


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

    @contextmanager
    def import_lifecycle_lock(self):
        """Hold an independent transaction lock through all committed job states."""
        bind = self.db.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            raise RuntimeError("Canonical mapping import lifecycle requires PostgreSQL")
        with bind.connect() as guard:
            guard.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY},
            )
            try:
                yield
            finally:
                try:
                    guard.rollback()
                except Exception:
                    guard.invalidate()
                    raise

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

    def reconcile_legacy_active_jobs(self) -> int:
        """Fail abandoned imports during startup or admission without replaying writes."""
        bind = self.db.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            raise RuntimeError(
                "Canonical mapping job reconciliation requires PostgreSQL"
            )
        lifecycle_acquired = self.db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": CANONICAL_MAPPING_JOB_LIFECYCLE_LOCK_KEY},
        ).scalar_one()
        if not lifecycle_acquired:
            raise RuntimeError("Canonical mapping reconciliation lock unavailable")
        acquired = self.db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
        ).scalar_one()
        if not acquired:
            raise RuntimeError("Canonical mapping reconciliation lock unavailable")
        now = datetime.now(timezone.utc)
        result = self.db.execute(
            update(CanonicalMappingPackageJob)
            .where(
                CanonicalMappingPackageJob.job_type == "import",
                CanonicalMappingPackageJob.status.in_(("pending", "running")),
            )
            .values(
                status="failed",
                error_message="Interrupted canonical mapping import; retry requires new validation",
                completed_at=now,
                updated_at=now,
            )
        )
        self.db.commit()
        return result.rowcount

    def mark_running(self, job_id: str) -> Optional[CanonicalMappingPackageJob]:
        job = self.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        updated = (
            self.db.query(CanonicalMappingPackageJob)
            .filter(
                CanonicalMappingPackageJob.id == job_id,
                CanonicalMappingPackageJob.status == "pending",
            )
            .update(
                {"status": "running", "started_at": now, "updated_at": now},
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None
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
        commit: bool = True,
    ) -> Optional[CanonicalMappingPackageJob]:
        job = self.get(job_id)
        if job is None:
            return None
        now = datetime.now(timezone.utc)
        updated = (
            self.db.query(CanonicalMappingPackageJob)
            .filter(
                CanonicalMappingPackageJob.id == job_id,
                CanonicalMappingPackageJob.status == "running",
            )
            .update(
                {
                    "status": "completed",
                    "result_body": result_body,
                    "total_rows": total_rows,
                    "accepted_rows": accepted_rows,
                    "rejected_rows": rejected_rows,
                    "committed": committed,
                    "error_message": error_message,
                    "completed_at": now,
                    "updated_at": now,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
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
        updated = (
            self.db.query(CanonicalMappingPackageJob)
            .filter(
                CanonicalMappingPackageJob.id == job_id,
                CanonicalMappingPackageJob.status.in_(("pending", "running")),
            )
            .update(
                {
                    "status": "failed",
                    "result_body": result_body,
                    "total_rows": total_rows,
                    "accepted_rows": accepted_rows,
                    "rejected_rows": rejected_rows,
                    "committed": committed,
                    "error_message": error_message,
                    "completed_at": now,
                    "updated_at": now,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None
        self.db.commit()
        self.db.refresh(job)
        return job
