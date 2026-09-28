import pytest

from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.unit_expression import (
    UnitExpressionParser,
    _ExpressionParser,
)


def make_parser():
    return UnitExpressionParser(
        unit_dimensions={
            "kg": DimensionVector({"mass": 1}),
            "kgCO2e": DimensionVector({"co2e": 1}),
            "kWh": DimensionVector({"energy": 1}),
            "m": DimensionVector({"length": 1}),
            "m3": DimensionVector({"volume": 1}),
            "s": DimensionVector({"time": 1}),
            "year": DimensionVector({"time": 1}),
            "EUR": DimensionVector({"currency": 1}),
            "USD": DimensionVector({"currency": 1}),
            "EURm": DimensionVector({"currency": 1}),
        },
        aliases={
            "kg CO2e": "kgCO2e",
            "m³": "m3",
            "million EUR": "EURm",
        },
    )


def test_parser_handles_simple_ratio_with_aliases():
    parser = make_parser()

    parsed = parser.parse("kg CO2e/kWh")

    assert parsed.canonical_expression == "kgCO2e/kWh"
    assert parsed.dimension == DimensionVector({"co2e": 1, "energy": -1})


def test_parser_handles_per_million_currency_denominator():
    parser = make_parser()

    parsed = parser.parse("m³ per million EUR")

    assert parsed.canonical_expression == "m3/EURm"
    assert parsed.dimension == DimensionVector({"volume": 1, "currency": -1})


def test_parser_handles_compound_ratio_with_parentheses():
    parser = make_parser()

    parsed = parser.parse("kg CO2e/(million EUR*year)")

    assert parsed.canonical_expression == "kgCO2e/(EURm*year)"
    assert parsed.dimension == DimensionVector({"co2e": 1, "currency": -1, "time": -1})


def test_parser_handles_exponents_and_explicit_multiplication():
    parser = make_parser()

    parsed = parser.parse("m2.s^-1")

    assert parsed.canonical_expression == "m^2*s^-1"
    assert parsed.dimension == DimensionVector({"length": 2, "time": -1})


def test_parser_uses_left_associative_multiply_and_divide():
    parser = make_parser()

    parsed = parser.parse("m/s*s")

    assert parsed.canonical_expression == "m/s*s"
    assert parsed.dimension == DimensionVector({"length": 1})


def test_parser_normalizes_signed_exponents_and_negative_zero():
    parser = make_parser()

    positive = parser.parse("s^+1")
    negative_zero = parser.parse("s^-0")

    assert positive.canonical_expression == "s^1"
    assert positive.dimension == DimensionVector({"time": 1})
    assert negative_zero.canonical_expression == "s^0"
    assert negative_zero.dimension.as_dict() == {}


def test_parser_keeps_exact_unit_match_before_trailing_exponent():
    parser = make_parser()

    parsed = parser.parse("m3/year")

    assert parsed.canonical_expression == "m3/year"
    assert parsed.dimension == DimensionVector({"volume": 1, "time": -1})


def test_currency_units_parse_as_expression_dimensions_not_fx_rates():
    parser = make_parser()

    parsed = parser.parse("USD/EUR")

    assert parsed.canonical_expression == "USD/EUR"
    assert parsed.dimension.as_dict() == {}


def test_alias_replacement_is_token_aware():
    parser = UnitExpressionParser(
        unit_dimensions={
            "g": DimensionVector({"mass": 1}),
            "gram": DimensionVector({"mass": 1}),
            "kg": DimensionVector({"mass": 1}),
        },
        aliases={"g": "gram"},
    )

    parsed = parser.parse("kg/g")

    assert parsed.canonical_expression == "kg/gram"
    assert parsed.dimension.as_dict() == {}


def test_parser_rejects_non_string_expression():
    parser = make_parser()

    with pytest.raises(TypeError):
        parser.parse(None)


def test_parser_rejects_expression_that_becomes_empty_after_alias_rewrite():
    parser = UnitExpressionParser(
        unit_dimensions={"kg": DimensionVector({"mass": 1})},
        aliases={"empty": " "},
    )

    with pytest.raises(ValueError, match="empty"):
        parser.parse("empty")


def test_expression_parser_rejects_unconsumed_trailing_syntax_and_missing_symbol():
    parser = make_parser()

    with pytest.raises(ValueError, match="unsupported unit expression syntax"):
        parser.parse("kg)")

    with pytest.raises(ValueError, match="expected unit symbol"):
        _ExpressionParser("", {"kg": DimensionVector({"mass": 1})})._parse_unit()


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "kg//kWh",
        "kg**kWh",
        "kg/",
        "(kg/kWh",
        "kg unknown",
        "kg^",
        "kg per per kWh",
        "kgCO2e/(EURm*)",
    ],
)
def test_parser_fails_closed_on_invalid_syntax(expression):
    parser = make_parser()

    with pytest.raises(ValueError):
        parser.parse(expression)
