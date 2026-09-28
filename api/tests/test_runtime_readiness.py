from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    ContractResolutionMetadata,
)
from src.services.runtime_readiness import build_interoperability_runtime_readiness


@dataclass(frozen=True)
class FakeMapping:
    id: int
    source_standard: str = "CSRD"
    source_code: str = "E3-4_05"
    target_standard: str = "GRI"
    target_code: str = "GRI 303-5.c"
    relationship_type: str = "equivalent"
    confidence: float = 1.0
    dataset: str = "test"


class FakeMappingStore:
    def search(self, **_kwargs):
        return [FakeMapping(id=12)]


class FakeMappingStoreRaises:
    def search(self, **_kwargs):
        raise RuntimeError("mapping repository unavailable")


class FakeConceptService:
    def get_concept_by_uri(self, uri: str):
        assert uri in {
            "urn:sds:disclosure:csrd:e3-5",
            "urn:sds:disclosure:gri:303-3",
            "urn:sds:reg:esrs:e3_5_01",
            "urn:sds:reg:gri:gri_303_3_a_total_all_areas",
            "urn:sds:reg:gri:gri_303_3_b_total_water_stressed_areas",
        }
        return {"uri": uri}

    def semantic_projection_coverage(self):
        return {
            "ready": True,
            "active_indicators": 4,
            "projected_active_indicators": 4,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": [],
        }


class FakeConceptServiceIncompleteProjection(FakeConceptService):
    def semantic_projection_coverage(self):
        return {
            "ready": False,
            "active_indicators": 4,
            "projected_active_indicators": 3,
            "missing_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
            "stale_active_indicator_identifiers": [],
        }


class FakeConceptServiceStaleProjection(FakeConceptService):
    def semantic_projection_coverage(self):
        return {
            "ready": False,
            "active_indicators": 4,
            "projected_active_indicators": 4,
            "missing_active_indicator_identifiers": [],
            "stale_active_indicator_identifiers": ["urn:sds:reg:esrs:e1_6_01"],
        }


class FakeResolver:
    def resolve(self, concept: str):
        assert concept == "urn:sds:disclosure:csrd:e3-5"
        return CalculationContract(
            contract_id="contract-e3-5",
            concept=concept,
            contract_version="2026.05",
            contract_hash="sha256:" + "a" * 64,
            runtime_status="executable",
            formula="industrial + cooling",
            result_unit="m3",
            inputs=(
                CalculationContractInput(
                    "industrial", "syg:Water_Industrial", unit="m3"
                ),
                CalculationContractInput("cooling", "syg:Water_Cooling", unit="m3"),
            ),
            resolution=ContractResolutionMetadata(source="test", row_id="99"),
        )


class FakeUnitConverter:
    def convert(self, value, from_unit, to_unit):
        assert (from_unit, to_unit) in {("L", "m3"), ("kWh", "MWh")}
        converted_value = Decimal("1.000000")
        converted_unit = to_unit
        return type(
            "Result",
            (),
            {
                "converted_value": converted_value,
                "converted_unit": converted_unit,
            },
        )()

    def are_units_compatible(self, from_unit, to_unit):
        assert (from_unit, to_unit) == ("GJ", "t CO2e")
        return False


class FakeUnitConverterCompatibilityError(FakeUnitConverter):
    def are_units_compatible(self, from_unit, to_unit):
        assert (from_unit, to_unit) == ("GJ", "t CO2e")
        raise RuntimeError("conversion catalogue unavailable")


class FakeUnitConverterFailures:
    def convert(self, _value, from_unit, to_unit):
        raise RuntimeError(f"missing conversion {from_unit}->{to_unit}")

    def are_units_compatible(self, from_unit, to_unit):
        assert (from_unit, to_unit) == ("GJ", "t CO2e")
        return True


class FakeConceptServiceRaisesCoverage(FakeConceptService):
    def semantic_projection_coverage(self):
        raise RuntimeError("projection unavailable")


class FakeConceptServiceMissingConcepts:
    def get_concept_by_uri(self, uri: str):
        if uri.endswith("gri:gri_303_3_a_total_all_areas"):
            raise RuntimeError("catalog read failed")
        return None


class FakeResolverRaises:
    def resolve(self, concept: str):
        raise RuntimeError(f"missing {concept}")


class FakeResolverNonExecutable:
    def resolve(self, concept: str):
        return CalculationContract(
            contract_id="contract-e3-5-draft",
            concept=concept,
            contract_version="2026.05",
            contract_hash="sha256:" + "b" * 64,
            runtime_status="draft",
            formula="industrial + cooling",
            result_unit="m3",
            inputs=(
                CalculationContractInput(
                    "industrial", "syg:Water_Industrial", unit="m3"
                ),
            ),
            resolution=ContractResolutionMetadata(source="test", row_id="100"),
        )


def test_interoperability_runtime_readiness_passes_when_all_runtime_surfaces_exist():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptService(),
    )

    assert response.status == "ready"
    assert response.required_checks == 6
    assert response.passed_checks == 6
    assert {check.name for check in response.checks} == {
        "semantic_catalogues_loaded",
        "l_to_m3_unit_conversion",
        "kwh_to_mwh_energy_conversion",
        "gj_to_tco2e_requires_emission_factor",
        "csrd_gri_exact_mapping_loaded",
        "csrd_disclosure_e3_5_calculation_contract_loaded",
    }


def test_emission_factor_refusal_check_is_not_ready_when_converter_errors():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverterCompatibilityError(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptService(),
    )

    assert response.status == "not_ready"
    check = next(
        check
        for check in response.checks
        if check.name == "gj_to_tco2e_requires_emission_factor"
    )
    assert check.ready is False
    assert "compatibility check failed" in check.detail
    assert check.evidence["error"] == "conversion catalogue unavailable"


def test_interoperability_readiness_fails_when_catalog_projection_is_incomplete():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptServiceIncompleteProjection(),
    )

    assert response.status == "not_ready"
    check = next(
        check for check in response.checks if check.name == "semantic_catalogues_loaded"
    )
    assert check.ready is False
    assert "semantic projection is incomplete" in check.detail
    assert check.evidence["active_indicators"] == 4
    assert check.evidence["projected_active_indicators"] == 3
    assert check.evidence["missing_active_indicator_identifiers"] == [
        "urn:sds:reg:esrs:e1_6_01"
    ]


def test_interoperability_readiness_fails_when_catalog_projection_is_stale():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptServiceStaleProjection(),
    )

    assert response.status == "not_ready"
    check = next(
        check for check in response.checks if check.name == "semantic_catalogues_loaded"
    )
    assert check.ready is False
    assert check.evidence["active_indicators"] == 4
    assert check.evidence["projected_active_indicators"] == 4
    assert check.evidence["stale_active_indicator_identifiers"] == [
        "urn:sds:reg:esrs:e1_6_01"
    ]


def test_interoperability_readiness_reports_missing_runtime_prerequisites():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverterFailures(),
        mapping_store=None,
        contract_resolver=None,
        concept_service=None,
    )

    checks = {check.name: check for check in response.checks}
    assert response.status == "not_ready"
    assert checks["semantic_catalogues_loaded"].ready is False
    assert "No DB-backed concept service" in checks["semantic_catalogues_loaded"].detail
    assert (
        checks["l_to_m3_unit_conversion"]
        .evidence["error"]
        .startswith("missing conversion")
    )
    assert (
        checks["kwh_to_mwh_energy_conversion"]
        .evidence["error"]
        .startswith("missing conversion")
    )
    assert checks["gj_to_tco2e_requires_emission_factor"].ready is False
    assert (
        "incorrectly exposed" in checks["gj_to_tco2e_requires_emission_factor"].detail
    )
    assert checks["csrd_gri_exact_mapping_loaded"].ready is False
    assert checks["csrd_disclosure_e3_5_calculation_contract_loaded"].ready is False
    assert (
        "No DB-backed calculation contract resolver"
        in checks["csrd_disclosure_e3_5_calculation_contract_loaded"].detail
    )


def test_interoperability_readiness_reports_projection_and_contract_errors():
    missing_contract = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolverRaises(),
        concept_service=FakeConceptServiceRaisesCoverage(),
    )
    checks = {check.name: check for check in missing_contract.checks}
    assert checks["semantic_catalogues_loaded"].evidence["error"] == (
        "projection unavailable"
    )
    assert (
        checks["csrd_disclosure_e3_5_calculation_contract_loaded"]
        .evidence["error"]
        .startswith("missing urn:sds:disclosure:csrd:e3-5")
    )

    non_executable = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolverNonExecutable(),
        concept_service=FakeConceptService(),
    )
    check = next(
        check
        for check in non_executable.checks
        if check.name == "csrd_disclosure_e3_5_calculation_contract_loaded"
    )
    assert check.ready is False
    assert "not executable" in check.detail
    assert check.evidence["runtime_status"] == "draft"


def test_interoperability_readiness_reports_mapping_store_failure():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStoreRaises(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptService(),
    )

    check = next(
        check
        for check in response.checks
        if check.name == "csrd_gri_exact_mapping_loaded"
    )
    assert response.status == "not_ready"
    assert check.ready is False
    assert "could not be checked" in check.detail
    assert check.evidence == {"error": "mapping repository unavailable"}


def test_interoperability_readiness_reports_missing_and_unreadable_concepts():
    response = build_interoperability_runtime_readiness(
        unit_converter=FakeUnitConverter(),
        mapping_store=FakeMappingStore(),
        contract_resolver=FakeResolver(),
        concept_service=FakeConceptServiceMissingConcepts(),
    )

    check = next(
        check for check in response.checks if check.name == "semantic_catalogues_loaded"
    )
    assert check.ready is False
    assert "Missing required concepts" in check.detail
    assert "urn:sds:disclosure:csrd:e3-5" in check.evidence["missing"]
    assert "urn:sds:reg:gri:gri_303_3_a_total_all_areas" in check.evidence["missing"]
