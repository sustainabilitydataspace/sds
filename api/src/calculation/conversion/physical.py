from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from src.calculation.conversion.dimensions import DimensionVector
from src.calculation.conversion.trace import ConversionStep
from src.calculation.conversion.unit_expression import UnitExpressionParser


class UnitDimensionError(ValueError):
    pass


class AmbiguousConversionRuleError(ValueError):
    pass


@dataclass(frozen=True)
class PhysicalUnit:
    symbol: str
    name: str
    dimension: DimensionVector
    factor_to_base: Decimal
    offset_to_base: Decimal = Decimal("0")
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicalConversionRule:
    from_unit: str
    to_unit: str
    formula: str
    priority: int
    rule_id: str
    valid_from: date | None = None
    valid_to: date | None = None

    def applies_on(self, as_of: date | None) -> bool:
        if as_of is None:
            return True
        if self.valid_from and as_of < self.valid_from:
            return False
        if self.valid_to and as_of > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class PhysicalConversionResult:
    value: Decimal
    unit: str
    trace: list[dict]


@dataclass(frozen=True)
class UnitConflict:
    """One catalog ambiguity that makes the expression registry unbuildable."""

    kind: str  # expression_token | canonical_symbol | alias
    token: str
    symbols: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class ExpressionRegistryAnalysis:
    expression_units: dict[str, PhysicalUnit]
    aliases: dict[str, str]
    conflicts: tuple[UnitConflict, ...]


def expression_symbol_for(unit: PhysicalUnit) -> str | None:
    """Return a catalog unit's safe atomic expression token, if any."""
    candidates = (re.sub(r"\s+", "", unit.symbol), *unit.aliases)
    for candidate in candidates:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
            return candidate
    return None


def analyze_expression_registry(
    units: dict[str, PhysicalUnit],
) -> ExpressionRegistryAnalysis:
    """Derive the expression registry and report every catalog ambiguity.

    This is the single source of truth for both PhysicalUnitConverter (which
    fails closed on the first conflict, in catalog order) and the admin
    unit-catalog diagnostics. A conflicting entry never overwrites the first
    owner, so later conflicts are reported against the same registry the
    converter would have built up to that point.
    """
    # The database catalog retains display-safe canonical symbols such as
    # ``kg CO2e`` and ``m³``.  The expression grammar deliberately uses
    # identifier-safe tokens, so derive a lossless expression registry and
    # map catalog symbols/aliases into it before parsing.
    expression_units: dict[str, PhysicalUnit] = {}
    conflicts: list[UnitConflict] = []
    # Catalog symbols always take precedence over aliases.  This is a
    # deliberate exception to the usual alias conflict rule: the catalog
    # can describe a historical alias (for example ``torr`` -> ``mmHg``)
    # that is also a separately catalogued canonical symbol.
    canonical_aliases: dict[str, str] = {}
    for unit in units.values():
        expression_symbol = expression_symbol_for(unit)
        if expression_symbol is None:
            continue
        existing = expression_units.get(expression_symbol)
        if existing is not None and existing.symbol != unit.symbol:
            conflicts.append(
                UnitConflict(
                    kind="expression_token",
                    token=expression_symbol,
                    symbols=(existing.symbol, unit.symbol),
                    message=(
                        "ambiguous expression unit token: "
                        f"{expression_symbol} maps to both {existing.symbol!r} "
                        f"and {unit.symbol!r}"
                    ),
                )
            )
            continue
        expression_units[expression_symbol] = unit

        for canonical_symbol in (unit.symbol, expression_symbol):
            existing_canonical = canonical_aliases.get(canonical_symbol)
            if (
                existing_canonical is not None
                and existing_canonical != expression_symbol
            ):
                conflicts.append(
                    UnitConflict(
                        kind="canonical_symbol",
                        token=canonical_symbol,
                        symbols=(existing_canonical, expression_symbol),
                        message=(
                            "ambiguous catalog canonical symbol: "
                            f"{canonical_symbol!r} maps to both "
                            f"{existing_canonical!r} and {expression_symbol!r}"
                        ),
                    )
                )
                continue
            canonical_aliases[canonical_symbol] = expression_symbol

    aliases = dict(canonical_aliases)
    alias_owners: dict[str, str] = {}
    for unit in units.values():
        expression_symbol = expression_symbol_for(unit)
        if expression_symbol is None:
            continue
        if expression_units.get(expression_symbol) is not unit:
            continue
        for alias in unit.aliases:
            canonical_target = canonical_aliases.get(alias)
            if canonical_target is not None:
                # Canonical catalog symbols win regardless of catalog order.
                continue
            existing_target = alias_owners.get(alias)
            if existing_target is not None and existing_target != expression_symbol:
                conflicts.append(
                    UnitConflict(
                        kind="alias",
                        token=alias,
                        symbols=(existing_target, expression_symbol),
                        message=(
                            "ambiguous expression unit alias: "
                            f"{alias!r} maps to both {existing_target!r} "
                            f"and {expression_symbol!r}"
                        ),
                    )
                )
                continue
            alias_owners[alias] = expression_symbol
            aliases[alias] = expression_symbol

    return ExpressionRegistryAnalysis(
        expression_units=expression_units,
        aliases=aliases,
        conflicts=tuple(conflicts),
    )


class PhysicalUnitConverter:
    def __init__(
        self,
        *,
        units: dict[str, PhysicalUnit],
        rules: Sequence[PhysicalConversionRule],
        precision: int = 6,
    ) -> None:
        self.units = units
        self.rules = list(rules)
        self.precision = precision
        # The database catalog retains display-safe canonical symbols such as
        # ``kg CO2e`` and ``m³``.  The expression grammar deliberately uses
        # identifier-safe tokens, so derive a lossless expression registry and
        # map catalog symbols/aliases into it before parsing.  Keeping the
        # original catalog symbols in ``self.units`` preserves public/API
        # output and direct-rule matching.
        analysis = analyze_expression_registry(units)
        if analysis.conflicts:
            raise ValueError(analysis.conflicts[0].message)
        expression_units = analysis.expression_units
        aliases = analysis.aliases

        self._expression_units = expression_units
        dimensions = {
            symbol: unit.dimension for symbol, unit in self._expression_units.items()
        }
        self.parser = UnitExpressionParser(unit_dimensions=dimensions, aliases=aliases)

    @staticmethod
    def _expression_symbol_for(unit: PhysicalUnit) -> str | None:
        """Return this catalog unit's safe atomic expression token, if any."""
        return expression_symbol_for(unit)

    def convert(
        self, value, from_unit: str, to_unit: str, *, as_of: date | None = None
    ) -> PhysicalConversionResult:
        decimal_value = Decimal(str(value))
        if from_unit == to_unit:
            return PhysicalConversionResult(decimal_value, to_unit, [])
        direct = self._select_direct_rule(from_unit, to_unit, as_of)
        if direct is not None:
            converted = _safe_decimal_eval(direct.formula, {"value": decimal_value})
            converted = self._round(converted)
            return PhysicalConversionResult(
                converted,
                to_unit,
                [
                    ConversionStep(
                        step_type="unit",
                        original_value=str(decimal_value),
                        converted_value=str(converted),
                        from_unit=from_unit,
                        to_unit=to_unit,
                        rule_id=direct.rule_id,
                        formula=direct.formula,
                    ).as_dict()
                ],
            )
        converted = self._convert_by_dimensions(decimal_value, from_unit, to_unit)
        return PhysicalConversionResult(
            converted,
            to_unit,
            [
                ConversionStep(
                    step_type="unit",
                    original_value=str(decimal_value),
                    converted_value=str(converted),
                    from_unit=from_unit,
                    to_unit=to_unit,
                    formula="canonical dimension factor conversion",
                ).as_dict()
            ],
        )

    def _select_direct_rule(self, from_unit: str, to_unit: str, as_of: date | None):
        matches = [
            rule
            for rule in self.rules
            if rule.from_unit == from_unit
            and rule.to_unit == to_unit
            and rule.applies_on(as_of)
        ]
        if not matches:
            return None
        matches.sort(key=lambda rule: rule.priority)
        best_priority = matches[0].priority
        best = [rule for rule in matches if rule.priority == best_priority]
        if len(best) != 1:
            raise AmbiguousConversionRuleError(
                f"ambiguous conversion rule: {from_unit} -> {to_unit}"
            )
        return best[0]

    def _convert_by_dimensions(
        self, value: Decimal, from_unit: str, to_unit: str
    ) -> Decimal:
        from_expr = self.parser.parse(from_unit)
        to_expr = self.parser.parse(to_unit)
        if not from_expr.dimension.is_compatible_with(to_expr.dimension):
            raise UnitDimensionError(
                f"incompatible dimensions: {from_unit} -> {to_unit}"
            )
        from_factor = self._expression_factor(from_expr.canonical_expression)
        to_factor = self._expression_factor(to_expr.canonical_expression)
        return self._round(value * from_factor / to_factor)

    def _expression_factor(self, expression: str) -> Decimal:
        return _FactorExpressionParser(expression, self._expression_units).parse()

    def _round(self, value: Decimal) -> Decimal:
        return value.quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )


_DECIMAL_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

# Mirror the calculation engine's bounds so a malicious/bad conversion rule such as
# `value ** 999999999` cannot create an availability DoS at conversion time
# (codex F07 M2). Conversion formulas are linear in practice; these caps are far
# above any legitimate factor while bounding exponentiation cost + result size.
_MAX_DECIMAL_EXPONENT = Decimal("12")
_MAX_DECIMAL_ABS_RESULT = Decimal("1E100")

_DECIMAL_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_decimal_eval(expr: str, variables: dict[str, Decimal]) -> Decimal:
    if not expr or not expr.strip():
        raise ValueError("Expression must not be empty")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {expr}") from exc
    return _walk_decimal_ast(tree.body, variables)


def validate_conversion_formula(expr: str) -> None:
    """Validate a conversion-rule formula is safe to evaluate (load-time guard).

    Probes the formula through the same capped safe evaluator with ``value=1`` so an
    unsafe node, bad syntax, or an over-cap exponent (e.g. ``value ** 999999999``) is
    rejected when the rule is REGISTERED rather than only when first executed
    (codex F07 M2). The exponent bound is checked on the exponent node regardless of
    the base, so the ``value=1`` probe still catches an over-cap exponent.
    """
    _safe_decimal_eval(expr, {"value": Decimal("1")})


def _walk_decimal_ast(node: ast.AST, variables: dict[str, Decimal]) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise ValueError("Unsafe expression node: Constant of type bool")
        if isinstance(node.value, int):
            return Decimal(node.value)
        if isinstance(node.value, float):
            return Decimal(str(node.value))
        raise ValueError(
            f"Unsafe expression node: Constant of type {type(node.value).__name__}"
        )

    if isinstance(node, ast.BinOp):
        op_func = _DECIMAL_BIN_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsafe expression node: {type(node.op).__name__}")
        left = _walk_decimal_ast(node.left, variables)
        right = _walk_decimal_ast(node.right, variables)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_DECIMAL_EXPONENT:
            raise ValueError(
                f"Unsafe exponent: {right} exceeds maximum {_MAX_DECIMAL_EXPONENT}"
            )
        try:
            result = op_func(left, right)
        except (ZeroDivisionError, OverflowError) as exc:
            raise ValueError(f"Arithmetic error in expression: {exc}") from exc
        if abs(result) > _MAX_DECIMAL_ABS_RESULT:
            raise ValueError("Expression result exceeds maximum allowed magnitude")
        return result

    if isinstance(node, ast.UnaryOp):
        op_func = _DECIMAL_UNARY_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsafe expression node: {type(node.op).__name__}")
        return op_func(_walk_decimal_ast(node.operand, variables))

    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        raise ValueError(f"Unsafe expression node: undefined variable '{node.id}'")

    raise ValueError(f"Unsafe expression node: {type(node).__name__}")


class _FactorExpressionParser:
    def __init__(self, text: str, units: dict[str, PhysicalUnit]) -> None:
        self.text = text
        self.units = units
        self.position = 0

    def parse(self) -> Decimal:
        factor = self._parse_expression()
        if self.position != len(self.text):
            raise ValueError(
                f"unsupported unit expression syntax near: {self.text[self.position:]}"
            )
        return factor

    def _parse_expression(self) -> Decimal:
        factor = self._parse_factor()
        while self._peek() in {"/", "*", "."}:
            operator_token = self._peek()
            self.position += 1
            right = self._parse_factor()
            if operator_token == "/":
                factor = factor / right
            else:
                factor = factor * right
        return factor

    def _parse_factor(self) -> Decimal:
        factor = self._parse_primary()
        if self._peek() == "^":
            self.position += 1
            exponent = self._parse_signed_integer()
            factor = factor**exponent
        return factor

    def _parse_primary(self) -> Decimal:
        char = self._peek()
        if char is None:
            raise ValueError("unit expression ended unexpectedly")
        if char == "(":
            self.position += 1
            factor = self._parse_expression()
            if self._peek() != ")":
                raise ValueError("unbalanced parentheses in unit expression")
            self.position += 1
            return factor
        if char in {")", "/", "*", ".", "^"}:
            raise ValueError(f"unexpected operator in unit expression: {char}")
        return self._parse_unit_factor()

    def _parse_unit_factor(self) -> Decimal:
        start = self.position
        while self._peek() is not None and self._peek() not in {
            "(",
            ")",
            "/",
            "*",
            ".",
            "^",
        }:
            self.position += 1
        symbol = self.text[start : self.position]
        unit = self.units.get(symbol)
        if unit is None:
            raise ValueError(f"unknown unit in expression: {symbol}")
        if unit.offset_to_base != 0:
            raise UnitDimensionError(
                f"offset unit requires explicit direct rule: {symbol}"
            )
        return unit.factor_to_base

    def _parse_signed_integer(self) -> int:
        start = self.position
        if self._peek() in {"+", "-"}:
            self.position += 1
        digit_start = self.position
        while self._peek() is not None and self._peek().isdigit():
            self.position += 1
        if self.position == digit_start:
            raise ValueError("expected integer exponent")
        return int(self.text[start : self.position])

    def _peek(self) -> str | None:
        if self.position >= len(self.text):
            return None
        return self.text[self.position]
