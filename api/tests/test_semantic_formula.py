from __future__ import annotations

from src.calculation.semantic_formula import (
    build_infix_sum_expression,
    build_sum_function_expression,
    canonical_formula_for_compare,
    compact_reference,
    display_unit_from_reference,
    local_name,
    normalize_formula_expression,
    normalize_temporal_granularity,
)


def test_sum_formula_helpers_match_engine_infix_convention():
    stored = build_sum_function_expression(
        ["syg:Water_Industrial", "syg:Water_Cooling"]
    )
    assert stored == "SUM(syg:Water_Industrial, syg:Water_Cooling)"
    assert normalize_formula_expression(stored) == "Water_Industrial + Water_Cooling"
    assert canonical_formula_for_compare(stored) == "Water_Industrial + Water_Cooling"


def test_sds_urn_local_names_are_engine_safe_identifiers():
    assert local_name("urn:sds:reg:esrs:e3_5_01") == "e3_5_01"
    assert (
        local_name("urn:sds:reg:gri:gri_303_3_a_total_all_areas")
        == "gri_303_3_a_total_all_areas"
    )
    assert normalize_formula_expression("SUM(urn:sds:reg:esrs:e3_5_01)") == "e3_5_01"


def test_sum_wrapper_with_single_division_expression_preserves_expression():
    expression = "sum((revenue * factor * share) / 1000)"

    assert (
        normalize_formula_expression(expression) == "(revenue * factor * share) / 1000"
    )


def test_display_unit_and_temporal_normalization_follow_runtime_mapping():
    assert display_unit_from_reference("sds:CubicMeter") == "m³"
    assert (
        display_unit_from_reference(
            "https://sustainabilitydataspace.com/ontology#Tonne"
        )
        == "t"
    )
    assert (
        normalize_temporal_granularity(
            "https://sustainabilitydataspace.com/ontology#Monthly"
        )
        == "monthly"
    )


def test_semantic_formula_empty_and_literal_unit_edges():
    assert compact_reference(None) is None
    assert compact_reference("   ") is None
    assert local_name(None) is None
    assert build_sum_function_expression(["", None]) is None
    assert build_infix_sum_expression(["", None]) is None
    assert normalize_formula_expression("   ") is None
    assert display_unit_from_reference(None) is None
    assert display_unit_from_reference("sds:Kilogram") == "kg"
    assert display_unit_from_reference("sds:Liter") == "L"
    assert display_unit_from_reference("sds:CustomUnit") == "sds:CustomUnit"
