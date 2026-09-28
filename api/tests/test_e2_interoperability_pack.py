from __future__ import annotations

import re
from pathlib import Path

try:
    from api.tests.public_boundary import assert_no_forbidden_public_references
except ModuleNotFoundError:
    from tests.public_boundary import assert_no_forbidden_public_references

REPO_ROOT = Path(__file__).resolve().parents[2]
E2_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E02-interoperabilidad-crosswalks"
    / "final"
    / "e02-informe-interoperabilidad-correspondencias-v2026-06-09.md"
)


def test_e2_interoperability_pack_exists_and_is_structured():
    assert E2_DOC_PATH.exists(), f"Missing E2 deliverable: {E2_DOC_PATH}"

    text = E2_DOC_PATH.read_text(encoding="utf-8")
    required_headings = [
        "# E2",
        "## Evidencia pública",
        "## Criterios de aceptación del proyecto",
        "## Métricas aceptadas por tema",
        "## Modelo de línea base de relaciones",
        "## Capacidades de interoperabilidad",
        "## Ejemplos de mapeo",
        "## Casos de uso prácticos de interoperabilidad",
        "## Recomendaciones técnicas",
        "## Alcance técnico adicional",
        "## Mapeo con el control de validación de aceptación",
    ]
    for heading in required_headings:
        assert heading in text, f"E2 must include heading: {heading}"

    for case_id in ("E2-CU01", "E2-CU02", "E2-CU03"):
        assert case_id in text
    assert "ejecutable y verificado" in text
    assert "diseño controlado; piloto pendiente" in text
    assert "rechazo estructurado" in text


def test_e2_interoperability_pack_includes_topic_table_and_diagrams():
    text = E2_DOC_PATH.read_text(encoding="utf-8")

    assert re.search(
        r"^\|\s*Tema\s*\|\s*Cobertura\s*\|\s*Detección\s*\|", text, flags=re.MULTILINE
    ), "E2 must include the topic metrics table."
    for topic in ("Energía", "GEI", "Agua"):
        assert (
            topic in text
        ), f"E2 must mention {topic} in the accepted baseline metrics."

    for expected_metric in (
        "`80 / 85` (94%)",
        "`146 / 154` (95%)",
        "`154 / 154` (100%)",
        "`43 / 53` (81%)",
        "`53 / 53` (100%)",
    ):
        assert expected_metric in text

    for stale_metric in (
        "`153 / 163`",
        "`161 / 163`",
        "`56 / 70`",
        "`66 / 70`",
    ):
        assert stale_metric not in text


def test_e2_interoperability_pack_includes_public_citations():
    text = E2_DOC_PATH.read_text(encoding="utf-8")

    for citation_key in (
        "[@EU_CSRD_2022_2464]",
        "[@EU_ESRS_2023_2772]",
        "[@GRI_STANDARDS_2025]",
        "[@GHG_PROTOCOL_CORPORATE_STANDARD]",
        "[@GHG_PROTOCOL_SCOPE3_STANDARD]",
    ):
        assert citation_key in text, f"E2 must cite {citation_key}."
    assert "## Referencias" in text, "E2 must include a References section."


def test_e2_interoperability_pack_uses_active_deliverable_framing():
    text = E2_DOC_PATH.read_text(encoding="utf-8")
    required_terms = (
        "Modelo de línea base de relaciones",
        "Capacidades de interoperabilidad",
        "Alcance técnico adicional",
        "Sin equivalencia forzada",
        "pending_missing_standard",
        "Capa de equivalencia exacta del Protocolo de GEI",
    )
    for expected in required_terms:
        assert (
            expected in text
        ), f"E2 must describe active interoperability scope: {expected}"

    forbidden_phrases = (
        "Original",
        "original",
        "Current public register",
        "Current-state",
        "Current SDS",
        "Extra Beyond Original",
        "Scope Coverage Beyond Minimum Project Requirement",
        "adds a stricter rule",
        "goes further",
        "now includes",
        "historical",
        "previous",
        "evolution",
    )
    for phrase in forbidden_phrases:
        assert (
            phrase not in text
        ), f"E2 should read as active deliverable text, not history: {phrase}"


def test_e2_interoperability_pack_avoids_internal_paths_and_commands():
    text = E2_DOC_PATH.read_text(encoding="utf-8")
    assert_no_forbidden_public_references(text, context="E2")
