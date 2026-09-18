from __future__ import annotations

import re
from dataclasses import dataclass

from src.calculation.conversion.dimensions import DimensionVector


@dataclass(frozen=True)
class ParsedUnitExpression:
    canonical_expression: str
    dimension: DimensionVector


class UnitExpressionParser:
    """Parse deterministic unit expressions into canonical text and dimensions.

    Multiplication (``*`` or ``.``) and division (``/`` or ``per``) share the
    same precedence and parse left-to-right. Use parentheses for denominator
    products, for example ``kgCO2e/(EURm*year)``.
    """

    def __init__(
        self,
        *,
        unit_dimensions: dict[str, DimensionVector],
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.unit_dimensions = unit_dimensions
        self.aliases = aliases or {}

    def parse(self, expression: str) -> ParsedUnitExpression:
        canonical_input = self._canonicalize(expression)
        parser = _ExpressionParser(canonical_input, self.unit_dimensions)
        canonical, dimension = parser.parse()
        return ParsedUnitExpression(canonical_expression=canonical, dimension=dimension)

    def _canonicalize(self, expression: str) -> str:
        if not isinstance(expression, str):
            raise TypeError("unit expression must be a string")
        value = " ".join(str(expression).strip().split())
        if not value:
            raise ValueError("unit expression is empty")
        for alias, canonical in sorted(
            self.aliases.items(), key=lambda item: -len(item[0])
        ):
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_\u00b2\u00b3\u00b9]){re.escape(alias)}(?![A-Za-z0-9_\u00b2\u00b3\u00b9])"
            )
            value = pattern.sub(canonical, value)
        value = re.sub(r"\bper\b", "/", value)
        value = value.replace(" ", "")
        if not value:
            raise ValueError("unit expression is empty")
        return value


class _ExpressionParser:
    def __init__(self, text: str, unit_dimensions: dict[str, DimensionVector]) -> None:
        self.text = text
        self.unit_dimensions = unit_dimensions
        self.position = 0

    def parse(self) -> tuple[str, DimensionVector]:
        canonical, dimension = self._parse_expression()
        if self.position != len(self.text):
            raise ValueError(
                f"unsupported unit expression syntax near: {self.text[self.position:]}"
            )
        return canonical, dimension

    def _parse_expression(self) -> tuple[str, DimensionVector]:
        canonical, dimension = self._parse_factor()
        while self._peek() in {"/", "*", "."}:
            operator = self._peek()
            self.position += 1
            right_canonical, right_dimension = self._parse_factor()
            if operator == "/":
                canonical = f"{canonical}/{right_canonical}"
                dimension = dimension / right_dimension
            else:
                canonical = f"{canonical}*{right_canonical}"
                dimension = dimension * right_dimension
        return canonical, dimension

    def _parse_factor(self) -> tuple[str, DimensionVector]:
        canonical, dimension = self._parse_primary()
        if self._peek() == "^":
            self.position += 1
            exponent = self._parse_signed_integer()
            canonical = f"{canonical}^{exponent}"
            dimension = dimension ** int(exponent)
        return canonical, dimension

    def _parse_primary(self) -> tuple[str, DimensionVector]:
        char = self._peek()
        if char is None:
            raise ValueError("unit expression ended unexpectedly")
        if char == "(":
            self.position += 1
            canonical, dimension = self._parse_expression()
            if self._peek() != ")":
                raise ValueError("unbalanced parentheses in unit expression")
            self.position += 1
            return f"({canonical})", dimension
        if char in {")", "/", "*", ".", "^"}:
            raise ValueError(f"unexpected operator in unit expression: {char}")
        return self._parse_unit()

    def _parse_unit(self) -> tuple[str, DimensionVector]:
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
        if not symbol:
            raise ValueError("expected unit symbol")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            raise ValueError(f"unsupported unit token: {symbol}")
        if symbol in self.unit_dimensions:
            return symbol, self.unit_dimensions[symbol]

        match = re.fullmatch(r"(.+?)(\d+)", symbol)
        if match:
            base_symbol, exponent = match.groups()
            if base_symbol in self.unit_dimensions:
                return f"{base_symbol}^{exponent}", self.unit_dimensions[
                    base_symbol
                ] ** int(exponent)

        raise ValueError(f"unknown unit in expression: {symbol}")

    def _parse_signed_integer(self) -> str:
        start = self.position
        if self._peek() in {"+", "-"}:
            self.position += 1
        digit_start = self.position
        while self._peek() is not None and self._peek().isdigit():
            self.position += 1
        if self.position == digit_start:
            raise ValueError("expected integer exponent")
        return str(int(self.text[start : self.position]))

    def _peek(self) -> str | None:
        if self.position >= len(self.text):
            return None
        return self.text[self.position]
