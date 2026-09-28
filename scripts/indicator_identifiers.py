#!/usr/bin/env python3
"""Canonical identifier helpers for SDS indicator imports and exports."""

from __future__ import annotations

import re

URN_PATTERN = re.compile(r"^urn:sds:reg:[a-z0-9]+:[a-z0-9_]+$")
URN_PREFIX_PATTERN = re.compile(r"^urn:sds:reg:([^:]+):(.+)$", re.IGNORECASE)
NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
MULTI_UNDERSCORE_PATTERN = re.compile(r"_+")


def normalize_framework_token(framework: str | None) -> str:
    """Collapse a framework name to the URN-safe namespace token."""
    token = NON_ALNUM_PATTERN.sub("", (framework or "").strip().lower())
    return token or "unknown"


def canonicalize_identifier_segment(value: str | None, *, fallback: str = "item") -> str:
    """Normalize a framework datapoint/code into the SDS URN local segment."""
    normalized = NON_ALNUM_PATTERN.sub("_", (value or "").strip().lower())
    normalized = MULTI_UNDERSCORE_PATTERN.sub("_", normalized).strip("_")
    return normalized or fallback


def build_indicator_identifier(
    framework: str | None,
    code: str | None,
    *,
    fallback: str = "item",
) -> str:
    """Build a canonical SDS indicator URN from a framework/code pair."""
    namespace = normalize_framework_token(framework)
    segment = canonicalize_identifier_segment(code, fallback=fallback)
    return f"urn:sds:reg:{namespace}:{segment}"


def canonicalize_indicator_identifier(identifier: str | None) -> str:
    """Normalize an existing SDS indicator URN to the canonical form when possible."""
    raw = (identifier or "").strip()
    match = URN_PREFIX_PATTERN.match(raw)
    if not match:
        return raw
    namespace, segment = match.groups()
    return build_indicator_identifier(namespace, segment)


def is_valid_indicator_identifier(identifier: str | None) -> bool:
    """Return True when the identifier matches the canonical SDS URN contract."""
    return bool(URN_PATTERN.fullmatch((identifier or "").strip()))
