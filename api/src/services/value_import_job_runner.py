"""Background execution for async value import jobs."""

from __future__ import annotations

from time import perf_counter

from fastapi import FastAPI

from src.config.settings import settings
from src.database.session import SessionLocal
from src.ontology.local_graph import load_ontology_graph
from src.services.hierarchy_store import InMemoryHierarchyStore
from src.services.value_batch import execute_value_batch
from src.services.value_import_job_store import (
    DatabaseValueImportJobStore,
    InMemoryValueImportJobStore,
)
from src.services.value_store import InMemoryValueStore

_DB_VALUE_IMPORT_CONTAINMENT_MESSAGE = (
    "Database-backed value import jobs are temporarily disabled"
)


def _get_runtime_graph(app: FastAPI):
    graph = getattr(app.state, "ontology_graph", None)
    if graph is not None:
        return graph
    graph, _ = load_ontology_graph()
    app.state.ontology_graph = graph
    return graph


def _job_result_body(result, *, elapsed_seconds: float) -> dict:
    body = result.model_dump(mode="json")
    body["import_job"] = {
        "elapsed_seconds": round(elapsed_seconds, 6),
        "total_rows": result.total_rows,
        "accepted_rows": result.accepted_rows,
        "rejected_rows": result.rejected_rows,
        "committed": result.committed,
    }
    return body


def run_value_import_job(
    *,
    app: FastAPI,
    job_id: str,
    items,
    submitted_by: str,
    company_id: str | None,
) -> None:
    """Execute one async value import job and persist status transitions."""
    if settings.require_database:
        db = SessionLocal()
        try:
            job_store = DatabaseValueImportJobStore(db)
            running_job = job_store.mark_running(job_id)
            if running_job is None:
                return
            job_store.fail(
                job_id=job_id,
                error_message=_DB_VALUE_IMPORT_CONTAINMENT_MESSAGE,
                committed=False,
            )
        except Exception:
            db.rollback()
            DatabaseValueImportJobStore(db).fail(
                job_id=job_id,
                error_message=_DB_VALUE_IMPORT_CONTAINMENT_MESSAGE,
                committed=False,
            )
        finally:
            db.close()
        return

    converter = getattr(app.state, "unit_converter", None)
    graph = None
    try:
        if converter is None:
            raise RuntimeError("Unit converter not initialized")
        graph = _get_runtime_graph(app)
    except Exception as init_error:
        _fail_value_import_job(
            app=app,
            job_id=job_id,
            error_message=str(init_error),
        )
        return

    job_store = getattr(app.state, "value_import_job_store", None)
    if job_store is None:
        job_store = InMemoryValueImportJobStore()
        app.state.value_import_job_store = job_store

    value_store = getattr(app.state, "value_store", None)
    if value_store is None:
        value_store = InMemoryValueStore()
        app.state.value_store = value_store

    hierarchy_store = getattr(app.state, "hierarchy_store", None)
    if hierarchy_store is None:
        hierarchy_store = InMemoryHierarchyStore()
        app.state.hierarchy_store = hierarchy_store

    try:
        running_job = job_store.mark_running(job_id)
        if running_job is None:
            return
        start = perf_counter()
        result = execute_value_batch(
            items=items,
            converter=converter,
            store=value_store,
            hierarchy_store=hierarchy_store,
            graph=graph,
            created_by=submitted_by,
            company_id=company_id,
            strict=False,
        )
        result_body = _job_result_body(
            result,
            elapsed_seconds=perf_counter() - start,
        )
        if result.committed:
            job_store.complete(
                job_id=job_id,
                result_body=result_body,
                total_rows=result.total_rows,
                accepted_rows=result.accepted_rows,
                rejected_rows=result.rejected_rows,
                committed=result.committed,
            )
        else:
            job_store.fail(
                job_id=job_id,
                error_message="Validation failed for one or more rows",
                result_body=result_body,
                total_rows=result.total_rows,
                accepted_rows=result.accepted_rows,
                rejected_rows=result.rejected_rows,
                committed=result.committed,
            )
    except Exception as error:
        job_store.fail(job_id=job_id, error_message=str(error))


def _fail_value_import_job(
    *,
    app: FastAPI,
    job_id: str,
    error_message: str,
) -> None:
    """Persist a terminal failed status when pre-execution init fails."""
    if settings.require_database:
        try:
            db = SessionLocal()
        except Exception:
            return
        try:
            DatabaseValueImportJobStore(db).fail(
                job_id=job_id, error_message=error_message
            )
        except Exception:
            db.rollback()
        finally:
            db.close()
        return

    job_store = getattr(app.state, "value_import_job_store", None)
    if job_store is None:
        job_store = InMemoryValueImportJobStore()
        app.state.value_import_job_store = job_store
    job_store.fail(job_id=job_id, error_message=error_message)
