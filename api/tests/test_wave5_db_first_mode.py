"""Wave 5 regression tests for DB-first runtime behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from rdflib import Graph

from src.config.settings import settings
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.concept_service import ConceptService
from src.services.indicator_store import IndicatorStore
from src.services.standard_mapping_store import StandardMappingStore


@pytest.mark.parametrize(
    ("store_cls", "method_name", "args"),
    [
        (IndicatorStore, "get_all", ()),
        (StandardMappingStore, "get_all", ()),
    ],
)
def test_db_first_stores_raise_on_query_failure(
    monkeypatch, store_cls, method_name, args
):
    monkeypatch.setattr(settings, "require_database", True)

    db = MagicMock()
    db.query.side_effect = RuntimeError("db unavailable")

    store = store_cls(db=db)

    with pytest.raises(CanonicalDataUnavailableError):
        getattr(store, method_name)(*args)


@pytest.mark.parametrize(
    ("store_cls", "method_name", "args"),
    [
        (IndicatorStore, "count", ()),
        (StandardMappingStore, "get_supported_standards", ()),
    ],
)
def test_db_first_stores_raise_without_db_session(
    monkeypatch, store_cls, method_name, args
):
    monkeypatch.setattr(settings, "require_database", True)

    store = store_cls(db=None)

    with pytest.raises(CanonicalDataUnavailableError):
        getattr(store, method_name)(*args)


def test_concept_service_raises_on_db_failure_in_db_first_mode(monkeypatch):
    monkeypatch.setattr(settings, "require_database", True)

    db = MagicMock()
    repo = MagicMock()
    repo.count_concepts.side_effect = RuntimeError("semantic db unavailable")

    service = ConceptService(db=db)
    service._repo = repo

    with pytest.raises(CanonicalDataUnavailableError):
        service.has_semantic_data()


@pytest.mark.asyncio
async def test_ontology_router_requires_db_semantics_when_db_first(monkeypatch):
    import src.api.routers.ontology as ontology

    monkeypatch.setattr(settings, "require_database", True)

    with pytest.raises(HTTPException) as exc:
        await ontology.list_taxonomies(
            graph=Graph(),
            concept_service=None,
            current_user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "Canonical semantic data is required" in exc.value.detail


@pytest.mark.asyncio
async def test_ontology_router_returns_503_when_semantic_db_projection_is_empty(
    monkeypatch,
):
    import src.api.routers.ontology as ontology

    monkeypatch.setattr(settings, "require_database", True)

    svc = MagicMock()
    svc.has_semantic_data.return_value = False

    with pytest.raises(HTTPException) as exc:
        await ontology.list_concepts(
            taxonomy=None,
            concept_type=None,
            search=None,
            limit=10,
            offset=0,
            graph=Graph(),
            concept_service=svc,
            current_user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "semantic DB projection is empty or unavailable" in exc.value.detail


@pytest.mark.asyncio
async def test_ontology_router_returns_503_when_db_semantic_read_fails(monkeypatch):
    import src.api.routers.ontology as ontology

    monkeypatch.setattr(settings, "require_database", True)

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.list_concepts_paginated.side_effect = CanonicalDataUnavailableError(
        "semantic read failed"
    )

    with pytest.raises(HTTPException) as exc:
        await ontology.list_concepts(
            taxonomy=None,
            concept_type=None,
            search=None,
            limit=10,
            offset=0,
            graph=Graph(),
            concept_service=svc,
            current_user=MagicMock(),
        )

    assert exc.value.status_code == 503
    assert "semantic read failed" in exc.value.detail
