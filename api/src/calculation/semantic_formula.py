"""Shared helpers for semantic formula normalization and evaluation adapters."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from src.ontology.curie import DEFAULT_NAMESPACES

_URN_TOKEN_RE = re.compile(r"\b(urn:[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)+)\b")
_CURIE_TOKEN_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9_.-]+)\b")
_REFERENCE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)+")


def compact_reference(value: str | None) -> Optional[str]:
    """Return a compact CURIE when possible."""
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    return DEFAULT_NAMESPACES.compact(text) if text.startswith("http") else text


def local_name(value: str | None) -> Optional[str]:
    """Return the local identifier used by the calculation engine."""
    compacted = compact_reference(value)
    if not compacted:
        return None
    if compacted.startswith("urn:"):
        return compacted.rsplit(":", 1)[-1]
    if ":" in compacted:
        return compacted.rsplit(":", 1)[-1]
    return compacted.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def build_sum_function_expression(variable_refs: Iterable[str]) -> Optional[str]:
    """Return canonical SUM(...) storage syntax for a dependency list."""
    compacted = [compact_reference(ref) for ref in variable_refs]
    cleaned = [ref for ref in compacted if ref]
    if not cleaned:
        return None
    return f"SUM({', '.join(cleaned)})"


def build_infix_sum_expression(variable_refs: Iterable[str]) -> Optional[str]:
    """Return the infix formula form used by the current engine."""
    names = [local_name(ref) for ref in variable_refs]
    cleaned = [name for name in names if name]
    if not cleaned:
        return None
    return " + ".join(cleaned)


def normalize_formula_expression(expression: str | None) -> Optional[str]:
    """Normalize ontology/DB formula text to the engine's infix syntax."""
    if not expression:
        return None

    expr = str(expression).strip()
    if not expr:
        return None

    if expr.upper().startswith("SUM(") and expr.endswith(")"):
        inner = expr[4:-1]
        args = [arg.strip() for arg in _split_top_level_commas(inner) if arg.strip()]
        if len(args) == 1:
            if _REFERENCE_TOKEN_RE.fullmatch(args[0]):
                return build_infix_sum_expression(args)
            return normalize_formula_expression(args[0])
        return build_infix_sum_expression(args)

    def _replace_curie(match: re.Match[str]) -> str:
        return local_name(match.group(1)) or match.group(1)

    replaced = _URN_TOKEN_RE.sub(_replace_curie, expr)
    replaced = _CURIE_TOKEN_RE.sub(_replace_curie, replaced)
    return re.sub(r"\s+", " ", replaced).strip()


def _split_top_level_commas(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def normalize_temporal_granularity(value: str | None) -> Optional[str]:
    """Normalize ontology temporal levels to lowercase runtime values."""
    compacted = compact_reference(value)
    if not compacted:
        return None

    token = compacted.split(":", 1)[1] if ":" in compacted else compacted
    token = token.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    return token.lower()


def display_unit_from_reference(value: str | None) -> Optional[str]:
    """Map stored ontology unit references to the runtime display symbols."""
    compacted = compact_reference(value)
    if not compacted:
        return None

    if compacted.endswith("CubicMeter"):
        return "m³"
    if compacted.endswith("Tonne"):
        return "t"
    if compacted.endswith("Kilogram"):
        return "kg"
    if compacted.endswith("Liter"):
        return "L"
    if compacted.endswith("Currency"):
        return "Currency"
    return compacted


def canonical_formula_for_compare(expression: str | None) -> Optional[str]:
    """Return a whitespace-stable formula string for parity checks."""
    normalized = normalize_formula_expression(expression)
    if not normalized:
        return None
    return re.sub(r"\s+", " ", normalized).strip()
