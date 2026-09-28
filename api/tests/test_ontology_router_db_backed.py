"""DB-backed ontology router tests for Wave 3."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rdflib import Graph


@pytest.mark.asyncio
async def test_ontology_list_concepts_prefers_db_service():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.list_concepts_paginated.return_value = (
        [
            {
                "uri": "urn:sds:disclosure:csrd:e3-5",
                "label": "Water consumption",
                "description": "Total water consumption",
                "taxonomy": "CSRD",
                "concept_type": "Disclosure",
                "unit": "sds:CubicMeter",
                "temporal_granularity": "annual",
                "hierarchy_level": 1,
                "similarity_score": None,
                "related_variables": None,
                "formula": "SUM(syg:Water_Industrial, syg:Water_Cooling)",
            }
        ],
        1,
    )

    response = await ontology.list_concepts(
        taxonomy="CSRD",
        concept_type="indicator",
        search="water",
        limit=10,
        offset=0,
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )

    assert response.total == 1
    assert response.items[0].uri == "urn:sds:disclosure:csrd:e3-5"
    svc.list_concepts_paginated.assert_called_once()


@pytest.mark.asyncio
async def test_ontology_get_concept_details_prefers_db_service():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.get_concept_by_uri.return_value = {
        "uri": "urn:sds:disclosure:csrd:e3-5",
        "label": "Water consumption",
        "description": "Total water consumption",
        "taxonomy": "CSRD",
        "concept_type": "Disclosure",
        "unit": "sds:CubicMeter",
        "temporal_granularity": "annual",
        "hierarchy_level": 1,
        "similarity_score": None,
        "related_variables": ["syg:Water_Industrial", "syg:Water_Cooling"],
        "formula": "SUM(syg:Water_Industrial, syg:Water_Cooling)",
    }

    concept = await ontology.get_concept_details(
        "urn:sds:disclosure:csrd:e3-5",
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )

    assert concept.uri == "urn:sds:disclosure:csrd:e3-5"
    assert concept.formula == "SUM(syg:Water_Industrial, syg:Water_Cooling)"
    assert concept.related_variables == ["syg:Water_Industrial", "syg:Water_Cooling"]
    svc.get_concept_by_uri.assert_called_once_with(
        "urn:sds:disclosure:csrd:e3-5",
        public_catalog=True,
    )


@pytest.mark.asyncio
async def test_ontology_equivalences_prefers_db_service():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.list_equivalences.return_value = [
        {
            "source_concept": "urn:sds:disclosure:csrd:e3-5",
            "target_concept": "urn:sds:disclosure:gri:303-3",
            "equivalence_type": "exact",
            "confidence": 1.0,
            "metadata": {"relationship_type": "equivalent"},
        }
    ]

    equivalences = await ontology.get_equivalences(
        concept="urn:sds:disclosure:csrd:e3-5",
        source_taxonomy="CSRD",
        target_taxonomy="GRI",
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )

    assert len(equivalences) == 1
    assert equivalences[0].target_concept == "urn:sds:disclosure:gri:303-3"
    assert equivalences[0].equivalence_type == "exact"


@pytest.mark.asyncio
async def test_ontology_taxonomies_prefers_db_service():
    import src.api.routers.ontology as ontology

    svc = MagicMock()
    svc.has_semantic_data.return_value = True
    svc.list_taxonomies.return_value = {
        "taxonomies": {
            "CSRD": {
                "name": "CSRD",
                "description": None,
                "type": None,
                "concepts_count": 4,
                "disclosures_count": 2,
                "variables_count": 2,
                "units_count": 0,
                "version": None,
            }
        },
        "total_taxonomies": 1,
        "total_concepts": 4,
        "last_updated": "2026-04-15T00:00:00+00:00",
    }

    response = await ontology.list_taxonomies(
        graph=Graph(),
        concept_service=svc,
        current_user=MagicMock(),
    )

    assert response["total_taxonomies"] == 1
    assert response["taxonomies"]["CSRD"]["concepts_count"] == 4
