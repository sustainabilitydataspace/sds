"""Shared public concept catalog policy constants."""

from sqlalchemy import func, or_

CATALOG_PROJECTION_SOURCES = (
    "indicator_catalog",
    "disclosure_catalog",
    "standard_datapoint_catalog",
)
PUBLIC_CATALOG_TAXONOMIES = ("CSRD", "GRI", "GHG", "SYGRIS")
PUBLIC_SOURCE_TAXONOMIES = ("CSRD", "GRI", "GHG")
FUTURE_CATALOG_TAXONOMIES = ("CDP", "ISSB", "SASB", "TCFD", "UN_SDG")
UNIFIED_SYGRIS_TAXONOMY = "SYGRIS"


def public_catalog_concept_conditions(concept_cls):
    """SQLAlchemy conditions restricting a Concept query to the public catalog.

    Single source of truth for the default (no taxonomy filter) public-catalog
    predicate used by ConceptRepository._apply_filters(public_catalog=True): a
    concept is public when its taxonomy is not a future-catalog taxonomy AND it is
    either a non-public-catalog taxonomy OR a member of the catalog projection
    sources. Reused by the RDF projection generator + its gate so the generated
    public artifact never includes internal/future concepts (codex F09 M1).
    """
    return (
        func.upper(concept_cls.taxonomy).notin_(FUTURE_CATALOG_TAXONOMIES),
        or_(
            func.upper(concept_cls.taxonomy).notin_(PUBLIC_CATALOG_TAXONOMIES),
            concept_cls.projection_source.in_(CATALOG_PROJECTION_SOURCES),
        ),
    )
