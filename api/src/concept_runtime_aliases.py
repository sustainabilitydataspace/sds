"""Runtime aliases for migrated disclosure concept identifiers."""

from __future__ import annotations

from src.ontology.curie import DEFAULT_NAMESPACES

CSRD_E3_5_DISCLOSURE_URI = "urn:sds:disclosure:csrd:e3-5"
GRI_303_3_DISCLOSURE_URI = "urn:sds:disclosure:gri:303-3"

_CANONICAL_TO_LEGACY_ALIASES = {
    "urn:sds:disclosure:csrd:e1-6": (
        "csrd:E1_6",
        "https://data.efrag.org/esrs#E1_6",
    ),
    CSRD_E3_5_DISCLOSURE_URI: (
        "csrd:E3_5",
        "https://data.efrag.org/esrs#E3_5",
    ),
    GRI_303_3_DISCLOSURE_URI: (
        "gri:303_3",
        "https://data.globalreporting.org/gri#303_3",
    ),
    "urn:sds:disclosure:gri:305-1": (
        "gri:305_1",
        "https://data.globalreporting.org/gri#305_1",
    ),
}

_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in _CANONICAL_TO_LEGACY_ALIASES.items()
    for alias in (canonical, *aliases)
}


def canonical_disclosure_uri(value: str | None) -> str | None:
    """Return the migrated SDS disclosure URI for a known legacy disclosure alias."""

    token = _clean(value)
    if token is None:
        return None
    expanded = DEFAULT_NAMESPACES.expand(token)
    return _ALIAS_TO_CANONICAL.get(token) or _ALIAS_TO_CANONICAL.get(expanded)


def legacy_disclosure_aliases_by_canonical() -> dict[str, tuple[str, ...]]:
    """Return migrated disclosure URI aliases that should not remain public rows."""

    return dict(_CANONICAL_TO_LEGACY_ALIASES)


def concept_uri_candidates(value: str | None) -> list[str]:
    """Return ordered lookup candidates for runtime concept resolution."""

    token = _clean(value)
    if token is None:
        return []

    canonical = canonical_disclosure_uri(token)
    if canonical is None:
        return _dedupe([token, DEFAULT_NAMESPACES.expand(token)])

    return _dedupe(
        [
            canonical,
            token,
            DEFAULT_NAMESPACES.expand(token),
            *_CANONICAL_TO_LEGACY_ALIASES[canonical],
        ]
    )


def normalize_runtime_concept_uri(value: str | None) -> str:
    """Canonicalize known migrated disclosure aliases for API payloads."""

    token = _clean(value)
    if token is None:
        return ""
    return canonical_disclosure_uri(token) or DEFAULT_NAMESPACES.compact(
        DEFAULT_NAMESPACES.expand(token)
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))
