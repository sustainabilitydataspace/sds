"""Cross-alias mapping authority must be classified before transferring values."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from src.api.models import ValueResolveRequest
from src.services.value_resolution import resolve_value_request
from src.services.value_store import InMemoryValueStore


@dataclass(frozen=True)
class Mapping:
    id: int
    source_standard: str
    source_code: str
    target_standard: str
    target_code: str
    relationship_type: str
    confidence: float = 1.0
    dataset: str = "test_mappings"


class MappingStore:
    def __init__(self, routes):
        self.routes = routes

    def find_candidate_routes(self, **_kwargs):
        return self.routes


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["ESRS_E3-5", "ESRS-E3-5", "CSRD E3-5", "ESRS.E3-5"])
async def test_implicit_source_rejects_equivalent_and_partial_esrs_code_aliases(alias):
    values = InMemoryValueStore()
    values.create(
        value_id="esrs-e3-5",
        concept="csrd:E3-5",
        entity="plant-a",
        period=date(2024, 1, 1),
        value=Decimal("12"),
        value_type="numeric",
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "test"},
    )
    routes = [
        Mapping(801, "ESRS", "E3-5", "GRI", "303-3", "equivalent"),
        Mapping(802, "ESRS", alias, "GRI", "303-3", "partial"),
    ]
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303-3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=values,
        mapping_store=MappingStore(routes),
    )
    assert response.status == "ambiguous_mapping"
    assert response.value is None


@pytest.mark.asyncio
async def test_failed_present_target_calculation_cannot_fall_back_to_mapping_value():
    values = InMemoryValueStore()
    values.create(
        value_id="water-e3-5",
        concept="csrd:E3-5",
        entity="plant-a",
        period=date(2024, 1, 1),
        value=Decimal("12"),
        value_type="numeric",
        unit="m3",
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "test"},
    )

    async def failed_calculation(_request):
        raise HTTPException(
            status_code=404,
            detail="CalculationError: failed while evaluating a present ontology formula",
        )

    with pytest.raises(HTTPException) as error:
        await resolve_value_request(
            ValueResolveRequest(
                target_concept="gri:303-3",
                entity="plant-a",
                period="2024",
            ),
            value_store=values,
            mapping_store=MappingStore(
                [Mapping(801, "ESRS", "E3-5", "GRI", "303-3", "equivalent")]
            ),
            calculate=failed_calculation,
        )
    assert error.value.status_code == 404
    assert "CalculationError" in error.value.detail
