#!/usr/bin/env python3
"""Canonical ESG-dimension classifier for SDS register variables.

Deterministic mapping from (framework, datapoint code) -> ESG dimension. This
replaces the prior free-text ``Topic`` keyword heuristic that mislabelled every
GRI datapoint as ``E`` and emitted zero ``G`` for the whole register, because
GRI ``Topic`` carries bare standard codes ("GRI 403") rather than thematic
labels and a silent ``dimension = 'E'`` default swallowed every miss.

The signal used here (framework id + standard code/series) is structured and
already present in the same Atomizer input, so classification is deterministic
and total over the installed ESRS+GRI surface. Unknown codes return ``None`` so
callers can fail loudly instead of silently defaulting.

Root-cause and fix attribution: universal-consensus run
``workspace/consensus/20260612-e1-dimension-fault-attribution`` (FINAL,
unanimous: primary fault SDS, fix belongs in SDS). The expected distribution
over the pinned 1,805-variable register is E=761, S=550, G=318, Transversal=176
— identical to the E1 inventory annex ``area_esg`` column.
"""

from __future__ import annotations

import re

DIMENSION_E = "E"
DIMENSION_S = "S"
DIMENSION_G = "G"
DIMENSION_TRANSVERSAL = "Transversal"

VALID_DIMENSIONS = (DIMENSION_E, DIMENSION_S, DIMENSION_G, DIMENSION_TRANSVERSAL)

# ESRS topical-standard + ESRS 2 cross-cutting prefixes -> dimension.
#   E1-E5 environmental · S1-S4 social · G1 + GOV governance ·
#   BP/IRO/MDR/SBM (basis, impacts-risks-opportunities, minimum disclosure,
#   strategy/business-model) are cross-cutting general disclosures -> Transversal.
_ESRS_PREFIX = {
    "E1": DIMENSION_E, "E2": DIMENSION_E, "E3": DIMENSION_E,
    "E4": DIMENSION_E, "E5": DIMENSION_E,
    "S1": DIMENSION_S, "S2": DIMENSION_S, "S3": DIMENSION_S, "S4": DIMENSION_S,
    "G1": DIMENSION_G, "GOV": DIMENSION_G,
    "BP": DIMENSION_TRANSVERSAL, "IRO": DIMENSION_TRANSVERSAL,
    "MDR": DIMENSION_TRANSVERSAL, "SBM": DIMENSION_TRANSVERSAL,
}


def _esrs_dimension(code: str) -> str | None:
    match = re.match(r"[A-Za-z]+\d*", code.strip())
    if not match:
        return None
    return _ESRS_PREFIX.get(match.group(0).upper())


def _gri_dimension(code: str) -> str | None:
    match = re.match(r"GRI\s*(\d+)", code.strip(), re.IGNORECASE)
    if not match:
        return None
    series = int(match.group(1))
    if series == 2:            # GRI 2 General Disclosures
        return DIMENSION_G
    if series in (1, 3):       # GRI 1 Foundation / GRI 3 Material Topics
        return DIMENSION_TRANSVERSAL
    if 100 <= series < 200:    # 1xx topic standards (e.g. GRI 101 Biodiversity)
        return DIMENSION_E
    if 200 <= series < 300:    # 2xx Economic
        return DIMENSION_G
    if 300 <= series < 400:    # 3xx Environmental
        return DIMENSION_E
    if 400 <= series < 500:    # 4xx Social
        return DIMENSION_S
    return None


def classify_esg_dimension(framework: str, code: str) -> str | None:
    """Return 'E' | 'S' | 'G' | 'Transversal', or None if not classifiable.

    ``framework`` is the FrameworkID (ESRS / GRI); ``code`` is the DatapointCode.
    """
    framework_id = (framework or "").strip().upper()
    code = (code or "").strip()
    if not code:
        return None
    if framework_id == "ESRS":
        return _esrs_dimension(code)
    if framework_id == "GRI":
        return _gri_dimension(code)
    return None
