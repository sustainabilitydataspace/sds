from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from src.api.models import ValueImportJobStatus
from src.services.indicator_import import IndicatorImportValidationError
from src.services.indicator_import_job_runner import run_indicator_import_job
from src.services.indicator_import_job_store import InMemoryIndicatorImportJobStore
from src.services.value_import_job_runner import (
    _get_runtime_graph,
    run_value_import_job,
)
from src.services.value_import_job_store import InMemoryValueImportJobStore


class _FakeDb:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _FakeIndicatorJobStore:
    instances: list["_FakeIndicatorJobStore"] = []

    def __init__(self, _db) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.instances.append(self)

    def mark_running(self, job_id: str) -> None:
        self.calls.append(("running", {"job_id": job_id}))
        return SimpleNamespace(status=ValueImportJobStatus.RUNNING)

    def complete(self, **kwargs) -> None:
        self.calls.append(("complete", kwargs))

    def fail(self, **kwargs) -> None:
        self.calls.append(("fail", kwargs))


class _FakeValueJobStore:
    instances: list["_FakeValueJobStore"] = []

    def __init__(self, _db) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.instances.append(self)

    def mark_running(self, job_id: str) -> None:
        self.calls.append(("running", {"job_id": job_id}))
        return SimpleNamespace(status=ValueImportJobStatus.RUNNING)

    def complete(self, **kwargs) -> None:
        self.calls.append(("complete", kwargs))

    def fail(self, **kwargs) -> None:
        self.calls.append(("fail", kwargs))


def _plan(**overrides):
    data = {
        "total_rows": 2,
        "accepted_rows": 2,
        "rejected_rows": 0,
        "committed": True,
        "to_result_body": lambda: {"valid": True, "committed": True},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _batch_result(*, committed: bool):
    return SimpleNamespace(
        total_rows=2,
        accepted_rows=2 if committed else 1,
        rejected_rows=0 if committed else 1,
        committed=committed,
        model_dump=lambda mode=None: {
            "committed": committed,
            "mode": mode,
        },
    )


def test_indicator_import_runner_rejects_non_database_mode(monkeypatch) -> None:
    app = FastAPI()
    store = InMemoryIndicatorImportJobStore()
    store.create(
        job_id="job-1",
        job_type="import",
        source_format="csv",
        submitted_by="admin",
        tenant_id="tenant-a",
        owner_user_id="admin-id",
    )
    app.state.indicator_import_job_store = store
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.settings.require_database",
        False,
    )

    run_indicator_import_job(
        app=app,
        job_id="job-1",
        csv_text="identifier\n",
        source_ref=None,
        source_hash=None,
        submitted_by="admin",
    )

    job = store.get("job-1", tenant_id="tenant-a")
    assert job.status.value == "failed"
    assert job.error_message == "Indicator imports require database-backed mode"


def test_indicator_import_runner_completes_database_job(monkeypatch) -> None:
    db = _FakeDb()
    _FakeIndicatorJobStore.instances = []
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.settings.require_database",
        True,
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.settings.indicator_import_max_rows",
        9,
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.SessionLocal", lambda: db
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.DatabaseIndicatorImportJobStore",
        _FakeIndicatorJobStore,
    )

    def _apply(**kwargs):
        captured.update(kwargs)
        return _plan()

    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.apply_indicator_import_transactional",
        _apply,
    )

    run_indicator_import_job(
        app=FastAPI(),
        job_id="job-2",
        csv_text="identifier\n",
        source_ref="package.csv",
        source_hash="sha",
        submitted_by="admin",
    )

    assert captured["max_rows"] == 9
    assert db.closed is True
    assert _FakeIndicatorJobStore.instances[0].calls == [
        ("running", {"job_id": "job-2"}),
        (
            "complete",
            {
                "job_id": "job-2",
                "result_body": {"valid": True, "committed": True},
                "total_rows": 2,
                "accepted_rows": 2,
                "rejected_rows": 0,
                "committed": True,
            },
        ),
    ]


def test_indicator_import_runner_records_validation_and_generic_failures(
    monkeypatch,
) -> None:
    db = _FakeDb()
    _FakeIndicatorJobStore.instances = []
    invalid_plan = _plan(
        rejected_rows=1,
        committed=False,
        to_result_body=lambda: {"valid": False, "errors": [{"row_number": 2}]},
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.settings.require_database",
        True,
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.SessionLocal", lambda: db
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.DatabaseIndicatorImportJobStore",
        _FakeIndicatorJobStore,
    )
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.apply_indicator_import_transactional",
        MagicMock(side_effect=IndicatorImportValidationError(invalid_plan)),
    )

    run_indicator_import_job(
        app=FastAPI(),
        job_id="job-3",
        csv_text="identifier\n",
        source_ref=None,
        source_hash=None,
        submitted_by="admin",
    )

    validation_fail = _FakeIndicatorJobStore.instances[1].calls[0]
    assert validation_fail[0] == "fail"
    assert (
        validation_fail[1]["error_message"]
        == "Validation failed for one or more indicator rows"
    )
    assert validation_fail[1]["rejected_rows"] == 1

    _FakeIndicatorJobStore.instances = []
    monkeypatch.setattr(
        "src.services.indicator_import_job_runner.apply_indicator_import_transactional",
        MagicMock(side_effect=RuntimeError("database write failed")),
    )

    run_indicator_import_job(
        app=FastAPI(),
        job_id="job-4",
        csv_text="identifier\n",
        source_ref=None,
        source_hash=None,
        submitted_by="admin",
    )

    assert db.rolled_back is True
    generic_fail = _FakeIndicatorJobStore.instances[1].calls[0]
    assert generic_fail == (
        "fail",
        {
            "job_id": "job-4",
            "error_message": "database write failed",
            "committed": False,
        },
    )


def test_value_import_runner_fails_job_when_unit_converter_missing() -> None:
    app = FastAPI()
    store = InMemoryValueImportJobStore()
    store.create(
        job_id="value-job-0",
        source_format="json",
        submitted_by="user",
    )
    app.state.value_import_job_store = store

    run_value_import_job(
        app=app,
        job_id="value-job-0",
        items=[],
        submitted_by="user",
        company_id="acme",
    )

    job = store.get("value-job-0")
    assert job.status == ValueImportJobStatus.FAILED
    assert "Unit converter not initialized" in job.error_message


def test_value_import_runner_does_not_restart_terminal_job(monkeypatch) -> None:
    app = FastAPI()
    store = InMemoryValueImportJobStore()
    store.create(job_id="value-job-terminal", source_format="json", submitted_by="user")
    store.fail(job_id="value-job-terminal", error_message="already failed")
    app.state.value_import_job_store = store
    app.state.unit_converter = object()
    app.state.ontology_graph = object()
    execute = MagicMock()
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch", execute
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        False,
    )

    run_value_import_job(
        app=app,
        job_id="value-job-terminal",
        items=[],
        submitted_by="user",
        company_id="acme",
    )

    execute.assert_not_called()
    job = store.get("value-job-terminal")
    assert job.status == ValueImportJobStatus.FAILED
    assert job.error_message == "already failed"


def test_value_import_runner_database_failure_is_generic_without_unit_converter(
    monkeypatch,
) -> None:
    db = _FakeDb()
    _FakeValueJobStore.instances = []
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        True,
    )
    monkeypatch.setattr("src.services.value_import_job_runner.SessionLocal", lambda: db)
    monkeypatch.setattr(
        "src.services.value_import_job_runner.DatabaseValueImportJobStore",
        _FakeValueJobStore,
    )
    app = FastAPI()

    run_value_import_job(
        app=app,
        job_id="value-job-db-missing-converter",
        items=[],
        submitted_by="user",
        company_id="acme",
    )

    calls = [call for store in _FakeValueJobStore.instances for call in store.calls]
    assert calls == [
        ("running", {"job_id": "value-job-db-missing-converter"}),
        (
            "fail",
            {
                "job_id": "value-job-db-missing-converter",
                "error_message": (
                    "Database-backed value import jobs are temporarily disabled"
                ),
                "committed": False,
            },
        ),
    ]
    assert "Unit converter" not in calls[-1][1]["error_message"]
    assert db.closed is True


def test_value_import_runner_fails_job_when_runtime_graph_load_fails(
    monkeypatch,
) -> None:
    app = FastAPI()
    app.state.unit_converter = object()
    store = InMemoryValueImportJobStore()
    store.create(
        job_id="value-job-graph-failure",
        source_format="json",
        submitted_by="user",
    )
    app.state.value_import_job_store = store
    monkeypatch.setattr(
        "src.services.value_import_job_runner.load_ontology_graph",
        MagicMock(side_effect=RuntimeError("graph load failed")),
    )

    run_value_import_job(
        app=app,
        job_id="value-job-graph-failure",
        items=[],
        submitted_by="user",
        company_id="acme",
    )

    job = store.get("value-job-graph-failure")
    assert job.status == ValueImportJobStatus.FAILED
    assert job.error_message == "graph load failed"


def test_value_import_runner_runtime_graph_is_cached(monkeypatch) -> None:
    app = FastAPI()
    monkeypatch.setattr(
        "src.services.value_import_job_runner.load_ontology_graph",
        lambda: ("graph", {"loaded": True}),
    )

    assert _get_runtime_graph(app) == "graph"
    assert _get_runtime_graph(app) == "graph"


def test_value_import_runner_non_database_paths(monkeypatch) -> None:
    app = FastAPI()
    app.state.unit_converter = object()
    store = InMemoryValueImportJobStore()
    store.create(
        job_id="value-job-1",
        source_format="json",
        submitted_by="user",
    )
    app.state.value_import_job_store = store
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        False,
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner._get_runtime_graph",
        lambda _app: "graph",
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch",
        lambda **_kwargs: _batch_result(committed=True),
    )

    run_value_import_job(
        app=app,
        job_id="value-job-1",
        items=[object()],
        submitted_by="user",
        company_id="acme",
    )

    completed = store.get("value-job-1")
    assert completed.status == ValueImportJobStatus.COMPLETED
    assert completed.result_body["import_job"]["elapsed_seconds"] >= 0
    assert completed.result_body["import_job"]["total_rows"] == 2
    assert hasattr(app.state, "value_store")
    assert hasattr(app.state, "hierarchy_store")

    store.create(
        job_id="value-job-2",
        source_format="json",
        submitted_by="user",
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch",
        lambda **_kwargs: _batch_result(committed=False),
    )

    run_value_import_job(
        app=app,
        job_id="value-job-2",
        items=[object()],
        submitted_by="user",
        company_id="acme",
    )

    failed = store.get("value-job-2")
    assert failed.status == ValueImportJobStatus.FAILED
    assert failed.error_message == "Validation failed for one or more rows"
    assert failed.result_body["import_job"]["committed"] is False

    store.create(
        job_id="value-job-3",
        source_format="json",
        submitted_by="user",
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch",
        MagicMock(side_effect=RuntimeError("bad batch")),
    )

    run_value_import_job(
        app=app,
        job_id="value-job-3",
        items=[object()],
        submitted_by="user",
        company_id="acme",
    )

    assert store.get("value-job-3").error_message == "bad batch"


def test_value_import_runner_initializes_missing_in_memory_job_store(
    monkeypatch,
) -> None:
    app = FastAPI()
    app.state.unit_converter = object()
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        False,
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner._get_runtime_graph",
        lambda _app: "graph",
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch",
        lambda **_kwargs: _batch_result(committed=False),
    )

    run_value_import_job(
        app=app,
        job_id="missing-job-row",
        items=[object()],
        submitted_by="user",
        company_id="acme",
    )

    assert isinstance(app.state.value_import_job_store, InMemoryValueImportJobStore)


def test_value_import_runner_database_mode_is_contained_before_value_execution(
    monkeypatch,
) -> None:
    db = _FakeDb()
    _FakeValueJobStore.instances = []
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        True,
    )
    monkeypatch.setattr("src.services.value_import_job_runner.SessionLocal", lambda: db)
    monkeypatch.setattr(
        "src.services.value_import_job_runner.DatabaseValueImportJobStore",
        _FakeValueJobStore,
    )
    dependency_factories = {
        name: MagicMock(side_effect=AssertionError(f"{name} must not be constructed"))
        for name in (
            "DatabaseValueStore",
            "DatabaseHierarchyStore",
            "IndicatorStore",
            "CanonicalMappingStore",
        )
    }
    for name, factory in dependency_factories.items():
        monkeypatch.setattr(
            f"src.services.value_import_job_runner.{name}",
            factory,
            raising=False,
        )
    graph_loader = MagicMock(side_effect=AssertionError("graph must not be loaded"))
    execute = MagicMock(side_effect=AssertionError("batch must not execute"))
    monkeypatch.setattr(
        "src.services.value_import_job_runner._get_runtime_graph", graph_loader
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch", execute
    )
    app = FastAPI()
    app.state.unit_converter = object()

    run_value_import_job(
        app=app,
        job_id="value-job-4",
        items=[object()],
        submitted_by="user",
        company_id="tenant-job",
    )

    assert db.closed is True
    assert _FakeValueJobStore.instances[0].calls == [
        ("running", {"job_id": "value-job-4"}),
        (
            "fail",
            {
                "job_id": "value-job-4",
                "error_message": (
                    "Database-backed value import jobs are temporarily disabled"
                ),
                "committed": False,
            },
        ),
    ]
    graph_loader.assert_not_called()
    execute.assert_not_called()
    for factory in dependency_factories.values():
        factory.assert_not_called()


def test_value_import_runner_database_containment_does_not_infer_missing_company(
    monkeypatch,
) -> None:
    db = _FakeDb()
    _FakeValueJobStore.instances = []
    execute = MagicMock(side_effect=AssertionError("batch must not execute"))
    monkeypatch.setattr(
        "src.services.value_import_job_runner.settings.require_database",
        True,
    )
    monkeypatch.setattr("src.services.value_import_job_runner.SessionLocal", lambda: db)
    monkeypatch.setattr(
        "src.services.value_import_job_runner.DatabaseValueImportJobStore",
        _FakeValueJobStore,
    )
    monkeypatch.setattr(
        "src.services.value_import_job_runner.execute_value_batch",
        execute,
    )
    app = FastAPI()
    app.state.unit_converter = object()

    run_value_import_job(
        app=app,
        job_id="value-job-missing-company",
        items=[object()],
        submitted_by="user",
        company_id=None,
    )

    calls = [call for store in _FakeValueJobStore.instances for call in store.calls]
    execute.assert_not_called()
    assert calls[-1] == (
        "fail",
        {
            "job_id": "value-job-missing-company",
            "error_message": (
                "Database-backed value import jobs are temporarily disabled"
            ),
            "committed": False,
        },
    )
    assert "company" not in calls[-1][1]["error_message"].lower()
    assert db.closed is True
