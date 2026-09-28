from datetime import date
from decimal import Decimal

import pytest

from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.physical import (
    AmbiguousConversionRuleError,
    PhysicalConversionRule,
    PhysicalUnit,
    PhysicalUnitConverter,
    UnitDimensionError,
    _FactorExpressionParser,
    _safe_decimal_eval,
)


def _converter(rules=()):
    units = {
        "kg": PhysicalUnit(
            "kg", "kilogram", DimensionVector({"mass": 1}), Decimal("1")
        ),
        "g": PhysicalUnit("g", "gram", DimensionVector({"mass": 1}), Decimal("0.001")),
        "t": PhysicalUnit("t", "tonne", DimensionVector({"mass": 1}), Decimal("1000")),
        "kWh": PhysicalUnit(
            "kWh", "kilowatt hour", DimensionVector({"energy": 1}), Decimal("3600000")
        ),
        "MWh": PhysicalUnit(
            "MWh",
            "megawatt hour",
            DimensionVector({"energy": 1}),
            Decimal("3600000000"),
        ),
        "kgCO2e": PhysicalUnit(
            "kgCO2e", "kilogram CO2e", DimensionVector({"co2e": 1}), Decimal("1")
        ),
        "tCO2e": PhysicalUnit(
            "tCO2e", "tonne CO2e", DimensionVector({"co2e": 1}), Decimal("1000")
        ),
        "EURm": PhysicalUnit(
            "EURm", "million euro", DimensionVector({"currency": 1}), Decimal("1000000")
        ),
        "year": PhysicalUnit(
            "year", "year", DimensionVector({"time": 1}), Decimal("1")
        ),
        "K": PhysicalUnit(
            "K", "kelvin", DimensionVector({"temperature": 1}), Decimal("1")
        ),
        "degC": PhysicalUnit(
            "degC",
            "degree Celsius",
            DimensionVector({"temperature": 1}),
            Decimal("1"),
            Decimal("273.15"),
        ),
    }
    return PhysicalUnitConverter(units=units, rules=list(rules), precision=6)


def test_linear_conversion_uses_base_factor():
    result = _converter().convert(Decimal("2"), "t", "kg")

    assert result.value == Decimal("2000.000000")
    assert result.trace[0]["step_type"] == "unit"
    assert result.trace[0]["from_unit"] == "t"
    assert result.trace[0]["to_unit"] == "kg"


def test_display_safe_catalog_symbols_convert_through_expression_tokens():
    converter = PhysicalUnitConverter(
        units={
            "kg CO2e": PhysicalUnit(
                "kg CO2e",
                "kilogram CO2e",
                DimensionVector({"co2e": 1}),
                Decimal("1"),
                aliases=("kgCO2e",),
            ),
            "t CO2e": PhysicalUnit(
                "t CO2e",
                "tonne CO2e",
                DimensionVector({"co2e": 1}),
                Decimal("1000"),
                aliases=("tCO2e",),
            ),
        },
        rules=[],
    )

    result = converter.convert(Decimal("2500"), "kg CO2e", "t CO2e")

    assert result.value == Decimal("2.500000")
    assert result.trace[0]["formula"] == "canonical dimension factor conversion"


def test_catalog_symbol_wins_over_torr_alias_collision_with_mmhg():
    torr_factor = Decimal("133.322368421")
    mmhg_factor = Decimal("133.322387415")
    converter = PhysicalUnitConverter(
        units={
            "torr": PhysicalUnit(
                "torr",
                "torr",
                DimensionVector({"mass": 1, "length": -1, "time": -2}),
                torr_factor,
                aliases=("mmHg",),
            ),
            "mmHg": PhysicalUnit(
                "mmHg",
                "millimeter mercury",
                DimensionVector({"mass": 1, "length": -1, "time": -2}),
                mmhg_factor,
            ),
        },
        rules=[],
        precision=12,
    )

    assert converter.parser.parse("torr").canonical_expression == "torr"
    assert converter.parser.parse("mmHg").canonical_expression == "mmHg"
    assert converter.convert(Decimal("1"), "torr", "mmHg").value == (
        torr_factor / mmhg_factor
    ).quantize(Decimal("0.000000000000"))


def test_conflicting_expression_aliases_fail_closed():
    with pytest.raises(ValueError, match="ambiguous expression unit alias"):
        PhysicalUnitConverter(
            units={
                "alpha": PhysicalUnit(
                    "alpha",
                    "alpha",
                    DimensionVector({"mass": 1}),
                    Decimal("1"),
                    aliases=("shared",),
                ),
                "beta": PhysicalUnit(
                    "beta",
                    "beta",
                    DimensionVector({"mass": 1}),
                    Decimal("2"),
                    aliases=("shared",),
                ),
            },
            rules=[],
        )


def test_compound_conversion_is_dimension_aware():
    result = _converter().convert(Decimal("1"), "kgCO2e/kWh", "tCO2e/MWh")

    assert result.value == Decimal("1.000000")


def test_parenthesized_denominator_product_conversion_uses_canonical_parser_expression():
    result = _converter().convert(
        Decimal("2"),
        "kgCO2e/(EURm*year)",
        "tCO2e/(EURm*year)",
    )

    assert result.value == Decimal("0.002000")


def test_exponent_canonicalization_conversion_uses_canonical_parser_expression():
    result = _converter().convert(Decimal("2"), "g^2", "kg^2")

    assert result.value == Decimal("0.000002")


def test_incompatible_dimensions_fail_closed():
    with pytest.raises(UnitDimensionError):
        _converter().convert(Decimal("1"), "kg", "kWh")


def test_direct_rules_respect_validity_dates_before_factor_fallback():
    rules = [
        PhysicalConversionRule(
            "kg",
            "t",
            "value / 500",
            priority=10,
            rule_id="valid-2024",
            valid_from=date(2024, 1, 1),
            valid_to=date(2024, 12, 31),
        )
    ]

    direct = _converter(rules).convert(
        Decimal("1000"), "kg", "t", as_of=date(2024, 6, 1)
    )
    fallback = _converter(rules).convert(
        Decimal("1000"), "kg", "t", as_of=date(2025, 1, 1)
    )

    assert direct.value == Decimal("2.000000")
    assert direct.trace[0]["rule_id"] == "valid-2024"
    assert fallback.value == Decimal("1.000000")
    assert "rule_id" not in fallback.trace[0]


def test_ambiguous_direct_rules_fail_closed():
    rules = [
        PhysicalConversionRule("kg", "t", "value / 1000", priority=10, rule_id="r1"),
        PhysicalConversionRule("kg", "t", "value / 1000", priority=10, rule_id="r2"),
    ]

    with pytest.raises(AmbiguousConversionRuleError):
        _converter(rules).convert(Decimal("1"), "kg", "t", as_of=date(2024, 1, 1))


@pytest.mark.parametrize(
    "formula", ["round(value / 1000)", "value // 1000", "value % 1000"]
)
def test_direct_rule_formula_rejects_unsupported_syntax(formula):
    rules = [
        PhysicalConversionRule("kg", "t", formula, priority=10, rule_id="unsafe"),
    ]

    with pytest.raises(ValueError):
        _converter(rules).convert(Decimal("1000"), "kg", "t")


def test_offset_unit_without_explicit_direct_rule_fails_closed():
    with pytest.raises(UnitDimensionError):
        _converter().convert(Decimal("20"), "degC", "K")


def test_identity_and_direct_rule_edge_cases():
    converter = _converter(
        [
            PhysicalConversionRule(
                "kg",
                "t",
                "value / 1000",
                priority=5,
                rule_id="kg-to-t",
                valid_from=date(2024, 1, 1),
                valid_to=date(2024, 12, 31),
            )
        ]
    )

    identity = converter.convert(Decimal("3"), "kg", "kg")
    assert identity.value == Decimal("3")
    assert identity.trace == []

    assert converter.rules[0].applies_on(None) is True
    assert converter.rules[0].applies_on(date(2023, 12, 31)) is False
    assert converter.rules[0].applies_on(date(2025, 1, 1)) is False

    direct = converter.convert(Decimal("2500"), "kg", "t", as_of=date(2024, 5, 1))
    assert direct.value == Decimal("2.500000")
    assert direct.trace[0]["rule_id"] == "kg-to-t"


@pytest.mark.parametrize(
    "expression",
    ["", "value +", "True", "value / 0", "value << 1"],
)
def test_safe_decimal_eval_rejects_invalid_or_unsafe_expressions(expression):
    with pytest.raises(ValueError):
        _safe_decimal_eval(expression, {"value": Decimal("1")})


def test_safe_decimal_eval_accepts_decimal_literals_and_rejects_edge_nodes():
    assert _safe_decimal_eval("1.5 + value", {"value": Decimal("2")}) == Decimal("3.5")
    assert _safe_decimal_eval("-value", {"value": Decimal("2")}) == Decimal("-2")

    with pytest.raises(ValueError, match="Constant of type str"):
        _safe_decimal_eval("'x'", {"value": Decimal("1")})
    with pytest.raises(ValueError, match="Invert"):
        _safe_decimal_eval("~value", {"value": Decimal("1")})
    with pytest.raises(ValueError, match="undefined variable"):
        _safe_decimal_eval("missing", {"value": Decimal("1")})


def test_factor_expression_parser_rejects_remaining_text_and_bad_primary():
    units = _converter().units

    with pytest.raises(ValueError, match="unsupported unit expression syntax"):
        _FactorExpressionParser("kg(kg", units).parse()

    with pytest.raises(ValueError, match="unit expression ended unexpectedly"):
        _FactorExpressionParser("", units).parse()

    with pytest.raises(ValueError, match="unbalanced parentheses"):
        _FactorExpressionParser("(kg", units).parse()

    with pytest.raises(ValueError, match="unexpected operator"):
        _FactorExpressionParser("/kg", units).parse()

    with pytest.raises(ValueError, match="unknown unit"):
        _FactorExpressionParser("unknown", units).parse()

    with pytest.raises(ValueError, match="expected integer exponent"):
        _FactorExpressionParser("kg^", units).parse()

    with pytest.raises(UnitDimensionError, match="offset unit"):
        _FactorExpressionParser("degC", units).parse()

    assert _FactorExpressionParser("kg^-2", units).parse() == Decimal("1")
