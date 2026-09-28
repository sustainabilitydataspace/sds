"""Tests for the Wave 4 value-isolation gate script."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "api" / "scripts" / "gate_wave4_value_isolation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gate_wave4_value_isolation_script", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate_wave4 = _load_module()


class _FakeDeleteQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def delete(self):
        return 2


class _FakeSession:
    def __init__(self):
        self.committed = False
        self.closed = False

    def query(self, *_args, **_kwargs):
        return _FakeDeleteQuery()

    def commit(self):
        self.committed = True

    def expire_all(self):
        return None

    def close(self):
        self.closed = True


def test_wave4_gate_passes_when_semantic_surface_is_stable(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    surfaces = iter(
        [
            {"overall_digest": "same", "tables": {"indicators": {"digest": "a"}}},
            {"overall_digest": "same", "tables": {"indicators": {"digest": "a"}}},
        ]
    )

    monkeypatch.setattr(gate_wave4, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(gate_wave4, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        gate_wave4, "sessionmaker", lambda **_kwargs: (lambda: fake_session)
    )
    monkeypatch.setattr(
        gate_wave4,
        "ensure_value_import_gate_hierarchy",
        lambda _db, *, created_by: {"hierarchy_status": "unchanged"},
    )
    monkeypatch.setattr(
        gate_wave4, "capture_semantic_surface", lambda _db: next(surfaces)
    )
    monkeypatch.setattr(gate_wave4, "import_values_csv", lambda **_kwargs: 2)

    report, failures = gate_wave4.run_gate(
        db_url="postgresql://example",
        report_path=tmp_path / "wave4.json",
    )

    assert failures == []
    assert report["csv_contract_rejects_semantic_headers"] is True
    assert report["imported_values"] == 2
    assert fake_session.committed is True


def test_wave4_gate_fails_when_semantic_surface_changes(monkeypatch, tmp_path):
    fake_session = _FakeSession()
    surfaces = iter(
        [
            {"overall_digest": "before", "tables": {"indicators": {"digest": "a"}}},
            {"overall_digest": "after", "tables": {"indicators": {"digest": "b"}}},
        ]
    )

    monkeypatch.setattr(gate_wave4, "create_engine", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(gate_wave4, "init_db_for_engine", lambda _engine: None)
    monkeypatch.setattr(
        gate_wave4, "sessionmaker", lambda **_kwargs: (lambda: fake_session)
    )
    monkeypatch.setattr(
        gate_wave4,
        "ensure_value_import_gate_hierarchy",
        lambda _db, *, created_by: {"hierarchy_status": "unchanged"},
    )
    monkeypatch.setattr(
        gate_wave4, "capture_semantic_surface", lambda _db: next(surfaces)
    )
    monkeypatch.setattr(gate_wave4, "import_values_csv", lambda **_kwargs: 2)

    _report, failures = gate_wave4.run_gate(
        db_url="postgresql://example",
        report_path=tmp_path / "wave4.json",
    )

    assert (
        "Semantic/catalog/reference surface changed after value CSV import." in failures
    )
    assert "Protected table changed during value import: indicators" in failures
