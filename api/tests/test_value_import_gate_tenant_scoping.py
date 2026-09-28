"""Gate evidence must only count rows owned by the explicit fixture tenant."""

from __future__ import annotations

import pytest

from scripts import gate_raw_value_transform_matrix as r8
from scripts import gate_value_import_performance as r10
from scripts import profile_value_import as profile
from scripts.value_import_gate_hierarchy import VALUE_IMPORT_GATE_COMPANY_ID


class RecordingQuery:
    def __init__(self, scope, rows):
        self.scope = scope
        self.rows = rows

    def filter(self, *criteria):
        self.scope.append(" ".join(str(criterion) for criterion in criteria))
        return self

    def distinct(self):
        return self

    def all(self):
        return self.rows

    def count(self):
        return 1


class RecordingDb:
    def __init__(self):
        self.scopes = []

    def query(self, *args):
        scope = []
        self.scopes.append(scope)
        rows = [] if args[0] is r8.ESGValue else [(1,)]
        return RecordingQuery(scope, rows)


def test_r8_persisted_values_are_tenant_scoped():
    db = RecordingDb()
    r8._load_actual_rows(db, "gate-r8-test", VALUE_IMPORT_GATE_COMPANY_ID)
    assert len(db.scopes) == 1
    assert "tenant_id" in " ".join(db.scopes[0])


def test_r8_and_r10_persistence_counts_are_tenant_scoped():
    db = RecordingDb()
    counts = r10._count_gate_rows(db, "gate-r8-test", VALUE_IMPORT_GATE_COMPANY_ID)
    assert set(counts) == {
        "esg_values",
        "value_revisions",
        "value_revision_events",
        "current_value_pointers",
        "value_contexts",
    }
    assert len(db.scopes) == 6
    assert all("tenant_id" in " ".join(scope) for scope in db.scopes)


@pytest.mark.parametrize("tool", ["r10", "profile"])
def test_gate_fixture_rejects_wrong_tenant_before_database_access(
    tool, monkeypatch, tmp_path
):
    module = r10 if tool == "r10" else profile

    def fail_database_guard(_url):
        raise AssertionError("database guard reached for a non-owner tenant")

    monkeypatch.setattr(
        module, "require_disposable_value_import_target", fail_database_guard
    )
    kwargs = {
        "db_url": "not-a-database-url",
        "row_count": 1,
        "batch_size": 1,
        "report_path": tmp_path / "not-written.json",
        "tenant_id": "another-tenant",
    }
    if tool == "r10":
        kwargs["max_seconds"] = 30.0
    with pytest.raises(ValueError, match="fixture tenant"):
        if tool == "r10":
            r10.run_gate(**kwargs)
        else:
            profile.run_profile(**kwargs)
    assert not kwargs["report_path"].exists()
