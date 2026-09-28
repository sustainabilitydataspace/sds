"""The current R8 gate must not rewrite the accepted historical evidence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.value_import_gate_hierarchy import VALUE_IMPORT_GATE_COMPANY_ID

API_ROOT = Path(__file__).resolve().parents[1]


def test_r8_help_is_available_without_importing_removed_history_purge():
    result = subprocess.run(
        [
            sys.executable,
            str(API_ROOT / "scripts/gate_raw_value_transform_matrix.py"),
            "--help",
        ],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--tenant-id" in result.stdout


def test_r8_refuses_non_disposable_target_before_opening_database(
    monkeypatch, tmp_path
):
    from scripts import gate_raw_value_transform_matrix as gate

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("R8 must refuse the target before opening a connection")

    monkeypatch.setattr(gate, "_engine", unexpected_connection)
    with pytest.raises(ValueError, match="disposable"):
        gate.run_gate(
            db_url="sqlite:///:memory:",
            row_count=1,
            min_success_rate=0.95,
            batch_size=1,
            matrix_path=tmp_path / "matrix.csv",
            report_path=tmp_path / "report.json",
            tenant_id=VALUE_IMPORT_GATE_COMPANY_ID,
        )
    assert not (tmp_path / "matrix.csv").exists()


def test_r8_refuses_tenant_that_does_not_own_its_fixture(monkeypatch, tmp_path):
    from scripts import gate_raw_value_transform_matrix as gate

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("fixture tenant must be checked before opening database")

    monkeypatch.setattr(gate, "_engine", unexpected_connection)
    with pytest.raises(ValueError, match="fixture tenant"):
        gate.run_gate(
            db_url="postgresql://example",
            row_count=1,
            min_success_rate=0.95,
            batch_size=1,
            matrix_path=tmp_path / "matrix.csv",
            report_path=tmp_path / "report.json",
            tenant_id="other-tenant",
        )


def test_r8_persists_result_without_deleting_immutable_history(monkeypatch, tmp_path):
    from scripts import gate_raw_value_transform_matrix as gate

    setup_calls = []

    class FakeDb:
        def close(self):
            pass

        def delete(self, *_args):
            raise AssertionError("committed revision history cannot be deleted")

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _url: object())
    monkeypatch.setattr(gate, "sessionmaker", lambda **_kwargs: lambda: FakeDb())
    monkeypatch.setattr(
        gate, "ensure_value_import_gate_hierarchy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        gate, "bootstrap_units_if_empty", lambda _db: setup_calls.append("units")
    )

    def fake_import(**_kwargs):
        assert setup_calls == ["units"]
        return 1

    monkeypatch.setattr(gate, "import_values_csv", fake_import)
    monkeypatch.setattr(
        gate,
        "_load_actual_rows",
        lambda _db, prefix, _tenant: {
            f"{prefix}:000001": {
                "value": 1,
                "unit": "m³",
                "original_value": 1000,
                "original_unit": "L",
                "conversion_applied": True,
            }
        },
    )
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
        min_success_rate=0.95,
        batch_size=1,
        matrix_path=tmp_path / "matrix.csv",
        report_path=tmp_path / "report.json",
        tenant_id=VALUE_IMPORT_GATE_COMPANY_ID,
    )
    assert not failures
    assert report["passed"] is True
    assert report["transform_summary"]["success"] == 1
    assert report["counts"]["value_revision_events"] == 1
    assert "deleted" not in report
    assert "disposable PostgreSQL database" in report["disposal"]
    assert (tmp_path / "matrix.csv").is_file()
    assert (tmp_path / "report.json").is_file()


def test_r8_partial_import_reports_failure_without_leaking_error_or_purging(
    monkeypatch, tmp_path
):
    from scripts import gate_raw_value_transform_matrix as gate

    class FakeDb:
        def close(self):
            pass

    marker = "synthetic-sensitive-marker"

    def partial_import(**_kwargs):
        raise RuntimeError(f"committed first batch: {marker}")

    monkeypatch.setattr(
        gate, "require_disposable_value_import_target", lambda _url: None
    )
    monkeypatch.setattr(gate, "_engine", lambda _url: object())
    monkeypatch.setattr(gate, "sessionmaker", lambda **_kwargs: lambda: FakeDb())
    monkeypatch.setattr(
        gate, "ensure_value_import_gate_hierarchy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(gate, "import_values_csv", partial_import)
    monkeypatch.setattr(gate, "bootstrap_units_if_empty", lambda _db: None)
    report, failures = gate.run_gate(
        db_url="postgresql://example",
        row_count=2,
        min_success_rate=0.95,
        batch_size=1,
        matrix_path=tmp_path / "matrix.csv",
        report_path=tmp_path / "report.json",
        tenant_id=VALUE_IMPORT_GATE_COMPANY_ID,
    )
    assert failures == ["R8 operation failed: RuntimeError"]
    assert report["passed"] is False
    assert report["status"] == "not_evaluated"
    assert report["operation_error"] == "RuntimeError"
    assert "disposable PostgreSQL database" in report["disposal"]
    assert not (tmp_path / "matrix.csv").exists()
    assert marker not in (tmp_path / "report.json").read_text(encoding="utf-8")
