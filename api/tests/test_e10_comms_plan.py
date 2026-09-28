from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E10_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E10-communications-plan"
    / "final"
    / "e10-communications-plan-v2026-02-03.md"
)


def test_e10_comms_plan_exists_and_is_structured():
    assert E10_DOC_PATH.exists(), f"Missing E10 comms plan: {E10_DOC_PATH}"

    text = E10_DOC_PATH.read_text(encoding="utf-8")
    required_headings = [
        "# Entregable E10",
        "## 2. Objetivos de comunicación",
        "## 3. Audiencias objetivo y mapa de stakeholders",
        "## 5. Estrategia de canales",
        "## 6. Cronograma de comunicación",
        "## 7. Gobernanza de la comunicación y modelo operativo",
        "## 8. Métricas de éxito y seguimiento",
        "## 10. Cumplimiento de requisitos y validación",
    ]
    for heading in required_headings:
        assert heading in text, f"E10 must include heading: {heading}"

    assert "| Versión | V1.0 |" in text
    assert "| Fecha | 2026-06-18 |" in text
    assert (
        "El cierre vigente de proyecto, registrado el 2026-09-18, confirma la aceptación de R20"
        in text
    )
    assert "actividades previstas" in text
    assert "actividades ejecutadas" in text


def test_e10_records_owner_accepted_closure_without_inventing_executed_actions():
    text = E10_DOC_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "se aprueba antes de que comiencen" not in normalized
    assert (
        "el cierre vigente de proyecto confirma la validación requerida para r20"
        in normalized
    )
    assert "no se infieren actividades ejecutadas a partir del cronograma" in normalized
    assert "criterio temporal de r20" not in normalized
    assert "e12" in normalized
