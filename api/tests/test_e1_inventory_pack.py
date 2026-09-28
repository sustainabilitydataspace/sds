from __future__ import annotations

import csv
from pathlib import Path

import pytest

try:
    from api.tests.public_boundary import assert_no_forbidden_public_references
except ModuleNotFoundError:
    from tests.public_boundary import assert_no_forbidden_public_references

pytestmark = pytest.mark.docs_only

REPO_ROOT = Path(__file__).resolve().parents[2]
E1_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "final"
    / "e01-key-data-inventory-v2026-02-03.md"
)
E1_DIMENSION_ANNEX_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "dimension-expanded-value-coordinate-annex-v2026-06-05.md"
)
E1_DIMENSION_LEDGER_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "dimension-expanded-value-coordinate-calculation-v2026-06-05.csv"
)
ACCEPTANCE_GATES_PATH = REPO_ROOT / "docs" / "quality" / "acceptance_gates.md"
SPANISH_E1_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "final"
    / "e01-inventario-datos-clave-variables-brutas-v2026-06-09.md"
)
SPANISH_E1_CSV_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "e01-inventario-variables-clave-v2026-06-12.csv"
)

# Load-bearing E1 figures pinned to code via the dimension ledger and the
# English inventory pack. The localized Spanish deliverable is published from
# the same baseline and must carry the identical numbers; this guards against
# silent translation drift (the Spanish file is otherwise not gate-validated).
E1_PINNED_FIGURES = (
    "1,200",
    "99 / 99",
    "3,964",
    "573 / 573",
    "18 / 18",
    "1,242",
    "1,249",
    "3,666",
    "4,252",
    "113",
    "317",
    "5,021",
    "5,818",
    "82,858",
    "4,399",
    "290",
    "87,547",
    "88,397",
)


def _read_e1_acceptance_gate_section() -> str:
    text = ACCEPTANCE_GATES_PATH.read_text(encoding="utf-8")
    start = text.index("## E1 ")
    end = text.index("\n## E2 ", start)
    return text[start:end]


def test_e1_inventory_pack_exists_and_is_structured():
    assert E1_DOC_PATH.exists(), f"Missing E1 deliverable: {E1_DOC_PATH}"

    text = E1_DOC_PATH.read_text(encoding="utf-8")
    required_headings = [
        "# E1",
        "## Public Evidence",
        "## Project Acceptance Criteria",
        "## Official-Standard Technical Baseline",
        "### Dimension-Expanded Value Coordinate Interpretation",
        "## Technical Readiness KPIs",
        "## Evidence Boundaries",
        "## Additional Technical Scope",
        "## Acceptance Gate Mapping",
    ]
    for heading in required_headings:
        assert heading in text, f"E1 must include heading: {heading}"


def test_e1_inventory_pack_references_required_evidence():
    text = E1_DOC_PATH.read_text(encoding="utf-8")
    required_refs = [
        "deliverables/deliverables-register.csv",
        "[@EU_CSRD_2022_2464]",
        "[@EU_ESRS_2023_2772]",
        "[@GRI_STANDARDS_2025]",
        "[@GHG_PROTOCOL_CORPORATE_STANDARD]",
        "[@GHG_PROTOCOL_SCOPE3_STANDARD]",
        "## References",
        "controlled indicator-granulation",
        "1,200",
        "99 / 99",
        "3,964",
        "573 / 573",
        "18 / 18",
        "1,242",
        "1,249",
        "3,666",
        "4,252",
        "113",
        "317",
        "accepted SDS technical-baseline metadata",
        "The E1 gate validates the public artifact",
        "GHG Protocol was not part of the minimum E1 acceptance scope",
        "voluntary additional technical coverage",
        "not used to satisfy the minimum `R1` / `R2` acceptance threshold",
        "Canonical SDS indicator definitions",
        "not a count of dimension-expanded operational collection fields",
        "multiple fillable value records",
        "Reportable closed-dimension total: `87,547`",
        "dimension-expanded-value-coordinate-annex-v2026-06-05.md",
        "dimension-expanded-value-coordinate-calculation-v2026-06-05.csv",
        "`5,021` canonical SDS definitions",
        "`5,818` calculation-ready nodes",
        "Dimension-expanded reportable value-coordinate evidence totals `87,547`",
        "Operational values",
        "Evidence Boundaries",
        "Additional Technical Scope",
    ]
    for ref in required_refs:
        assert ref in text, f"E1 must reference {ref}"


def test_e1_inventory_pack_uses_active_deliverable_framing():
    text = E1_DOC_PATH.read_text(encoding="utf-8")
    forbidden_phrases = (
        "Original",
        "original",
        "Current baseline",
        "Current-state",
        "Accepted Evidence vs Current",
        "Extra Beyond Original",
        "Scope Coverage Beyond Minimum Project Requirement",
        "now includes",
        "now validates",
        "has moved beyond",
        "historical",
        "previous",
        "evolution",
        "E01 row in `deliverables/deliverables-register.csv`",
        "E01 public artifact and E01 row",
    )
    for phrase in forbidden_phrases:
        assert (
            phrase not in text
        ), f"E1 should read as active deliverable text, not history: {phrase}"


def test_e1_acceptance_gate_section_uses_formal_and_metadata_framing():
    text = _read_e1_acceptance_gate_section()

    required_phrases = (
        "Project acceptance shape",
        "Current evidence basis",
        "Current evidence gate",
        "Accepted technical baseline metadata",
        "GHG Protocol is not part of this minimum E1 acceptance scope",
        "canonical SDS indicator definitions",
        "the `87,547` reportable closed-dimension coordinate total",
        "the `88,397` broader SDS technical-capacity total",
        "Additional technical coverage documented in E1",
    )
    for phrase in required_phrases:
        assert phrase in text, f"E1 acceptance gate must include: {phrase}"

    forbidden_phrases = (
        "Original subsidy threshold",
        "Original acceptance baseline",
        "Current-state baseline",
        "Extra beyond original E1 scope",
        "e1_dataset_register_V",
        "e1_coverage_V",
        "e1_normative_coverage_reviewer_V",
    )
    for phrase in forbidden_phrases:
        assert (
            phrase not in text
        ), f"E1 acceptance gate should avoid stale E1 framing: {phrase}"


def test_e1_inventory_pack_avoids_internal_data_paths():
    text = E1_DOC_PATH.read_text(encoding="utf-8")
    assert_no_forbidden_public_references(text, context="E1")


def test_e1_dimension_expansion_annex_reconciles_headline_totals():
    assert E1_DIMENSION_ANNEX_PATH.exists(), "Missing E1 dimension expansion annex"
    assert E1_DIMENSION_LEDGER_PATH.exists(), "Missing E1 dimension expansion ledger"

    annex = E1_DIMENSION_ANNEX_PATH.read_text(encoding="utf-8")
    for phrase in (
        "reportable_total = 82,858 + 4,399 + 290 = 87,547",
        "technical_total = 82,858 + 4,985 + 554 = 88,397",
        "GHG Protocol is included here as voluntary additional technical coverage",
    ):
        assert phrase in annex, f"E1 dimension annex must include: {phrase}"

    with E1_DIMENSION_LEDGER_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    reportable_by_standard = {
        standard: sum(
            int(row["reportable_coordinates"] or 0)
            for row in rows
            if row["standard"] == standard
        )
        for standard in {"ESRS", "GRI", "GHG Protocol"}
    }
    assert reportable_by_standard == {
        "ESRS": 82858,
        "GRI": 4399,
        "GHG Protocol": 290,
    }
    assert sum(reportable_by_standard.values()) == 87547


def test_spanish_e1_deliverable_matches_pinned_figures():
    assert (
        SPANISH_E1_DOC_PATH.exists()
    ), f"Missing Spanish E1 deliverable: {SPANISH_E1_DOC_PATH}"
    text = SPANISH_E1_DOC_PATH.read_text(encoding="utf-8")
    missing = [figure for figure in E1_PINNED_FIGURES if figure not in text]
    assert not missing, f"Spanish E1 deliverable missing pinned figures: {missing}"
    # Version must match the current formal ficha table, not a stale stamp.
    assert "| Versión | V1.0 |" in text
    assert "V2026-06-08" not in text
    assert "El anexo CSV usa valores controlados en inglés" in text
    assert "`annual`" in text
    assert "## Doble materialidad y origen del dato" in text
    assert "`entity-assessment-required`" in text
    assert "no predetermina la materialidad de una empresa" in text


def test_spanish_e1_machine_readable_annex_is_current_and_controlled():
    assert (
        SPANISH_E1_CSV_PATH.exists()
    ), f"Missing Spanish E1 CSV: {SPANISH_E1_CSV_PATH}"
    with SPANISH_E1_CSV_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1805
    required_columns = {
        "identifier",
        "variable",
        "area_esg",
        "estandar",
        "unidad",
        "tipo_unidad",
        "frecuencia",
        "tipo_valor",
        "definicion",
        "origen_definicion",
        "origen_dato_operativo",
        "evaluacion_doble_materialidad",
    }
    assert required_columns <= set(rows[0])
    assert {row["frecuencia"] for row in rows} == {"annual"}
    assert all(row["definicion"].strip() for row in rows)
    assert {row["origen_definicion"] for row in rows} == {"external-standard"}
    assert {row["origen_dato_operativo"] for row in rows} == {"entity-specific"}
    assert {row["evaluacion_doble_materialidad"] for row in rows} == {
        "entity-assessment-required"
    }
