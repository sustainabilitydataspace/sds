from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E9_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E09-adjustments"
    / "final"
    / "e09-adjustments-report-v2026-02-03.md"
)
E7_FEEDBACK_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E07-workshops"
    / "evidence"
    / "e07-feedback-traceability-v1-0.csv"
)


def test_e9_adjustments_report_exists_and_is_structured():
    assert E9_DOC_PATH.exists(), f"Missing E9 adjustments report: {E9_DOC_PATH}"

    text = E9_DOC_PATH.read_text(encoding="utf-8")
    assert "# Entregable E9" in text
    assert "Ajustes al marco de gobernanza y diseño técnico" in text
    assert "## 1. Resumen ejecutivo" in text
    assert "## 2. Alcance del entregable" in text
    assert "## 5. Criterios de ajuste" in text
    assert "## 6. Matriz de ajustes" in text
    assert "### 6.1 Trazabilidad de feedback, casos y ajustes" in text
    assert "## 10. Relación con R18 y R19" in text
    assert "## 11. Riesgos y controles pendientes" in text
    assert "## 13. Conclusiones" in text

    assert "| A1 |" in text
    assert "| A10 |" in text
    for feedback_id in (
        "F01",
        "F02",
        "F03",
        "F04",
        "F05",
        "F06",
        "F07",
        "F08",
        "F09",
        "F10",
    ):
        assert f"| {feedback_id} |" in text
    assert "R18 - Justificación de los ajustes" in text
    assert "R19 - Consistencia estratégica" in text
    assert "no debe emitir decisiones externas" in text
    assert "sin crear equivalencias falsas" in text
    assert "| Versión | V1.0 |" in text
    assert "| Fecha | 2026-06-18 |" in text


def test_e7_feedback_matrix_traces_every_row_to_e8_and_e9():
    with E7_FEEDBACK_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["feedback_id"] for row in rows] == [
        f"F{index:02d}" for index in range(1, 11)
    ]
    assert all(row["e8_case"].strip() for row in rows)
    assert all(row["e9_adjustment"].strip() for row in rows)
    assert all(row["evidence_boundary"].strip() for row in rows)
