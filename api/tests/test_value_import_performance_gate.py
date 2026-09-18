from __future__ import annotations

from scripts import gate_value_import_performance as gate
from src.services.value_csv_import import load_values_from_csv


def test_build_gate_csv_uses_only_public_sample_concepts(tmp_path):
    csv_path = tmp_path / "gate-values.csv"

    gate.build_gate_csv(csv_path, row_count=5, external_key_prefix="pytest-gate")

    rows = load_values_from_csv(csv_path)
    assert len(rows) == 5
    assert {row.concept for row in rows} == {
        "urn:sds:sample:water_volume",
        "urn:sds:sample:energy_use",
    }
    assert all(
        row.external_key is not None and row.external_key.startswith("pytest-gate:")
        for row in rows
    )
    assert all(row.metadata is not None and row.metadata["source"] == gate.GATE_SOURCE for row in rows)


def test_gate_rejects_missing_or_placeholder_tenant_before_database_access():
    for tenant_id in (None, "", "  ", "tenant-id", "placeholder"):
        try:
            gate._require_explicit_tenant_id(tenant_id)
        except ValueError:
            continue
        raise AssertionError(f"tenant {tenant_id!r} should be rejected")

    assert gate._require_explicit_tenant_id("sds_public_demo") == "sds_public_demo"


def test_public_gate_defaults_write_to_ignored_artifacts():
    assert "artifacts" in gate.DEFAULT_REPORT.parts
    assert "deliverables" not in gate.DEFAULT_REPORT.parts