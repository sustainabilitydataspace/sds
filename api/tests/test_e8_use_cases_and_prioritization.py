from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E8_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E08-use-cases"
    / "final"
    / "e08-casos-de-uso-y-prioridades-sectoriales.md"
)


def test_e8_has_at_least_five_use_cases():
    text = E8_DOC_PATH.read_text(encoding="utf-8")
    use_cases = re.findall(
        r"^##\s+\d+\.\s+Caso de uso\s+UC-\d{2}\b",
        text,
        flags=re.MULTILINE,
    )
    assert len(use_cases) >= 5, "E8 must document at least 5 use cases."


def test_e8_has_prioritization_matrix_with_impact_feasibility_urgency():
    text = E8_DOC_PATH.read_text(encoding="utf-8")
    assert "## 4. Matriz de priorización" in text

    after_heading = text.split("## 4. Matriz de priorización", 1)[1]
    table_header_candidates = [
        line for line in after_heading.splitlines() if line.strip().startswith("|")
    ]
    assert any(
        "Impacto" in line and "Viabilidad" in line and "Urgencia" in line
        for line in table_header_candidates
    ), "E8 must include a prioritization table with Impacto/Viabilidad/Urgencia columns."

    rows = re.findall(r"^\|\s*(UC-\d{2})\s*\|", after_heading, flags=re.MULTILINE)
    assert len(set(rows)) >= 5, "Prioritization matrix must cover at least 5 use cases."


def test_e8_does_not_reintroduce_unemitted_ngsi_ld_domain_classes():
    text = E8_DOC_PATH.read_text(encoding="utf-8")
    unemitted_classes = {
        "EmissionObservation",
        "EmissionFactor",
        "EmissionAggregate",
        "EnergyConsumption",
        "EnergyMix",
        "ContractAttribute",
        "WaterWithdrawal",
        "WaterDischarge",
        "WaterStressZone",
        "WasteStream",
        "SecondaryMaterialOffer",
        "TreatmentFacility",
        "Parcel",
        "SupplyLot",
        "LandUseEvidence",
    }
    assert not (unemitted_classes & set(re.findall(r"`?([A-Z][A-Za-z0-9]+)`?", text)))
