"""CURIE/IRI helpers for SDS ontology namespaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict

_CURIE_RE = re.compile(
    r"^(?P<prefix>[A-Za-z][A-Za-z0-9_-]*):(?P<local>[A-Za-z0-9_.-]+)$"
)


@dataclass(frozen=True)
class CurieNamespaces:
    """Namespace mapping for CURIE expansion and compaction."""

    prefixes: Dict[str, str]

    def expand(self, value: str) -> str:
        """Expand a CURIE to full IRI when possible; otherwise return unchanged."""
        if value.startswith("http://") or value.startswith("https://"):
            return value

        match = _CURIE_RE.match(value)
        if not match:
            return value

        prefix = match.group("prefix")
        local = match.group("local")
        base = self.prefixes.get(prefix)
        return f"{base}{local}" if base else value

    def compact(self, iri: str) -> str:
        """Compact a full IRI to a CURIE when possible; otherwise return unchanged."""
        for prefix, base in self.prefixes.items():
            if iri.startswith(base):
                return f"{prefix}:{iri[len(base):]}"
        return iri


DEFAULT_NAMESPACES = CurieNamespaces(
    prefixes={
        "sds": "https://sustainabilitydataspace.com/ontology#",
        "csrd": "https://data.efrag.org/esrs#",
        "gri": "https://data.globalreporting.org/gri#",
        "ghg": "https://ghgprotocol.org/standards#",
        "syg": "https://sustainabilitydataspace.com/sygris#",
    }
)


def taxonomy_from_curie(curie_or_iri: str) -> str:
    """Derive a taxonomy label from a CURIE/IRI."""
    value = curie_or_iri
    if value.startswith("http://") or value.startswith("https://"):
        value = DEFAULT_NAMESPACES.compact(value)

    lowered = value.lower()
    if lowered.startswith("urn:sds:disclosure:csrd:"):
        return "CSRD"
    if lowered.startswith("urn:sds:disclosure:gri:"):
        return "GRI"
    if value.startswith("csrd:"):
        return "CSRD"
    if value.startswith("gri:"):
        return "GRI"
    if value.startswith("ghg:"):
        return "GHG"
    if value.startswith("syg:"):
        return "Sygris"
    if value.startswith("sds:"):
        return "SDS"
    return "Unknown"
