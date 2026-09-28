from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E12_README_PATH = REPO_ROOT / "deliverables" / "E12-communications-impact" / "README.md"
E12_FINAL_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E12-communications-impact"
    / "final"
    / "e12-reporte-final-difusion-impacto-v1-0.md"
)
E12_ACTIVITY_MATRIX_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E12-communications-impact"
    / "evidence"
    / "e12-plan-execution-traceability-v1-0.csv"
)
E12_R22_REPORT_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e12_r22_activity_coverage_report.json"
)


def test_e12_readme_points_to_sanitized_final_report():
    text = E12_README_PATH.read_text(encoding="utf-8")
    assert "Status: `published-local`" in text
    assert "final/e12-reporte-final-difusion-impacto-v1-0.md" in text
    assert "R22 as demonstrated" in text


def test_e12_final_report_is_structured_and_bounds_impact_claims():
    assert E12_FINAL_PATH.exists(), f"Missing E12 final report: {E12_FINAL_PATH}"
    text = E12_FINAL_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert text.startswith("# Entregable E12")
    assert "| Versión | V1.0 |" in text
    assert "| Fecha | 2026-06-18 |" in text
    assert "## 5.1 Matriz plan–ejecución–evidencia" in text
    assert "6 envíos dentro de 1 campaña" in text
    assert "Visitas web** | No medidas" in text
    assert "R22 queda demostrado" in text
    assert "sharepoint.com" not in normalized
    assert "6 campañas" not in normalized
    assert "garantizar la continuidad" not in normalized
    assert "toda actividad prevista en E10 se ejecutara" in text


def test_e12_r22_matrix_documents_every_planned_action():
    with E12_ACTIVITY_MATRIX_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    report = json.loads(E12_R22_REPORT_PATH.read_text(encoding="utf-8"))

    assert [row["action_id"] for row in rows] == [
        *[f"A{i:02d}" for i in range(1, 17)],
        "X01",
    ]
    assert all(row["planned_action"] and row["execution_status"] for row in rows)
    assert report["passed"] is True
    assert report["summary"]["planned_actions"] == 16
    assert report["summary"]["executed_activities_not_separate_in_plan"] == 1
    assert report["summary"]["required_activity_records"] == 17
    assert report["summary"]["documented_actions"] == 17
    assert report["summary"]["documentation_coverage"] == 1.0
