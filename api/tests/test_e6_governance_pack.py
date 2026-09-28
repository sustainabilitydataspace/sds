from __future__ import annotations

from pathlib import Path

try:
    from api.tests.public_boundary import (
        assert_no_forbidden_public_references,
        assert_references_cover_citations,
        citation_keys,
    )
except ModuleNotFoundError:
    from tests.public_boundary import (
        assert_no_forbidden_public_references,
        assert_references_cover_citations,
        citation_keys,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
E6_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E06-governance"
    / "final"
    / "e06-gobernanza-politica-datos-v2026-06-09.md"
)
TRACEABILITY_MATRIX_PATH = (
    REPO_ROOT
    / "deliverables"
    / "evidence-public"
    / "pdf-first-traceability-matrix-sds-v2026-02-02.md"
)

REQUIRED_CITATIONS = {
    "DSSC_BLUEPRINT_V2_0",
    "ECLIPSE_DATASPACE_PROTOCOL_2025_1",
    "ETSI_NGSI_LD_CIM_009",
    "EU_CSRD_2022_2464",
    "EU_DATA_ACT_2023_2854",
    "EU_DGA_2022_868",
    "EU_EIDAS_2024_1183",
    "EU_ESRS_2023_2772",
    "EU_GDPR_2016_679",
    "GHG_PROTOCOL_CORPORATE_STANDARD",
    "GHG_PROTOCOL_SCOPE3_STANDARD",
    "GAIA_X_TRUST_FRAMEWORK_22_10",
    "GRI_STANDARDS_2025",
    "IDSA_DATASPACE_PROTOCOL",
    "OIDF_OPENID4VCI_2025",
    "OIDF_OPENID4VP_2025",
    "SEMIC_DCAT_AP",
    "W3C_BITSTRING_STATUS_LIST_2025",
    "W3C_DID_CORE_2022",
    "W3C_ODRL_2018",
    "W3C_VC_DATA_MODEL_2_0_2025",
}


def test_e6_governance_pack_exists_and_is_structured():
    assert E6_DOC_PATH.exists(), f"Missing E6 deliverable: {E6_DOC_PATH}"

    text = E6_DOC_PATH.read_text(encoding="utf-8")
    required_headings = [
        "# Entregable E6",
        "## 1. Resumen ejecutivo",
        "## 10. Mapeo regulatorio",
        "### 10.2 Directrices éticas y uso responsable",
        "## 11. Controles de aceptación e indicadores clave de rendimiento (E6)",
        "## Anexo A",
        "## Referencias",
    ]
    for heading in required_headings:
        assert heading in text, f"E6 must include heading: {heading}"

    for principle in (
        "No discriminación",
        "Proporcionalidad y minimización",
        "Calidad y sesgo",
        "Explicabilidad y revisión humana",
        "Reclamación y reparación",
        "Usos prohibidos",
    ):
        assert principle in text


def test_e6_governance_pack_includes_required_public_citations():
    text = E6_DOC_PATH.read_text(encoding="utf-8")
    missing = sorted(REQUIRED_CITATIONS - citation_keys(text))
    assert not missing, f"E6 must cite required keys: {missing}"
    assert_references_cover_citations(text, context="E6")


def test_e6_governance_pack_preserves_formal_snapshot_metrics():
    text = E6_DOC_PATH.read_text(encoding="utf-8")

    for expected in (
        "8 políticas",
        "4 emisores",
        "22 tipos de evento",
        "1,805 activos",
        "1,805 definiciones contractuales",
        "24 artefactos de gobernanza",
        "32 pruebas de conformidad",
        "denegación por defecto",
        "100% de las negociaciones",
        "99%",
    ):
        assert expected in text, f"E6 must include verified metric: {expected}"
    assert "28 conformance tests" not in text


def test_e6_governance_pack_covers_sds_technical_baseline_without_evolutionary_framing():
    text = E6_DOC_PATH.read_text(encoding="utf-8")

    required_terms = (
        "línea base técnica y operativa de SDS",
        "Gobernanza de evidencia de estándares",
        "admisión de evidencia de estándares",
        "Gobernanza de publicación de relaciones",
        "estándares ausentes",
        "falsas equivalencias",
        "no convertibles",
        "Gobernanza de unidades, conversiones y datos de referencia",
        "valores operativos",
        "datos de muestra",
        "G6-A13",
        "G6-A14",
        "G6-A15",
        "G6-A16",
        "Gobernanza de línea base técnica SDS",
    )
    for expected in required_terms:
        assert (
            expected in text
        ), f"E6 must govern SDS technical baseline term: {expected}"

    forbidden_evolutionary_phrases = (
        "has moved beyond",
        "now governs",
        "This is now",
        "technical developments documented",
        "Current SDS development",
        "current SDS baseline",
    )
    for forbidden in forbidden_evolutionary_phrases:
        assert (
            forbidden not in text
        ), f"E6 should be written as the active version, not evolution: {forbidden}"


def test_e6_governance_pack_avoids_internal_paths_processes_and_commands():
    text = E6_DOC_PATH.read_text(encoding="utf-8")
    assert_no_forbidden_public_references(
        text,
        context="E6",
        extra_forbidden=("docs/", "configs/"),
    )


def test_e6_traceability_matrix_row_uses_public_evidence_only():
    text = TRACEABILITY_MATRIX_PATH.read_text(encoding="utf-8")
    e6_rows = [line for line in text.splitlines() if line.startswith("| `E06` |")]
    assert len(e6_rows) == 1, "Traceability matrix must include one E06 row."

    e6_row = e6_rows[0]
    assert (
        "deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md"
        in e6_row
    )
    assert "deliverables/deliverables-register.csv" in e6_row
    assert "governance-source evidence held externally" in e6_row
    assert "historical governance-source evidence held externally" not in e6_row
    assert_no_forbidden_public_references(
        e6_row,
        context="E6 traceability row",
        extra_forbidden=("docs/", "configs/"),
    )
