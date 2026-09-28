"""Evidence preservation and atomic failure tests; SQLite does not prove locks."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from src.database.models import ValueImportJob
from src.database.repositories.value_import_job_repository import (
    ValueImportJobRepository,
)
from src.services.value_import_job_store import DatabaseValueImportJobStore
from tests.value_job_reconciliation_support import (
    CHANGED_FIELDS,
    MODELS,
    assert_reconciled,
    seed_evidence,
    snapshot,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def evidence_engine():
    engine = create_engine("sqlite://")
    try:
        for model in MODELS:
            model.__table__.create(engine)
        with Session(engine) as db:
            seed_evidence(db)
        yield engine
    finally:
        engine.dispose()


def test_reconciliation_preserves_all_other_rows_and_evidence(evidence_engine):
    before = snapshot(evidence_engine)
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(evidence_engine, "before_cursor_execute", capture)
    try:
        with Session(evidence_engine) as db:
            assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 6
    finally:
        event.remove(evidence_engine, "before_cursor_execute", capture)
    after = snapshot(evidence_engine)
    assert_reconciled(before, after)
    # The operation reads only IDs and emits exactly one four-column update.
    assert len(statements) == 2
    assert statements[0].startswith("SELECT value_import_jobs.id ")
    update = statements[1]
    assert update.startswith("UPDATE value_import_jobs SET ")
    assignments = update.split(" SET ")[1].split(" WHERE ")[0].split(", ")
    assert {part.split("=")[0] for part in assignments} == CHANGED_FIELDS
    with Session(evidence_engine) as db:
        assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 0
    assert snapshot(evidence_engine) == after


@pytest.mark.parametrize("failure_stage", ["select", "update", "commit"])
def test_reconciliation_rolls_back_and_can_be_retried(evidence_engine, failure_stage):
    before = snapshot(evidence_engine)

    def fail_statement(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith(failure_stage.upper()):
            raise RuntimeError("synthetic-driver-secret")

    with Session(evidence_engine) as db:
        if failure_stage == "commit":
            # Flush/update has reached the database, but the transaction must roll back.
            def fail_commit(_db):
                raise RuntimeError("synthetic-driver-secret")

            event.listen(db, "before_commit", fail_commit)
        else:
            event.listen(evidence_engine, "before_cursor_execute", fail_statement)
        try:
            with pytest.raises(RuntimeError, match="synthetic-driver-secret"):
                DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs()
            assert not db.in_transaction()
        finally:
            if failure_stage == "commit":
                event.remove(db, "before_commit", fail_commit)
            else:
                event.remove(evidence_engine, "before_cursor_execute", fail_statement)
    assert snapshot(evidence_engine) == before
    with Session(evidence_engine) as db:
        assert DatabaseValueImportJobStore(db).reconcile_legacy_active_jobs() == 6
    assert_reconciled(before, snapshot(evidence_engine))


def test_repository_uses_blocking_ordered_postgres_row_locks():
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.with_for_update.return_value = query
    query.all.return_value = [SimpleNamespace(id="active")]
    query.update.return_value = 1
    assert ValueImportJobRepository(db).reconcile_legacy_active_jobs() == 1
    query.with_for_update.assert_called_once_with()
    query.order_by.assert_called_once()
    assert str(query.order_by.call_args.args[0]) == "value_import_jobs.id ASC"
    predicates = [
        str(
            p.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        for call in query.filter.call_args_list
        for p in call.args
    ]
    assert predicates == [
        "value_import_jobs.status IN ('pending', 'running')",
        "value_import_jobs.id IN ('active')",
        "value_import_jobs.status IN ('pending', 'running')",
    ]
    assert db.query.call_args_list[0].args == (ValueImportJob.id,)
    assert set(query.update.call_args.args[0]) == CHANGED_FIELDS
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_empty_repository_commits_without_update():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.all.return_value = (
        []
    )
    assert ValueImportJobRepository(db).reconcile_legacy_active_jobs() == 0
    assert db.query.call_count == 1
    db.commit.assert_called_once_with()
