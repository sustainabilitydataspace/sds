"""Services package for business logic."""

from importlib import import_module

_SERVICE_MODULES = {
    "ConceptService": "concept_service",
    "HierarchyService": "hierarchy_service",
    "OntologyService": "ontology_service",
    "UnitService": "unit_service",
}


def __getattr__(name):
    module_name = _SERVICE_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module_name}", __name__), name)


__all__ = ["UnitService", "HierarchyService", "ConceptService", "OntologyService"]
