"""Tests for canonical semantic concept models and repository."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.database import models as db_models
from src.database.repositories.concept_repository import ConceptRepository


def test_concept_semantic_models_expose_expected_columns():
    """Concept, formula, variable, and equivalence tables should expose the Wave 1 schema."""
    assert hasattr(db_models, "Concept")
    assert hasattr(db_models, "ConceptFormula")
    assert hasattr(db_models, "ConceptVariable")
    assert hasattr(db_models, "ConceptEquivalence")

    concept_cols = set(db_models.Concept.__table__.columns.keys())
    formula_cols = set(db_models.ConceptFormula.__table__.columns.keys())
    variable_cols = set(db_models.ConceptVariable.__table__.columns.keys())
    equivalence_cols = set(db_models.ConceptEquivalence.__table__.columns.keys())

    assert {
        "id",
        "uri",
        "indicator_id",
        "label",
        "description",
        "taxonomy",
        "concept_type",
        "unit",
        "temporal_granularity",
        "hierarchy_level",
        "concept_state",
        "created_at",
        "updated_at",
    }.issubset(concept_cols)
    assert {
        "id",
        "concept_id",
        "expression",
        "expression_language",
        "version",
        "is_active",
    }.issubset(formula_cols)
    assert {
        "id",
        "concept_id",
        "variable_uri",
        "variable_label",
        "ordering",
        "aggregation_method",
    }.issubset(variable_cols)
    assert {
        "id",
        "source_concept_id",
        "target_uri",
        "target_taxonomy",
        "relationship_type",
        "confidence",
    }.issubset(equivalence_cols)


def test_concept_repository_builds_expected_query_chains():
    """Repository should filter and order the semantic concept tables deterministically."""
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.options.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = []
    query.first.return_value = None

    repo = ConceptRepository(db)

    assert repo.get_by_uri("csrd:E3_5") is None
    assert (
        repo.list_concepts(
            taxonomy="CSRD", concept_type="Indicator", concept_state="catalogued"
        )
        == []
    )
    assert repo.get_active_formula(1) is None
    assert repo.get_variables(1) == []
    assert repo.get_equivalences(1) == []

    assert db.query.called
    assert query.filter.called
    assert query.order_by.called
