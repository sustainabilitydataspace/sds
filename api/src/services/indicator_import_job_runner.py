"""Background execution for async indicator import jobs."""

from __future__ import annotations

from fastapi import FastAPI

from src.config.settings import settings
from src.database.session import SessionLocal
from src.services.indicator_import import (
    IndicatorImportValidationError,
    apply_indicator_import_transactional,
)
from src.services.indicator_import_job_store import DatabaseIndicatorImportJobStore


def run_indicator_import_job(
    *,
    app: FastAPI,
    job_id: str,
    csv_text: str,
    source_ref: str | None,
    source_hash: str | None,
    submitted_by: str,
) -> None:
    """Execute one async indicator import job and persist status transitions."""
    if not settings.require_database:
        store = getattr(app.state, "indicator_import_job_store", None)
        if store is not None:
            store.mark_running(job_id)
            store.fail(
                job_id=job_id,
                error_message="Indicator imports require database-backed mode",
                committed=False,
            )
        return

    db = SessionLocal()
    try:
        job_store = DatabaseIndicatorImportJobStore(db)
        job_store.mark_running(job_id)
        plan = apply_indicator_import_transactional(
            csv_text=csv_text,
            db=db,
            source_ref=source_ref,
            source_hash=source_hash,
            created_by=submitted_by,
            max_rows=settings.indicator_import_max_rows,
        )
        result_body = plan.to_result_body()
        job_store.complete(
            job_id=job_id,
            result_body=result_body,
            total_rows=plan.total_rows,
            accepted_rows=plan.accepted_rows,
            rejected_rows=plan.rejected_rows,
            committed=plan.committed,
        )
    except IndicatorImportValidationError as error:
        result_body = error.plan.to_result_body()
        DatabaseIndicatorImportJobStore(db).fail(
            job_id=job_id,
            error_message="Validation failed for one or more indicator rows",
            result_body=result_body,
            total_rows=error.plan.total_rows,
            accepted_rows=error.plan.accepted_rows,
            rejected_rows=error.plan.rejected_rows,
            committed=False,
        )
    except Exception as error:
        db.rollback()
        DatabaseIndicatorImportJobStore(db).fail(
            job_id=job_id, error_message=str(error), committed=False
        )
    finally:
        db.close()
