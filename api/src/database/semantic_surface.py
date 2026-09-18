"""Deterministic semantic/catalog surface snapshots for isolation gates."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from src.database.models import (
    Concept,
    ConceptEquivalence,
    ConceptFormula,
    ConceptIndicatorLink,
    ConceptVariable,
    ConversionRule,
    HierarchyConfiguration,
    Indicator,
    StandardMapping,
    SustainabilityStandard,
    Unit,
    UnitCategory,
)


def _digest_rows(rows: list[list[Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=True, default=str, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _query_rows(session: Session, model, *columns) -> list[list[Any]]:
    query = session.query(*columns)
    rows = query.order_by(*columns).all()
    return [list(row) for row in rows]


def capture_semantic_surface(session: Session) -> dict[str, Any]:
    """Capture a deterministic fingerprint of semantic/catalog/reference tables."""
    tables = {
        "indicators": _query_rows(
            session,
            Indicator,
            Indicator.id,
            Indicator.identifier,
            Indicator.title,
            Indicator.dimension,
            Indicator.code_esrs,
            Indicator.code_gri,
            Indicator.concept_state,
        ),
        "standard_mappings": _query_rows(
            session,
            StandardMapping,
            StandardMapping.source_standard,
            StandardMapping.source_code,
            StandardMapping.target_standard,
            StandardMapping.target_code,
            StandardMapping.relationship_type,
            StandardMapping.confidence,
            StandardMapping.esg_dimension,
        ),
        "sustainability_standards": _query_rows(
            session,
            SustainabilityStandard,
            SustainabilityStandard.id,
            SustainabilityStandard.name,
            SustainabilityStandard.organization,
            SustainabilityStandard.version,
            SustainabilityStandard.is_active,
        ),
        "concepts": _query_rows(
            session,
            Concept,
            Concept.uri,
            Concept.indicator_id,
            Concept.label,
            Concept.taxonomy,
            Concept.concept_type,
            Concept.unit,
            Concept.concept_state,
        ),
        "concept_formulas": _query_rows(
            session,
            ConceptFormula,
            ConceptFormula.concept_id,
            ConceptFormula.expression,
            ConceptFormula.expression_language,
            ConceptFormula.version,
            ConceptFormula.is_active,
        ),
        "concept_variables": _query_rows(
            session,
            ConceptVariable,
            ConceptVariable.concept_id,
            ConceptVariable.variable_uri,
            ConceptVariable.ordering,
            ConceptVariable.aggregation_method,
            ConceptVariable.temporal_granularity,
        ),
        "concept_equivalences": _query_rows(
            session,
            ConceptEquivalence,
            ConceptEquivalence.source_concept_id,
            ConceptEquivalence.target_uri,
            ConceptEquivalence.target_taxonomy,
            ConceptEquivalence.relationship_type,
            ConceptEquivalence.confidence,
        ),
        "concept_indicator_links": _query_rows(
            session,
            ConceptIndicatorLink,
            ConceptIndicatorLink.concept_id,
            ConceptIndicatorLink.indicator_id,
            ConceptIndicatorLink.link_type,
            ConceptIndicatorLink.confidence,
            ConceptIndicatorLink.rationale,
        ),
        "hierarchy_configurations": _query_rows(
            session,
            HierarchyConfiguration,
            HierarchyConfiguration.id,
            HierarchyConfiguration.company_id,
            HierarchyConfiguration.hierarchy_type,
            HierarchyConfiguration.name,
            HierarchyConfiguration.is_active,
            HierarchyConfiguration.configuration,
        ),
        "unit_categories": _query_rows(
            session,
            UnitCategory,
            UnitCategory.name,
            UnitCategory.base_unit,
            UnitCategory.special_conversions,
        ),
        "units": _query_rows(
            session,
            Unit,
            Unit.symbol,
            Unit.category_id,
            Unit.name,
            Unit.conversion_factor,
            Unit.conversion_offset,
            Unit.is_active,
        ),
        "conversion_rules": _query_rows(
            session,
            ConversionRule,
            ConversionRule.from_unit,
            ConversionRule.to_unit,
            ConversionRule.formula,
            ConversionRule.reverse_formula,
            ConversionRule.is_active,
        ),
    }

    summary = {
        name: {
            "count": len(rows),
            "digest": _digest_rows(rows),
        }
        for name, rows in tables.items()
    }
    overall = sha256(
        json.dumps(
            summary, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "tables": summary,
        "overall_digest": overall,
    }
