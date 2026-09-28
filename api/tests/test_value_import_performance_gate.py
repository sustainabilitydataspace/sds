from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from scripts import gate_value_import_performance as gate
from scripts import profile_value_import as profile
from scripts import value_import_db_probe as db_probe
from src.services.value_csv_import import load_values_from_csv


def test_build_gate_csv_creates_unique_strict_value_rows(tmp_path):
    csv_path = tmp_path / "gate-values.csv"

    gate.build_gate_csv(csv_path, row_count=5, external_key_prefix="pytest-gate")

    rows = load_values_from_csv(csv_path)
    external_keys = [row.external_key for row in rows]
    assert len(rows) == 5
    assert len(set(external_keys)) == 5
    assert external_keys[0] == "pytest-gate:000001"
    assert rows[0].concept == "urn:sds:reg:esrs:e3_4_01"
    assert rows[1].concept == "urn:sds:reg:esrs:e3_4_02"
    assert all(not row.concept.startswith("syg:") for row in rows)
    assert rows[0].entity == gate.VALUE_IMPORT_GATE_ENTITY
    assert rows[0].metadata["source"] == "value_import_performance_gate"


def test_profile_csv_uses_current_gate_concepts_not_retired_syg_aliases(tmp_path):
    csv_path = tmp_path / "profile-values.csv"
    profile.build_profile_csv(
        csv_path, row_count=2, external_key_prefix="pytest-profile"
    )
    rows = load_values_from_csv(csv_path)
    assert [row.concept for row in rows] == list(gate.VALUE_IMPORT_GATE_CONCEPTS)
    assert all(not row.concept.startswith("syg:") for row in rows)


def test_gate_default_report_cannot_overwrite_public_historical_evidence():
    assert gate.DEFAULT_REPORT.is_relative_to(gate.REPO_ROOT / ".local_artifacts")


def test_make_performance_gate_passes_the_synthetic_fixture_tenant():
    recipe = (gate.API_ROOT / "Makefile").read_text(encoding="utf-8")
    assert (
        "scripts/gate_value_import_performance.py --tenant-id value_import_gate"
        in recipe
    )


def test_value_import_tools_refuse_unattested_database_before_any_connection(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SDS_VALUE_IMPORT_DISPOSABLE_DATABASE_URL", raising=False)
    monkeypatch.delenv("SDS_VALUE_IMPORT_DISPOSABLE_ALLOW_WRITE", raising=False)

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("unattested target must not be opened")

    monkeypatch.setattr(gate, "_engine", unexpected_connection)
    monkeypatch.setattr(profile, "probe_database", unexpected_connection)
    with pytest.raises(ValueError, match="disposable"):
        gate.run_gate(
            db_url="sqlite:///:memory:",
            row_count=1,
            max_seconds=30,
            batch_size=1,
            report_path=tmp_path / "gate.json",
            tenant_id=gate.VALUE_IMPORT_GATE_COMPANY_ID,
        )
    with pytest.raises(ValueError, match="disposable"):
        profile.run_profile(
            db_url="sqlite:///:memory:",
            row_count=1,
            batch_size=1,
            report_path=tmp_path / "profile.json",
            tenant_id=profile.VALUE_IMPORT_GATE_COMPANY_ID,
        )


def test_gate_never_deletes_committed_revision_history(monkeypatch, tmp_path):
    class FakeDb:
        def close(self):
            pass

    def forbidden_purge(*_args):
        raise AssertionError("immutable revision history must not be purged")

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _url: object())
    monkeypatch.setattr(gate, "sessionmaker", lambda **_kwargs: FakeDb)
    monkeypatch.setattr(
        gate, "ensure_value_import_gate_hierarchy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(gate, "_purge_gate_rows", forbidden_purge, raising=False)
    monkeypatch.setattr(gate, "import_values_csv", lambda **_kwargs: 1)
    monkeypatch.setattr(
        gate,
        "_count_gate_rows",
        lambda *_args: {
            "esg_values": 1,
            "value_revisions": 1,
            "value_revision_events": 1,
            "current_value_pointers": 1,
            "value_contexts": 1,
        },
    )
    report, failures = gate.run_gate(
        db_url="postgresql://example",
        row_count=1,
        max_seconds=30,
        batch_size=1,
        report_path=tmp_path / "gate.json",
        tenant_id=gate.VALUE_IMPORT_GATE_COMPANY_ID,
    )
    assert not failures
    assert report["counts"]["value_revision_events"] == 1
    assert "deleted" not in report


def test_run_gate_bootstraps_gate_hierarchy(monkeypatch, tmp_path):
    hierarchy_calls = []

    class FakeDb:
        def close(self):
            return None

    def fake_hierarchy(db, *, created_by):
        hierarchy_calls.append((db, created_by))
        return {"hierarchy_status": "unchanged"}

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _db_url: object())
    monkeypatch.setattr(
        gate,
        "sessionmaker",
        lambda **_kwargs: lambda: FakeDb(),
    )
    monkeypatch.setattr(gate, "ensure_value_import_gate_hierarchy", fake_hierarchy)
    monkeypatch.setattr(
        gate,
        "_count_gate_rows",
        lambda _db, _prefix, _tenant: {
            "esg_values": 3,
            "value_revisions": 3,
            "value_revision_events": 3,
            "current_value_pointers": 3,
            "value_contexts": 3,
        },
    )
    monkeypatch.setattr(gate, "import_values_csv", lambda **_kwargs: 3)

    report, failures = gate.run_gate(
        db_url="postgresql://example",
        row_count=3,
        max_seconds=30,
        batch_size=250,
        report_path=tmp_path / "report.json",
        tenant_id=gate.VALUE_IMPORT_GATE_COMPANY_ID,
    )

    assert hierarchy_calls
    assert hierarchy_calls[0][1] == gate.GATE_SOURCE
    assert not failures
    assert report["imported"] == 3


def test_run_gate_requires_explicit_tenant_before_engine(monkeypatch, tmp_path):
    def fail_engine(*_args, **_kwargs):
        raise AssertionError("engine should not be created without tenant_id")

    monkeypatch.setattr(gate, "_engine", fail_engine)

    for tenant_id in (None, "", "  ", "tenant-id", "placeholder"):
        with pytest.raises(ValueError, match="tenant_id"):
            gate.run_gate(
                db_url="postgresql://example",
                row_count=3,
                max_seconds=30,
                batch_size=250,
                report_path=tmp_path / "report.json",
                tenant_id=tenant_id,
            )


def test_run_gate_passes_explicit_tenant_to_import(monkeypatch, tmp_path):
    import_calls = []

    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _db_url: object())
    monkeypatch.setattr(
        gate,
        "sessionmaker",
        lambda **_kwargs: lambda: FakeDb(),
    )
    monkeypatch.setattr(
        gate,
        "ensure_value_import_gate_hierarchy",
        lambda _db, *, created_by: {"hierarchy_status": "unchanged"},
    )
    monkeypatch.setattr(
        gate,
        "_count_gate_rows",
        lambda _db, _prefix, _tenant: {
            "esg_values": 3,
            "value_revisions": 3,
            "value_revision_events": 3,
            "current_value_pointers": 3,
            "value_contexts": 3,
        },
    )

    def fake_import_values_csv(**kwargs):
        import_calls.append(kwargs)
        return 3

    monkeypatch.setattr(gate, "import_values_csv", fake_import_values_csv)

    report, failures = gate.run_gate(
        db_url="postgresql://example",
        row_count=3,
        max_seconds=30,
        batch_size=250,
        report_path=tmp_path / "report.json",
        tenant_id=gate.VALUE_IMPORT_GATE_COMPANY_ID,
    )

    assert not failures
    assert report["imported"] == 3
    assert import_calls
    assert import_calls[0]["tenant_id"] == gate.VALUE_IMPORT_GATE_COMPANY_ID


def test_run_gate_reports_partial_import_for_database_disposal_without_deleting_history(
    monkeypatch, tmp_path
):
    """Failed committed batches remain immutable until the test DB is discarded."""
    benchmark_rows = set()
    sensitive_marker = "synthetic-sensitive-marker"

    class FakeDb:
        def close(self):
            return None

    def partial_import(**kwargs):
        prefix = next(
            row.external_key.rsplit(":", 1)[0]
            for row in load_values_from_csv(kwargs["csv_path"])
        )
        benchmark_rows.update({f"{prefix}:000001", f"{prefix}:000002"})
        raise RuntimeError(
            f"simulated failure after a committed benchmark batch {sensitive_marker}"
        )

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _db_url: object())
    monkeypatch.setattr(gate, "sessionmaker", lambda **_kwargs: lambda: FakeDb())
    monkeypatch.setattr(
        gate,
        "ensure_value_import_gate_hierarchy",
        lambda _db, *, created_by: {"hierarchy_status": "unchanged"},
    )
    monkeypatch.setattr(gate, "import_values_csv", partial_import)

    report, failures = gate.run_gate(
        db_url="postgresql://example",
        row_count=3,
        max_seconds=30,
        batch_size=250,
        report_path=tmp_path / "report.json",
        tenant_id=gate.VALUE_IMPORT_GATE_COMPANY_ID,
    )

    assert len(benchmark_rows) == 2
    assert "deleted" not in report
    assert "disposable PostgreSQL database" in report["disposal"]
    assert sensitive_marker not in (tmp_path / "report.json").read_text(
        encoding="utf-8"
    )
    assert report["operation_error"] == "RuntimeError"
    assert failures == ["benchmark operation failed: RuntimeError"]


def test_gate_main_requires_tenant_before_engine(monkeypatch, tmp_path, capsys):
    report_path = tmp_path / "gate-report.json"

    def fail_engine(*_args, **_kwargs):
        raise AssertionError("engine should not be created without tenant_id")

    monkeypatch.setattr(gate, "_engine", fail_engine)
    monkeypatch.setattr(sys, "argv", ["gate", "--report", str(report_path)])

    exit_code = gate.main()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 1
    assert "tenant_id" in output


def test_gate_main_reports_database_unavailable_without_traceback(
    monkeypatch, tmp_path, capsys
):
    report_path = tmp_path / "gate-report.json"

    def unavailable(**_kwargs):
        raise gate.DatabaseUnavailable(
            "PostgreSQL unavailable; value-import performance was not evaluated. "
            "Check DATABASE_URL or start the SDS database (localhost:55432/sds)."
        )

    monkeypatch.setattr(gate, "run_gate", unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gate",
            "--report",
            str(report_path),
            "--tenant-id",
            "tenant-gate",
            "--db-url",
            "sqlite://",
        ],
    )

    exit_code = gate.main()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == gate.DATABASE_UNAVAILABLE_EXIT_CODE
    assert "Traceback" not in output
    assert "status: not_evaluated" in output
    assert "PostgreSQL unavailable" in output
    assert payload["status"] == "not_evaluated"
    assert payload["failure_kind"] == "database_unavailable"


def test_profile_main_reports_database_unavailable_without_traceback(
    monkeypatch, tmp_path, capsys
):
    report_path = tmp_path / "profile-report.json"

    def unavailable(**_kwargs):
        raise profile.DatabaseUnavailable(
            "PostgreSQL unavailable; value-import profiling was not evaluated. "
            "Check DATABASE_URL or start the SDS database (localhost:55432/sds)."
        )

    monkeypatch.setattr(profile, "run_profile", unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile",
            "--report",
            str(report_path),
            "--tenant-id",
            "tenant-profile",
            "--db-url",
            "sqlite://",
        ],
    )

    exit_code = profile.main()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == profile.DATABASE_UNAVAILABLE_EXIT_CODE
    assert "Traceback" not in output
    assert "status: not_evaluated" in output
    assert "PostgreSQL unavailable" in output
    assert payload["status"] == "not_evaluated"
    assert payload["failure_kind"] == "database_unavailable"


def test_profile_main_requires_tenant_before_database_probe(
    monkeypatch, tmp_path, capsys
):
    report_path = tmp_path / "profile-report.json"

    def fail_probe(*_args, **_kwargs):
        raise AssertionError("database probe should not run without tenant_id")

    monkeypatch.setattr(profile, "probe_database", fail_probe)
    monkeypatch.setattr(sys, "argv", ["profile", "--report", str(report_path)])

    exit_code = profile.main()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 1
    assert "tenant_id" in output


def test_run_profile_requires_explicit_tenant_before_database_probe(
    monkeypatch, tmp_path
):
    def fail_probe(*_args, **_kwargs):
        raise AssertionError("database probe should not run without tenant_id")

    def fail_create_engine(*_args, **_kwargs):
        raise AssertionError("engine should not be created without tenant_id")

    def fail_build_conversion_engine(*_args, **_kwargs):
        raise AssertionError("conversion engine should not be built without tenant_id")

    monkeypatch.setattr(profile, "probe_database", fail_probe)
    monkeypatch.setattr(profile, "create_engine", fail_create_engine)
    monkeypatch.setattr(
        profile,
        "build_conversion_engine",
        fail_build_conversion_engine,
        raising=False,
    )

    for tenant_id in (None, "", "  ", "tenant-id", "placeholder"):
        with pytest.raises(ValueError, match="tenant_id"):
            profile.run_profile(
                db_url="postgresql://example",
                row_count=3,
                batch_size=2,
                report_path=tmp_path / "profile.json",
                tenant_id=tenant_id,
            )


def test_probe_database_translates_operational_error_and_sanitizes_endpoint(
    monkeypatch,
):
    disposed = []

    class FakeEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(
        db_probe, "create_engine", lambda *_args, **_kwargs: FakeEngine()
    )

    with pytest.raises(db_probe.DatabaseUnavailable) as error:
        db_probe.probe_database(
            "postgres" + "ql://sds:***@localhost:55432/sds",
            operation_label="value-import performance",
        )

    message = str(error.value)
    assert "PostgreSQL unavailable" in message
    assert "localhost:55432/sds" in message
    assert "secret" not in message
    assert disposed


def test_disposable_probe_rejects_non_postgresql_15_before_schema_changes(monkeypatch):
    disposed = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            return SimpleNamespace(scalar_one=lambda: "140000")

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(
        db_probe, "create_engine", lambda *_args, **_kwargs: FakeEngine()
    )
    with pytest.raises(db_probe.DatabaseUnavailable, match="PostgreSQL 15"):
        db_probe.probe_database(
            "sqlite:///:memory:",
            operation_label="value-import performance",
            require_pg15=True,
        )
    assert disposed


def test_profile_uses_non_refreshing_bulk_save(monkeypatch, tmp_path):
    save_calls = []
    value_store_tenant_ids = []
    prepare_company_ids = []
    factory_calls = []
    prepare_engines = []
    db_sessions = []
    converter = object()
    conversion_engine = object()

    class FakeDb:
        def close(self):
            return None

    class FakeValueStore:
        def __init__(self, _db, *, tenant_id):
            value_store_tenant_ids.append(tenant_id)
            return None

        def save(self, **kwargs):
            save_calls.append(kwargs)

    monkeypatch.setattr(profile, "probe_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        profile, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(profile, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(profile, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        profile,
        "attach_query_counter",
        lambda _engine: SimpleNamespace(total=0, by_category={}, by_statement={}),
    )

    def fake_session_factory():
        db = FakeDb()
        db_sessions.append(db)
        return db

    monkeypatch.setattr(profile, "sessionmaker", lambda **_kwargs: fake_session_factory)
    monkeypatch.setattr(
        profile,
        "ensure_value_import_gate_hierarchy",
        lambda _db, *, created_by: {"hierarchy_status": "unchanged"},
    )

    def forbidden_purge(*_args):
        raise AssertionError("immutable revision history must not be purged")

    monkeypatch.setattr(profile, "_purge_profile_rows", forbidden_purge, raising=False)
    count_results = iter(
        [
            {
                "esg_values": 3,
                "value_revisions": 3,
                "value_revision_events": 3,
                "current_value_pointers": 3,
                "value_contexts": 3,
            }
        ]
    )
    monkeypatch.setattr(
        profile,
        "_count_profile_rows",
        lambda _db, _prefix: next(count_results),
    )
    monkeypatch.setattr(
        profile,
        "load_values_from_csv",
        lambda *_args, **_kwargs: [object(), object(), object()],
    )
    monkeypatch.setattr(profile, "load_ontology_graph", lambda: (object(), None))
    monkeypatch.setattr(profile, "DatabaseHierarchyStore", lambda _db: object())
    monkeypatch.setattr(profile, "IndicatorStore", lambda db: object())
    monkeypatch.setattr(
        profile,
        "UnitConverter",
        lambda db_session: converter,
    )
    monkeypatch.setattr(profile, "DatabaseValueStore", FakeValueStore)

    def fake_build_conversion_engine(db, unit_converter):
        factory_calls.append((db, unit_converter))
        return conversion_engine

    monkeypatch.setattr(
        profile,
        "build_conversion_engine",
        fake_build_conversion_engine,
    )

    monkeypatch.setattr(
        profile,
        "prepare_value_records",
        lambda rows, **kwargs: (
            prepare_company_ids.append(kwargs["company_id"])
            or prepare_engines.append(kwargs["conversion_engine"])
            or [object() for _value_id, _row in rows]
        ),
    )

    report = profile.run_profile(
        db_url="postgresql://example",
        row_count=3,
        batch_size=2,
        report_path=tmp_path / "profile.json",
        tenant_id=profile.VALUE_IMPORT_GATE_COMPANY_ID,
    )

    assert report["imported"] == 3
    assert report["refresh"]["explicit_refresh_count"] == 0
    assert report["row_outcomes"] == {
        "inserted": 3,
        "updated": 0,
        "unchanged": 0,
        "rejected": 0,
    }
    assert report["counts"]["esg_values"] == 3
    assert "deleted" not in report
    assert "memory_peak_bytes" in report
    assert value_store_tenant_ids == [profile.VALUE_IMPORT_GATE_COMPANY_ID]
    assert prepare_company_ids == [profile.VALUE_IMPORT_GATE_COMPANY_ID] * 2
    assert factory_calls == [(db_sessions[1], converter)]
    assert prepare_engines == [conversion_engine, conversion_engine]
    assert save_calls
    assert all(call["refresh"] is False for call in save_calls)
    assert all(call["return_responses"] is False for call in save_calls)
