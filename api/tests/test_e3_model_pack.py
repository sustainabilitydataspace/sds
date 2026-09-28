from __future__ import annotations

import csv
import json
from pathlib import Path

try:
    from api.tests.public_boundary import assert_no_forbidden_public_references
except ModuleNotFoundError:
    from tests.public_boundary import assert_no_forbidden_public_references

REPO_ROOT = Path(__file__).resolve().parents[2]
E3_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "final"
    / "e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md"
)
TRACEABILITY_MATRIX_PATH = (
    REPO_ROOT
    / "deliverables"
    / "evidence-public"
    / "pdf-first-traceability-matrix-sds-v2026-02-02.md"
)
E3_SUMMARY_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e3_model_representation_summary_v2026-05-14.csv"
)
E3_R5_REPORT_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "evidence"
    / "e3-r5-hierarchy-report-v1-0.json"
)
E3_R7_REPORT_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "evidence"
    / "e3-r7-functional-pilot-report-v1-0.json"
)


def _summary_metrics() -> dict[str, str]:
    with E3_SUMMARY_PATH.open(newline="", encoding="utf-8") as handle:
        return {
            row["metric"]: row["value"]
            for row in csv.DictReader(handle)
            if row.get("metric")
        }


def _format_int(value: str) -> str:
    return f"{int(value):,}"


def test_e3_model_pack_exists_and_is_structured():
    assert E3_DOC_PATH.exists(), f"Missing E3 deliverable: {E3_DOC_PATH}"

    text = E3_DOC_PATH.read_text(encoding="utf-8")
    required_headings = [
        "# E3",
        "## Fuentes públicas del modelo",
        "## Vista del modelo",
        "## Diagrama y jerarquía del modelo",
        "## Matriz contractual R5-R7",
        "## Evidencia generada",
        "## Capacidades del modelo semántico",
        "## Estado de validación",
        "## Referencias",
    ]
    for heading in required_headings:
        assert heading in text, f"E3 must include heading: {heading}"

    assert "cobertura de serialización" in text
    assert "no mide por sí sola la precisión semántica" in text
    assert "Cobertura y organización R5 | Demostrado" in text
    assert "Piloto funcional R7 | Demostrado" in text


def test_e3_r5_r7_reports_meet_contractual_thresholds():
    r5 = json.loads(E3_R5_REPORT_PATH.read_text(encoding="utf-8"))
    r7 = json.loads(E3_R7_REPORT_PATH.read_text(encoding="utf-8"))

    assert r5["passed"] is True
    assert r5["summary"]["represented_variables"] == 1805
    assert r5["summary"]["hierarchy_valid_variables"] == 1805
    assert r5["summary"]["representation_rate"] >= r5["threshold"] == 0.8
    assert r5["summary"]["hierarchy_rate"] >= r5["threshold"]
    assert r7["passed"] is True
    assert r7["summary"] == {
        "pilot_rows": 100,
        "success": 100,
        "error": 0,
        "accuracy": 1.0,
    }
    assert r7["summary"]["accuracy"] >= r7["threshold"] == 0.9


def test_e3_model_pack_includes_public_citations_and_references():
    text = E3_DOC_PATH.read_text(encoding="utf-8")

    for citation_key in (
        "[@ETSI_NGSI_LD_CIM_009]",
        "[@W3C_JSON_LD_1_1]",
        "[@W3C_SHACL_2017]",
        "[@JSON_SCHEMA_2020_12]",
        "[@W3C_OWL2_2012]",
        "[@GHG_PROTOCOL_CORPORATE_STANDARD]",
        "[@GHG_PROTOCOL_SCOPE3_STANDARD]",
    ):
        assert citation_key in text, f"E3 must cite {citation_key}."
    assert "## Referencias" in text, "E3 must include a References section."


def test_e3_model_pack_records_verified_current_metrics():
    text = E3_DOC_PATH.read_text(encoding="utf-8")
    metrics = _summary_metrics()

    for expected in (
        (
            f"{_format_int(metrics['ngsi_ld_dataset_entities'])} / "
            f"{_format_int(metrics['delivery_register_rows'])}"
        ),
        _format_int(metrics["ngsi_ld_total_entities"]),
        f"Archivos del paquete semántico | {metrics['semantics_bundle_files']}",
        f"{metrics['semantics_manifest_hashes']} resúmenes",
        f"Tripletas de la ontología central | {_format_int(metrics['ontology_core_triples'])}",
        (
            "Tripletas de la proyección generada | "
            f"{_format_int(metrics['ontology_projection_triples'])}"
        ),
        (
            "Tripletas de la ontología fusionada en el entorno de ejecución | "
            f"{_format_int(metrics['ontology_runtime_merged_triples'])}"
        ),
        (
            "Conceptos semánticos respaldados por base de datos cubiertos | "
            f"{_format_int(metrics['projected_semantic_concepts'])}"
        ),
        (
            "Conceptos de variables proyectados | "
            f"{_format_int(metrics['projected_variable_concepts'])}"
        ),
        (
            "Otros conceptos proyectados que no son divulgaciones | "
            f"{_format_int(metrics['projected_non_disclosure_concepts'])}"
        ),
        f"Fórmulas proyectadas | {_format_int(metrics['projected_formulas'])}",
    ):
        assert expected in text, f"E3 must include verified metric: {expected}"

    assert (
        "Conceptos semánticos respaldados por base de datos cubiertos | 284" not in text
    )
    assert "Tripletas de la proyección generada | 1,169" not in text


def test_e3_model_pack_uses_active_deliverable_framing():
    text = E3_DOC_PATH.read_text(encoding="utf-8")
    required_terms = (
        "Capacidades del modelo semántico",
        "Soporte de metadatos de relaciones",
        "Soporte de metadatos de unidades y cálculo",
        "Soporte de metadatos de políticas",
        "Mecanismo de extensión de normas",
    )
    for expected in required_terms:
        assert (
            expected in text
        ), f"E3 must describe active common-model scope: {expected}"

    forbidden_phrases = (
        "original",
        "Original",
        "Added Technical Depth",
        "Added depth beyond",
        "beyond the original",
        "on top of",
        "now includes",
        "historical",
        "previous",
        "evolution",
    )
    for phrase in forbidden_phrases:
        assert (
            phrase not in text
        ), f"E3 should read as active deliverable text, not history: {phrase}"


def test_e3_model_pack_avoids_internal_paths_processes_and_commands():
    text = E3_DOC_PATH.read_text(encoding="utf-8")
    assert_no_forbidden_public_references(text, context="E3")


def test_e3_traceability_matrix_row_uses_public_evidence_only():
    text = TRACEABILITY_MATRIX_PATH.read_text(encoding="utf-8")
    e3_rows = [line for line in text.splitlines() if line.startswith("| `E03` |")]
    assert len(e3_rows) == 1, "Traceability matrix must include one E03 row."

    e3_row = e3_rows[0]
    assert (
        "deliverables/E03-modelo-ngsi-ld/final/e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md"
        in e3_row
    )
    assert "deliverables/deliverables-register.csv" in e3_row
    assert_no_forbidden_public_references(e3_row, context="E3 traceability row")
