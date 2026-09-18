from __future__ import annotations

import csv
import json
import re
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E4_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E04-api-prototype"
    / "final"
    / "e04-prototipo-api-transformacion-datos-v2026-06-09.md"
)
E4_REPORT_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e4_performance_report.txt"
)
E4_SUMMARY_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e4_performance_summary.csv"
)
E4_OPERATIONAL_REPORT_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E04-api-prototype"
    / "evidence"
    / "e4-r10-value-import-performance-report-v1-0.json"
)
E4_RAW_TRANSFORM_REPORT_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e4_raw_transform_report.json"
)
E4_RAW_TRANSFORM_MATRIX_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "e4_raw_transform_matrix.csv"
)


def test_e4_doc_exists_and_describes_current_api_prototype():
    assert E4_DOC_PATH.exists(), f"Missing E4 deliverable: {E4_DOC_PATH}"

    text = E4_DOC_PATH.read_text(encoding="utf-8")

    required_fragments = [
        "# E4 - Prototipo API y transformación de datos",
        "| Versión | V1.0 |",
        "## Superficie del prototipo",
        "## Control de validación de cierre E4 requerido",
        "## Capacidades SDS adicionales más allá del alcance mínimo de E4",
        "## Comandos de validación",
        "## Referencias",
        "libro mayor de cálculo ampliado por dimensiones",
        "Ingesta register-first",
        "API de indicadores",
        "API semántica",
        "API de valores y cálculo",
        "API de unidades y datos de referencia",
        "Controles de gobernanza",
    ]
    for fragment in required_fragments:
        assert fragment in text, f"E4 deliverable must include: {fragment}"
    assert "0.004 seconds" not in text
    assert "| Elapsed seconds | 0.004 |" not in text


def test_e4_doc_public_boundary_and_citations_are_clean():
    text = E4_DOC_PATH.read_text(encoding="utf-8")

    forbidden_patterns = [
        r"\bAtomizer\b",
        r"\batomizer\b",
        r"\bD:\\",
        r"OneDrive",
        r"workspace",
        r"current repository snapshot",
        r"moved beyond",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text), f"E4 public deliverable leaks: {pattern}"

    assert "[@ETSI_NGSI_LD_CIM_009]" in text
    assert "[@JSON_SCHEMA_2020_12]" in text
    assert "[@EU_ESRS_2023_2772]" in text


def test_e4_gate_evidence_uses_current_dimension_ledger_and_is_sanitized():
    report = E4_REPORT_PATH.read_text(encoding="utf-8")
    summary = E4_SUMMARY_PATH.read_text(encoding="utf-8")
    summary_row = next(csv.DictReader(StringIO(summary)))

    assert "Gate result: PASS" in report
    assert "Source mode: dimension-ledger" in report
    assert int(summary_row["reportable_coordinates"]) >= 1000
    assert f"Ledger groups:        {summary_row['ledger_groups']}" in report
    assert f"Reportable coordinates: {summary_row['reportable_coordinates']}" in report
    assert f"Coordinates summed:   {summary_row['coordinates_summed']}" in report
    assert "Transformed rows:" not in report
    assert "Success rate:" not in report

    for payload in (report, summary):
        assert r"D:\\" not in payload
        assert "OneDrive" not in payload
        assert "Atomizer" not in payload
        assert "atomizer" not in payload


def test_e4_doc_does_not_overclaim_dimension_ledger_as_raw_transformation():
    text = E4_DOC_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "el libro mayor dimensional no transforma observaciones brutas" in normalized
    assert "r10 no queda demostrado por `make e4-gate`" in normalized
    assert "r8 queda cerrado por la matriz ejecutada" in normalized
    assert "e4_raw_transform_matrix.csv" in normalized
    assert "r10 queda cerrado por la ejecución postgresql" in normalized
    assert "4,675" in normalized
    assert "87,547 observaciones empresariales" not in normalized


def test_e4_operational_r10_report_is_passed_and_cleanup_complete():
    report = json.loads(E4_OPERATIONAL_REPORT_PATH.read_text(encoding="utf-8"))

    assert report["passed"] is True
    assert report["failures"] == []
    assert report["row_count"] == 1000
    assert report["imported"] == 1000
    assert report["elapsed_seconds"] < report["max_seconds"] == 30.0
    for surface in (
        "esg_values",
        "value_contexts",
        "value_revisions",
        "value_revision_events",
        "current_value_pointers",
    ):
        assert report["counts"][surface] == 1000
        assert report["deleted"][surface] == 1000


def test_e4_r8_raw_transform_matrix_closes_the_explicit_denominator():
    report = json.loads(E4_RAW_TRANSFORM_REPORT_PATH.read_text(encoding="utf-8"))
    with E4_RAW_TRANSFORM_MATRIX_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert report["passed"] is True
    assert report["min_success_rate"] == 0.95
    assert report["transform_summary"] == {
        "total": 1000,
        "success": 1000,
        "error": 0,
        "success_rate": 1.0,
    }
    assert len(rows) == 1000
    assert {row["status"] for row in rows} == {"success"}
    assert {row["rule_id"] for row in rows} == {
        "water_l_to_m3",
        "energy_kwh_to_mwh",
        "emissions_kgco2e_to_tco2e",
    }
