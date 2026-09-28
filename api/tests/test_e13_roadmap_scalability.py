from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E13_README_PATH = REPO_ROOT / "deliverables" / "E13-roadmap-scalability" / "README.md"
E13_DRAFT_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E13-roadmap-scalability"
    / "draft"
    / "e13-roadmap-scalability-draft-v2026-05-11.md"
)
E13_VERSION_1_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E13-roadmap-scalability"
    / "final"
    / "e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md"
)


def test_e13_readme_points_to_canonical_version_1():
    assert E13_README_PATH.exists(), f"Missing E13 README: {E13_README_PATH}"

    text = E13_README_PATH.read_text(encoding="utf-8")

    assert "Status: `published-local`" in text
    assert "draft/e13-roadmap-scalability-draft-v2026-05-11.md" in text
    assert (
        "final/e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md" in text
    )
    assert (
        "final/e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.docx" in text
    )
    assert "Canonical version 1.0" in text


def test_e13_draft_exists_and_is_structured():
    assert E13_DRAFT_PATH.exists(), f"Missing E13 draft: {E13_DRAFT_PATH}"

    text = E13_DRAFT_PATH.read_text(encoding="utf-8")

    required_headings = [
        "# E13 - Roadmap, Scalability, Sustainability, and Interoperability",
        "## 1. Executive Summary",
        "## 2. Current Baseline",
        "## 3. Roadmap Phases",
        "## 4. Scalability Plan",
        "## 5. Sustainability Plan",
        "## 6. Interoperability Plan",
        "## 7. Risks And Mitigations",
        "## 8. Recommended Next Actions",
        "## 9. Finalization Checklist",
    ]
    for heading in required_headings:
        assert heading in text, f"E13 draft must include heading: {heading}"


def test_e13_draft_preserves_mapping_cutover_boundary():
    text = E13_DRAFT_PATH.read_text(encoding="utf-8")

    assert (
        "Product mapping behavior is backed by reviewed relationship evidence" in text
    )
    assert "reviewed mapping" in text
    assert "active legacy fallback rows are retired" in text
    assert re.search(
        r"^\|\s*Phase\s*\|\s*Objective\s*\|\s*Key outputs\s*\|\s*Acceptance gate\s*\|",
        text,
        flags=re.MULTILINE,
    ), "E13 draft must include a phase roadmap table."


def test_e13_version_1_is_validated():
    assert E13_VERSION_1_PATH.exists(), f"Missing E13 version 1.0: {E13_VERSION_1_PATH}"

    text = E13_VERSION_1_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "Versión: V1.0",
        "Estado: Documento final",
        "R23",
        "E1",
        "E2",
        "E3",
        "E4",
        "E5",
        "E6",
        "e01-inventario-datos-clave-variables-brutas-v2026-06-09.docx",
        "e02-informe-interoperabilidad-correspondencias-v2026-06-09.docx",
        "e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.docx",
        "e04-prototipo-api-transformacion-datos-v2026-06-09.docx",
        "e05-codigo-tecnico-modelo-v2026-06-09.docx",
        "e06-gobernanza-politica-datos-v2026-06-09.docx",
    ]
    for fragment in required_fragments:
        assert fragment in text, f"E13 version 1.0 must include: {fragment}"

    assert not re.search(r"deliverables/E\d{2}-.+\.md", text)
    assert "relaciones generalmente consultables" in text
    assert re.search(
        r"^\|\s*Fase\s*\|\s*Objetivo\s*\|\s*Actores tipo\s*\|\s*Salidas\s*\|\s*Criterio de avance\s*\|",
        text,
        flags=re.MULTILINE,
    ), "E13 candidate must include a roadmap table."


def test_e13_r23_gate_covers_the_complete_approved_topic_universe():
    text = E13_VERSION_1_PATH.read_text(encoding="utf-8")
    matrix_match = re.search(
        r"^## 16\. Índice aprobado de cobertura R23\s*$" r"(?P<body>.*?)^## 17\.",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert matrix_match, "E13 must contain the approved R23 coverage matrix"

    rows = re.findall(
        r"^\|\s*(?P<topic>[^|]+?)\s*\|\s*(?P<sections>[^|]+?)\s*\|"
        r"\s*(?P<evidence>[^|]+?)\s*\|$",
        matrix_match.group("body"),
        flags=re.MULTILINE,
    )
    topic_rows = {
        topic.strip(): (sections.strip(), evidence.strip())
        for topic, sections, evidence in rows
        if topic.strip() not in {"Tema R23/E13", "---"}
    }
    assert set(topic_rows) == {
        "Escalabilidad",
        "Sostenibilidad",
        "Interoperabilidad",
        "Recomendaciones técnicas",
        "Recomendaciones legales",
        "Recomendaciones estratégicas",
        "Oportunidades de expansión",
        "Adaptación a nuevos desafíos",
        "Guía para futuros participantes",
    }
    assert all(sections and evidence for sections, evidence in topic_rows.values())

    for section_number in (8, 9, 10):
        section = re.search(
            rf"^## {section_number}\. .*?(?=^## {section_number + 1}\.)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert section, f"Missing R23 core section {section_number}"
        normalized = section.group(0).casefold()
        assert "conclusión" in normalized
        assert "recomendaciones" in normalized
        analysis_body = normalized.split("conclusión", maxsplit=1)[0]
        assert len(analysis_body) >= 500
        assert analysis_body.count("###") >= 2
