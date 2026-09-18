"""Readiness checks for the real SDS interoperability runtime."""

from __future__ import annotations

from typing import Any

from src.api.models import RuntimeReadinessCheck, RuntimeReadinessResponse
from src.concept_runtime_aliases import (
    CSRD_E3_5_DISCLOSURE_URI,
    GRI_303_3_DISCLOSURE_URI,
)
from src.services.value_resolution import find_mapping_routes


def build_interoperability_runtime_readiness(
    *,
    unit_converter: Any,
    mapping_store: Any = None,
    contract_resolver: Any = None,
    concept_service: Any = None,
) -> RuntimeReadinessResponse:
    """Return the prerequisite state for the interoperability runtime."""

    checks = [
        _concepts_check(concept_service),
        _unit_conversion_check(unit_converter),
        _energy_conversion_check(unit_converter),
        _emission_factor_refusal_check(unit_converter),
        _mapping_check(mapping_store),
        _calculation_contract_check(contract_resolver),
    ]
    passed = sum(1 for check in checks if check.ready)
    status = "ready" if passed == len(checks) else "not_ready"
    return RuntimeReadinessResponse(
        status=status,
        required_checks=len(checks),
        passed_checks=passed,
        checks=checks,
    )


def _concepts_check(concept_service: Any) -> RuntimeReadinessCheck:
    required = [
        CSRD_E3_5_DISCLOSURE_URI,
        GRI_303_3_DISCLOSURE_URI,
        "urn:sds:reg:esrs:e3_5_01",
        "urn:sds:reg:gri:gri_303_3_a_total_all_areas",
        "urn:sds:reg:gri:gri_303_3_b_total_water_stressed_areas",
    ]
    if concept_service is None:
        return RuntimeReadinessCheck(
            name="semantic_catalogues_loaded",
            ready=False,
            detail="No DB-backed concept service is available for semantic catalogue checks.",
            evidence={"required_concepts": required},
        )

    missing: list[str] = []
    for concept in required:
        try:
            if concept_service.get_concept_by_uri(concept) is None:
                missing.append(concept)
        except Exception:
            missing.append(concept)

    coverage = None
    if hasattr(concept_service, "semantic_projection_coverage"):
        try:
            coverage = concept_service.semantic_projection_coverage()
        except Exception as exc:
            coverage = {"ready": False, "error": str(exc)}

    coverage_ready = coverage is None or bool(coverage.get("ready"))
    if not missing and not coverage_ready:
        return RuntimeReadinessCheck(
            name="semantic_catalogues_loaded",
            ready=False,
            detail="Required smoke concepts are present, but semantic projection is incomplete.",
            evidence={
                "required_concepts": required,
                "missing": missing,
                **(coverage or {}),
            },
        )

    return RuntimeReadinessCheck(
        name="semantic_catalogues_loaded",
        ready=not missing and coverage_ready,
        detail=(
            "Required CSRD, GRI, and runtime datapoint concepts are present."
            if not missing
            else "Missing required concepts: " + ", ".join(missing)
        ),
        evidence={
            "required_concepts": required,
            "missing": missing,
            **(coverage or {}),
        },
    )


def _unit_conversion_check(unit_converter: Any) -> RuntimeReadinessCheck:
    ready = False
    detail = "L -> m3 conversion is not available."
    evidence: dict[str, Any] = {"from_unit": "L", "to_unit": "m3"}
    try:
        result = unit_converter.convert(1000, "L", "m3")
        ready = str(result.converted_value) in {"1.000000", "1"}
        evidence["converted_value"] = str(result.converted_value)
        evidence["converted_unit"] = result.converted_unit
        if ready:
            detail = "L -> m3 unit conversion is available."
    except Exception as exc:
        evidence["error"] = str(exc)

    return RuntimeReadinessCheck(
        name="l_to_m3_unit_conversion",
        ready=ready,
        detail=detail,
        evidence=evidence,
    )


def _energy_conversion_check(unit_converter: Any) -> RuntimeReadinessCheck:
    ready = False
    detail = "kWh -> MWh energy conversion is not available."
    evidence: dict[str, Any] = {"from_unit": "kWh", "to_unit": "MWh"}
    try:
        result = unit_converter.convert(1000, "kWh", "MWh")
        ready = str(result.converted_value) in {"1.000000", "1.0000", "1"}
        evidence["converted_value"] = str(result.converted_value)
        evidence["converted_unit"] = result.converted_unit
        if ready:
            detail = "kWh -> MWh energy conversion is available."
    except Exception as exc:
        evidence["error"] = str(exc)

    return RuntimeReadinessCheck(
        name="kwh_to_mwh_energy_conversion",
        ready=ready,
        detail=detail,
        evidence=evidence,
    )


def _emission_factor_refusal_check(unit_converter: Any) -> RuntimeReadinessCheck:
    ready = False
    evidence: dict[str, Any] = {"from_unit": "GJ", "to_unit": "t CO2e"}
    try:
        compatible = unit_converter.are_units_compatible("GJ", "t CO2e")
        ready = compatible is False
        evidence["compatible"] = compatible
    except Exception as exc:
        evidence["error"] = str(exc)

    return RuntimeReadinessCheck(
        name="gj_to_tco2e_requires_emission_factor",
        ready=ready,
        detail=(
            "GJ -> t CO2e is correctly unavailable as a simple unit conversion."
            if ready
            else (
                "GJ -> t CO2e compatibility check failed; runtime cannot confirm "
                "that an emission factor is required."
                if "error" in evidence
                else "GJ -> t CO2e is incorrectly exposed as a direct unit conversion."
            )
        ),
        evidence=evidence,
    )


def _mapping_check(mapping_store: Any) -> RuntimeReadinessCheck:
    routes = find_mapping_routes(
        mapping_store,
        source_concept="csrd:E3-4_05",
        target_concept="gri:303-5.c",
    )
    equivalent = [route for route in routes if route.is_equivalent]
    return RuntimeReadinessCheck(
        name="csrd_gri_exact_mapping_loaded",
        ready=bool(equivalent),
        detail=(
            "csrd:E3-4_05 -> gri:303-5.c exact/equivalent mapping is available."
            if equivalent
            else "csrd:E3-4_05 -> gri:303-5.c exact/equivalent mapping is missing."
        ),
        evidence={
            "mapping_row_ids": [route.row_id for route in equivalent],
            "candidate_count": len(routes),
        },
    )


def _calculation_contract_check(contract_resolver: Any) -> RuntimeReadinessCheck:
    check_name = "csrd_disclosure_e3_5_calculation_contract_loaded"
    if contract_resolver is None:
        return RuntimeReadinessCheck(
            name=check_name,
            ready=False,
            detail="No DB-backed calculation contract resolver is available.",
            evidence={"concept": CSRD_E3_5_DISCLOSURE_URI},
        )
    try:
        contract = contract_resolver.resolve(CSRD_E3_5_DISCLOSURE_URI)
    except Exception as exc:
        return RuntimeReadinessCheck(
            name=check_name,
            ready=False,
            detail=(
                f"{CSRD_E3_5_DISCLOSURE_URI} executable calculation contract is "
                "missing."
            ),
            evidence={"concept": CSRD_E3_5_DISCLOSURE_URI, "error": str(exc)},
        )
    ready = bool(getattr(contract, "is_executable", False))
    return RuntimeReadinessCheck(
        name=check_name,
        ready=ready,
        detail=(
            f"{CSRD_E3_5_DISCLOSURE_URI} executable calculation contract is available."
            if ready
            else (
                f"{CSRD_E3_5_DISCLOSURE_URI} calculation contract exists but is "
                "not executable."
            )
        ),
        evidence={
            "concept": getattr(contract, "concept", CSRD_E3_5_DISCLOSURE_URI),
            "contract_id": getattr(contract, "contract_id", None),
            "contract_version": getattr(contract, "contract_version", None),
            "runtime_status": getattr(contract, "runtime_status", None),
        },
    )
