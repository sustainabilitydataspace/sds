"""Backward-compatible ontology service facade."""

from __future__ import annotations

from .concept_service import ConceptService


class OntologyService(ConceptService):
    """Compatibility wrapper for the public ontology router."""

    pass
