from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.models import (
    CalculationResponse,
    CalculationTrace,
    TemporalGranularity,
    ValueResolveRequest,
)
from src.database.repositories.standard_mapping_repository import (
    MappingCandidateLimitExceeded,
)
from src.services.value_resolution import (
    CalculationContractAbsent,
    resolve_value_request,
)
from src.services.value_store import InMemoryValueStore


@dataclass(frozen=True)
class FakeMapping:
    id: int
    source_standard: str
    source_code: str
    target_standard: str
    target_code: str
    relationship_type: str
    confidence: float = 1.0
    dataset: str = "test_mappings"


class FakeMappingStore:
    def __init__(self, rows: list[FakeMapping]):
        self.rows = rows

    def search(self, **_kwargs):
        return self.rows


class CodeAwareMappingStore:
    def __init__(self, rows: list[FakeMapping]):
        self.rows = rows
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(dict(kwargs))
        if not kwargs.get("source_code") and not kwargs.get("target_code"):
            return self.rows[:1]

        results = self.rows
        if kwargs.get("source_standard"):
            results = [
                row
                for row in results
                if row.source_standard == kwargs["source_standard"]
            ]
        if kwargs.get("target_standard"):
            results = [
                row
                for row in results
                if row.target_standard == kwargs["target_standard"]
            ]
        if kwargs.get("source_code"):
            results = [
                row
                for row in results
                if row.source_code.startswith(kwargs["source_code"])
            ]
        if kwargs.get("target_code"):
            results = [
                row
                for row in results
                if row.target_code.startswith(kwargs["target_code"])
            ]
        return results[: kwargs.get("limit", 100)]


class CandidateRouteMappingStore:
    def __init__(self, rows: list[FakeMapping]):
        self.rows = rows
        self.calls: list[dict] = []

    def find_candidate_routes(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.rows

    def search(self, **_kwargs):
        raise AssertionError("bounded route discovery should not fan out to search()")


class OverflowingCandidateRouteMappingStore:
    def find_candidate_routes(self, **_kwargs):
        raise MappingCandidateLimitExceeded(500)


class LatestOnlyStore:
    def __init__(self, value):
        self.value = value
        self.calls: list[dict] = []

    def latest(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.value

    def iterate(self, **_kwargs):
        raise AssertionError("latest value resolution must use the bounded path")


class ListOnlyStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(dict(kwargs))
        return list(self.rows), len(self.rows)


class SuccessfulConverter:
    def convert(self, value, from_unit, to_unit):
        return SimpleNamespace(
            original_value=value,
            converted_value=Decimal("1.5"),
            original_unit=from_unit,
            converted_unit=to_unit,
            formula_used="fixture-conversion",
        )


class MwhToGjConverter:
    def convert(self, value, from_unit, to_unit):
        assert (from_unit, to_unit) == ("MWh", "GJ")
        converted = Decimal(str(value)) * Decimal("3.6")
        return SimpleNamespace(
            original_value=value,
            converted_value=converted,
            original_unit=from_unit,
            converted_unit=to_unit,
            formula_used="value * 3.6",
        )


def _store_with_value(
    *,
    concept: str,
    value: Decimal = Decimal("12"),
    unit: str = "m3",
    entity: str = "plant-a",
    period: date = date(2024, 1, 1),
) -> InMemoryValueStore:
    store = InMemoryValueStore()
    store.create(
        value_id=f"value-{concept.replace(':', '-')}",
        concept=concept,
        entity=entity,
        period=period,
        value=value,
        value_type="numeric",
        unit=unit,
        original_unit=None,
        conversion_applied=False,
        metadata={"source": "test"},
    )
    return store


def _value_row(
    *,
    concept: str,
    value: Decimal = Decimal("12"),
    unit: str = "m3",
    entity: str = "plant-a",
    period: date = date(2024, 1, 1),
):
    return SimpleNamespace(
        id=f"value-{concept.replace(':', '-')}",
        concept=concept,
        entity=entity,
        period=period,
        value=value,
        unit=unit,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )


async def _calculated_response(request):
    return CalculationResponse(
        concept=request.concept,
        entity=request.entity,
        period=request.period,
        value=Decimal("99"),
        unit="m3",
        formula_used="fixture",
        calculated_at=datetime.now(timezone.utc),
        inputs_used={},
        trace=CalculationTrace(
            calculation_id="calc-run-1",
            steps=["contract_id=calc-1", "contract_version=2026.05"],
            dependencies_resolved=["syg:Water_Industrial"],
            variables_used=["Water_Industrial"],
            variable_values={"Water_Industrial": Decimal("99")},
            aggregations_applied=[],
        ),
    )


async def _calculation_without_trace(request):
    return CalculationResponse(
        concept=request.concept,
        entity=request.entity,
        period=request.period,
        value=Decimal("44"),
        unit="m3",
        formula_used="fixture",
        calculated_at=datetime.now(timezone.utc),
        inputs_used={},
        trace=None,
    )


async def _bridge_calculated_response(request):
    return CalculationResponse(
        concept=request.concept,
        entity=request.entity,
        period=request.period,
        value=Decimal("120"),
        unit="MWh",
        formula_used="sum(esrs_component_a, esrs_component_b)",
        calculated_at=datetime.now(timezone.utc),
        inputs_used={},
        trace=CalculationTrace(
            calculation_id="bridge-run-1",
            steps=[
                "contract_id=bridge-gri-302-1e-total-energy",
                "contract_version=2026.06",
            ],
            dependencies_resolved=[
                "urn:sds:reg:esrs:e1_5_10",
                "urn:sds:reg:esrs:e1_5_11",
            ],
            variables_used=["esrs_component_a", "esrs_component_b"],
            variable_values={
                "esrs_component_a": Decimal("50"),
                "esrs_component_b": Decimal("70"),
            },
            aggregations_applied=[],
        ),
        execution_authority="certified_bridge",
        bridge_id="bridge-gri-302-1e-total-energy",
    )


def _calculation_raises_http(_request):
    raise CalculationContractAbsent(_request.concept)


async def _target_fails_source_calculates(request):
    if request.concept == "gri:303_3":
        raise CalculationContractAbsent(request.concept)
    return await _calculated_response(request)


async def _target_fails_source_bridge_calculates(request):
    if request.concept == "gri:302_1_e":
        raise CalculationContractAbsent(request.concept)
    return await _bridge_calculated_response(request)


@pytest.mark.asyncio
async def test_mapping_candidate_overflow_returns_fail_closed_refusal():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=OverflowingCandidateRouteMappingStore(),
    )

    assert response.status == "ambiguous_mapping"
    assert response.value is None
    assert response.mapping_row_ids == []
    assert response.trace is not None
    assert "exceeded the bounded classification limit" in response.trace["reason"]


@pytest.mark.asyncio
async def test_legacy_mapping_search_detects_501st_candidate_before_classification():
    class CappedLegacySearch:
        def __init__(self):
            self.rows = [
                FakeMapping(
                    id=i,
                    source_standard="ESRS",
                    source_code="E3-5",
                    target_standard="GRI",
                    target_code="303-3",
                    relationship_type="equivalent",
                )
                for i in range(501)
            ]

        def search(self, **kwargs):
            return self.rows[: kwargs["limit"]]

    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=CappedLegacySearch(),
    )

    assert response.status == "ambiguous_mapping"
    assert response.value is None
    assert response.mapping_row_ids == []
    assert response.trace is not None
    assert "exceeded the bounded classification limit" in response.trace["reason"]


@pytest.mark.asyncio
async def test_hidden_target_calculation_403_is_terminal_before_mapping_fallback():
    class ExplodingMappingStore:
        def search(self, **_kwargs):
            raise AssertionError(
                "mapping fallback must not run after calculation denial"
            )

    async def deny_hidden_target(_request):
        raise HTTPException(
            status_code=403,
            detail="Calculation contract is not public.",
        )

    with pytest.raises(HTTPException) as denied:
        await resolve_value_request(
            ValueResolveRequest(
                target_concept="internal:runtime-support-total",
                entity="plant-a",
                period="2024",
                granularity=TemporalGranularity.ANNUAL,
                include_trace=True,
            ),
            value_store=InMemoryValueStore(),
            mapping_store=ExplodingMappingStore(),
            calculate=deny_hidden_target,
        )

    assert denied.value.status_code == 403
    assert denied.value.detail == "Calculation contract is not public."
    denial_text = str(denied.value.detail)
    for hidden_detail in (
        "internal:runtime-support-total",
        "hidden-support-contract",
        "sha256:hidden-support",
        "hidden_observation",
        "runtime_support",
    ):
        assert hidden_detail not in denial_text


@pytest.mark.asyncio
async def test_direct_target_value_wins_and_keeps_calculation_as_alternative():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
            granularity=TemporalGranularity.ANNUAL,
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("12")),
        calculate=_calculated_response,
    )

    assert response.status == "resolved"
    assert response.method == "direct_stored"
    assert response.source_concept == "csrd:E3_5"
    assert response.value == Decimal("12")
    assert response.alternative_route is not None
    assert response.alternative_route["method"] == "calculation"
    assert response.alternative_route["value"] == "99"


@pytest.mark.asyncio
async def test_direct_resolution_uses_bounded_latest_value_path():
    store = LatestOnlyStore(_value_row(concept="csrd:E3_5", value=Decimal("12")))

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
        ),
        value_store=store,
    )

    assert response.status == "resolved"
    assert response.value == Decimal("12")
    assert store.calls == [
        {
            "concept": "csrd:E3_5",
            "entity": "plant-a",
            "period_start": date(2024, 1, 1),
            "period_end": date(2024, 12, 31),
        }
    ]


@pytest.mark.asyncio
async def test_direct_resolution_matches_compact_standard_code_to_stored_sds_urn():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E1-5_12",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(
            concept="urn:sds:reg:esrs:e1_5_12",
            value=Decimal("390.604"),
            unit="MWh",
        ),
    )

    assert response.status == "resolved"
    assert response.method == "direct_stored"
    assert response.source_concept == "urn:sds:reg:esrs:e1_5_12"
    assert response.value == Decimal("390.604")


@pytest.mark.asyncio
async def test_exact_mapping_transfers_value_with_row_id_and_relationship_trace():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("25")),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=42,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.status == "resolved"
    assert response.method == "equivalent_mapping"
    assert response.source_concept == "csrd:E3_5"
    assert response.target_concept == "gri:303_3"
    assert response.relationship_type == "equivalent"
    assert response.mapping_row_ids == ["42"]
    assert response.value == Decimal("25")
    assert response.trace is not None
    assert response.trace["mapping"]["direction"] == "forward"


@pytest.mark.asyncio
async def test_gri_code_matching_accepts_codes_without_repeated_standard_prefix():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3-4_05",
            target_concept="gri:303-5.c",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3-4_05", value=Decimal("25")),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=43,
                    source_standard="ESRS",
                    source_code="E3-4_05",
                    target_standard="GRI",
                    target_code="GRI 303-5.c",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.status == "resolved"
    assert response.method == "equivalent_mapping"
    assert response.mapping_row_ids == ["43"]
    assert response.value == Decimal("25")


@pytest.mark.asyncio
async def test_partial_mapping_is_discoverable_but_refuses_numeric_transfer():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("25")),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=7,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="partial",
                    confidence=0.8,
                )
            ]
        ),
    )

    assert response.status == "not_transformable"
    assert response.method == "refused_non_equivalent_mapping"
    assert response.value is None
    assert response.mapping_row_ids == ["7"]
    assert response.candidates[0]["relationship_type"] == "partial"


@pytest.mark.parametrize(
    ("concept", "expected", "excluded"),
    [
        (
            "urn:sds:reg:esrs:e1_5_12",
            {"csrd:E1-5_12", "esrs:E1-5_12"},
            "gri:302-1.e",
        ),
        (
            "urn:sds:reg:gri:302_1_e",
            {"gri:GRI 302-1.e", "gri:302-1.e"},
            "csrd:E1-5_12",
        ),
        ("unprefixed_indicator", {"unprefixed_indicator"}, "gri:302-1.e"),
    ],
)
def test_value_alias_candidates_stay_within_the_requested_standard(
    concept, expected, excluded
):
    from src.services.value_resolution import _value_concept_candidates

    candidates = _value_concept_candidates(concept)
    assert expected <= set(candidates)
    assert excluded not in candidates


@pytest.mark.asyncio
async def test_value_alias_cannot_substitute_for_cross_standard_mapping_authority():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E1-5_12",
            target_concept="gri:302-1.e",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(
            concept="urn:sds:reg:esrs:e1_5_12", value=Decimal("12"), unit="MWh"
        ),
        mapping_store=FakeMappingStore([]),
    )
    assert response.value is None
    assert response.status == "not_found"


def _gri_302_1a_component_mappings() -> list[FakeMapping]:
    return [
        FakeMapping(
            id=104,
            source_standard="ESRS",
            source_code="E1-5_10",
            target_standard="GRI",
            target_code="GRI 302-1.a",
            relationship_type="narrower",
        ),
        FakeMapping(
            id=105,
            source_standard="ESRS",
            source_code="E1-5_11",
            target_standard="GRI",
            target_code="GRI 302-1.a",
            relationship_type="narrower",
        ),
        FakeMapping(
            id=106,
            source_standard="ESRS",
            source_code="E1-5_12",
            target_standard="GRI",
            target_code="GRI 302-1.a",
            relationship_type="narrower",
        ),
        FakeMapping(
            id=107,
            source_standard="ESRS",
            source_code="E1-5_13",
            target_standard="GRI",
            target_code="GRI 302-1.a",
            relationship_type="narrower",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_source", [None, "csrd:E1-5_10"])
async def test_narrower_pairs_cannot_authorize_an_incomplete_target_sum(
    explicit_source,
):
    values = InMemoryValueStore()
    for code, amount in (("10", "10"), ("11", "20")):
        values.create(
            value_id=f"v-{code}",
            concept=f"urn:sds:reg:esrs:e1_5_{code}",
            entity="plant-a",
            period=date(2024, 1, 1),
            value=Decimal(amount),
            value_type="numeric",
            unit="MWh",
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept=explicit_source,
            target_concept="gri:302-1.a",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=values,
        mapping_store=FakeMappingStore(_gri_302_1a_component_mappings()[:2]),
    )
    assert response.status == "bridge_required"
    assert response.value is None
    assert response.bridge_id is None
    assert response.trace is not None
    assert "partition" in response.trace["reason"]


@pytest.mark.asyncio
async def test_narrower_component_set_refuses_total_even_with_convertible_values():
    value_store = InMemoryValueStore()
    for code, value in {
        "e1_5_10": Decimal("100"),
        "e1_5_11": Decimal("50"),
        "e1_5_12": Decimal("10"),
        "e1_5_13": Decimal("5"),
    }.items():
        concept = f"urn:sds:reg:esrs:{code}"
        value_store.create(
            value_id=f"value-{code}",
            concept=concept,
            entity="plant-a",
            period=date(2024, 1, 1),
            value=value,
            value_type="numeric",
            unit="MWh",
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302-1.a",
            entity="plant-a",
            period="2024",
            target_unit="GJ",
            include_trace=True,
        ),
        value_store=value_store,
        mapping_store=FakeMappingStore(_gri_302_1a_component_mappings()),
        unit_converter=MwhToGjConverter(),
    )

    assert response.status == "bridge_required"
    assert response.method == "refused_non_equivalent_mapping"
    assert response.relationship_type == "narrower"
    assert response.mapping_row_ids == ["104", "105", "106", "107"]
    assert response.component_concepts == [
        "csrd:E1-5_10",
        "csrd:E1-5_11",
        "csrd:E1-5_12",
        "csrd:E1-5_13",
    ]
    assert response.value is None
    assert response.unit == "GJ"
    assert response.conversion_steps == []
    assert response.trace is not None
    assert "partition" in response.trace["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "no_unit",
        "non_numeric",
        "no_converter",
        "broken_converter",
    ],
)
async def test_uncertified_component_set_refuses_before_conversion(fault):
    values = InMemoryValueStore()
    for code in ("10", "11", "12", "13"):
        value = (
            "not-a-number" if fault == "non_numeric" and code == "13" else Decimal("2")
        )
        unit = "" if fault == "no_unit" else "MWh"
        values.create(
            value_id=f"v-{code}",
            concept=f"urn:sds:reg:esrs:e1_5_{code}",
            entity="plant-a",
            period=date(2024, 1, 1),
            value=value,
            value_type=(
                "string" if fault == "non_numeric" and code == "13" else "numeric"
            ),
            unit=unit,
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )

    class BrokenConverter:
        def convert(self, *_args):
            raise ValueError("conversion denied")

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302-1.a",
            entity="plant-a",
            period="2024",
            target_unit=None if fault in {"no_unit", "non_numeric"} else "GJ",
            include_trace=True,
        ),
        value_store=values,
        mapping_store=FakeMappingStore(_gri_302_1a_component_mappings()),
        unit_converter=BrokenConverter() if fault == "broken_converter" else None,
    )
    assert response.status == "bridge_required"
    assert response.value is None
    assert response.mapping_row_ids == ["104", "105", "106", "107"]
    assert response.component_concepts == [
        "csrd:E1-5_10",
        "csrd:E1-5_11",
        "csrd:E1-5_12",
        "csrd:E1-5_13",
    ]
    assert response.trace is not None
    assert "partition" in response.trace["reason"]


@pytest.mark.asyncio
async def test_explicit_component_rejects_sibling_with_contradictory_authority():
    values = InMemoryValueStore()
    for code, amount in (("10", "100"), ("11", "50")):
        values.create(
            value_id=f"value-{code}",
            concept=f"urn:sds:reg:esrs:e1_5_{code}",
            entity="plant-a",
            period=date(2024, 1, 1),
            value=Decimal(amount),
            value_type="numeric",
            unit="MWh",
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )
    routes = _gri_302_1a_component_mappings()[:2] + [
        FakeMapping(
            id=108,
            source_standard="ESRS",
            source_code="E1-5_11",
            target_standard="GRI",
            target_code="GRI 302-1.a",
            relationship_type="equivalent",
        )
    ]
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E1-5_10",
            target_concept="gri:302-1.a",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=values,
        mapping_store=FakeMappingStore(routes),
    )
    assert response.status == "ambiguous_mapping"
    assert response.value is None
    assert set(response.mapping_row_ids) == {"104", "105", "108"}


@pytest.mark.asyncio
async def test_inverse_narrower_routes_cannot_be_aggregated_as_components():
    value_store = InMemoryValueStore()
    for code, value in (("e1_5_10", "100"), ("e1_5_11", "50")):
        value_store.create(
            value_id=f"value-{code}",
            concept=f"urn:sds:reg:esrs:{code}",
            entity="plant-a",
            period=date(2024, 1, 1),
            value=Decimal(value),
            value_type="numeric",
            unit="MWh",
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )
    mappings = [
        FakeMapping(
            id=index,
            source_standard="GRI",
            source_code="GRI 302-1.a",
            target_standard="ESRS",
            target_code=code,
            relationship_type="narrower",
        )
        for index, code in ((104, "E1-5_10"), (105, "E1-5_11"))
    ]

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302-1.a", entity="plant-a", period="2024"
        ),
        value_store=value_store,
        mapping_store=FakeMappingStore(mappings),
    )

    assert response.value is None
    assert response.method != "component_aggregation"


@pytest.mark.asyncio
async def test_explicit_narrower_component_refuses_without_partition_authority():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E1-5_12",
            target_concept="gri:302-1.a",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(
            concept="urn:sds:reg:esrs:e1_5_12",
            value=Decimal("10"),
            unit="MWh",
        ),
        mapping_store=FakeMappingStore(_gri_302_1a_component_mappings()),
    )

    assert response.status == "bridge_required"
    assert response.method == "refused_non_equivalent_mapping"
    assert response.relationship_type == "narrower"
    assert response.mapping_row_ids == ["104", "105", "106", "107"]
    assert response.missing_inputs == []
    assert response.trace is not None
    assert "partition" in response.trace["reason"]


@pytest.mark.asyncio
async def test_mapping_resolution_uses_code_filters_to_avoid_broad_page_misses():
    mapping_store = CodeAwareMappingStore(
        [
            FakeMapping(
                id=101,
                source_standard="ESRS",
                source_code="E3_5",
                target_standard="GRI",
                target_code="303_3",
                relationship_type="equivalent",
            )
        ]
    )

    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("25")),
        mapping_store=mapping_store,
    )

    assert response.status == "resolved"
    assert response.mapping_row_ids == ["101"]
    assert any(
        call.get("source_code") == "E3_5" and call.get("target_code") == "303_3"
        for call in mapping_store.calls
    )


@pytest.mark.asyncio
async def test_mapping_resolution_uses_bounded_candidate_route_lookup():
    mapping_store = CandidateRouteMappingStore(
        [
            FakeMapping(
                id=101,
                source_standard="ESRS",
                source_code="E3_5",
                target_standard="GRI",
                target_code="303_3",
                relationship_type="equivalent",
            )
        ]
    )

    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("25")),
        mapping_store=mapping_store,
    )

    assert response.status == "resolved"
    assert response.mapping_row_ids == ["101"]
    assert len(mapping_store.calls) == 1
    assert mapping_store.calls[0]["source_codes"]
    assert mapping_store.calls[0]["target_codes"]


@pytest.mark.asyncio
async def test_mapping_resolution_preserves_multiple_target_refusal_with_code_filters():
    mapping_store = CodeAwareMappingStore(
        [
            FakeMapping(
                id=201,
                source_standard="ESRS",
                source_code="E3_5",
                target_standard="GRI",
                target_code="303_3",
                relationship_type="equivalent",
            ),
            FakeMapping(
                id=202,
                source_standard="ESRS",
                source_code="E3_6",
                target_standard="GRI",
                target_code="303_3",
                relationship_type="equivalent",
            ),
        ]
    )
    value_store = InMemoryValueStore()
    for concept in ("csrd:E3_5", "csrd:E3_6"):
        value_store.create(
            value_id=f"value-{concept.replace(':', '-')}",
            concept=concept,
            entity="plant-a",
            period=date(2024, 1, 1),
            value=Decimal("25"),
            value_type="numeric",
            unit="m3",
            original_unit=None,
            conversion_applied=False,
            metadata={"source": "test"},
        )

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=value_store,
        mapping_store=mapping_store,
    )

    assert response.status == "multiple_targets"
    assert sorted(response.mapping_row_ids) == ["201", "202"]
    assert any(call.get("target_code") == "303_3" for call in mapping_store.calls)


@pytest.mark.asyncio
async def test_exact_mapping_can_resolve_inverse_direction_without_explicit_source():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="gri:303_3", value=Decimal("31")),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=84,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.status == "resolved"
    assert response.method == "equivalent_mapping"
    assert response.source_concept == "gri:303_3"
    assert response.target_concept == "csrd:E3_5"
    assert response.mapping_row_ids == ["84"]
    assert response.trace is not None
    assert response.trace["mapping"]["direction"] == "inverse"


@pytest.mark.asyncio
async def test_calculation_route_is_used_when_no_direct_target_value_exists():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        calculate=_calculated_response,
    )

    assert response.status == "resolved"
    assert response.method == "calculation"
    assert response.value == Decimal("99")
    assert response.contract_id == "calc-1"
    assert response.contract_version == "2026.05"


@pytest.mark.asyncio
async def test_calculation_route_exposes_bridge_execution_metadata():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302_1_e",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        calculate=_bridge_calculated_response,
    )

    assert response.status == "resolved"
    assert response.method == "calculation"
    assert response.contract_id == "bridge-gri-302-1e-total-energy"
    assert response.execution_authority == "certified_bridge"
    assert response.bridge_id == "bridge-gri-302-1e-total-energy"


@pytest.mark.asyncio
async def test_unit_conversion_failure_returns_structured_unsupported_conversion(
    mock_unit_converter_database,
):
    from src.calculation.unit_converter import UnitConverter

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="syg:TotalEnergyConsumptionWithinOrganization",
            entity="plant-a",
            period="2024",
            target_unit="t CO2e",
            include_trace=True,
        ),
        value_store=_store_with_value(
            concept="syg:TotalEnergyConsumptionWithinOrganization",
            value=Decimal("5"),
            unit="GJ",
        ),
        unit_converter=UnitConverter(),
    )

    assert response.status == "unsupported_conversion"
    assert response.method == "converted"
    assert response.value is None
    assert response.trace is not None
    assert response.trace["conversion"]["from_unit"] == "GJ"
    assert response.trace["conversion"]["to_unit"] == "t CO2e"


@pytest.mark.asyncio
async def test_explicit_source_without_mapping_returns_not_found_trace():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore([]),
    )

    assert response.status == "not_found"
    assert response.method is None
    assert response.trace["reason"].startswith("No mapping route")


@pytest.mark.asyncio
async def test_explicit_source_refuses_ambiguous_equivalent_mapping_rows():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", value=Decimal("25")),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=301,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                ),
                FakeMapping(
                    id=302,
                    source_standard="ESRS",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="exact_match",
                ),
            ]
        ),
    )

    assert response.status == "ambiguous_mapping"
    assert sorted(response.mapping_row_ids) == ["301", "302"]
    assert response.trace["candidates"][0]["transferable"] is True


@pytest.mark.asyncio
async def test_explicit_source_equivalent_mapping_without_source_value_is_not_enough_evidence():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=303,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
        calculate=_calculation_raises_http,
    )

    assert response.status == "not_enough_evidence"
    assert response.mapping_row_ids == ["303"]
    assert "no source value" in response.trace["reason"]


@pytest.mark.asyncio
async def test_target_resolution_without_mapping_candidates_returns_not_found():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore([]),
        calculate=_calculation_raises_http,
    )

    assert response.status == "not_found"
    assert response.trace["reason"].startswith("No direct value")


@pytest.mark.asyncio
async def test_target_resolution_refuses_non_equivalent_mapping_candidates():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=304,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="supporting_context",
                )
            ]
        ),
        calculate=_calculation_raises_http,
    )

    assert response.status == "not_transformable"
    assert response.method == "refused_non_equivalent_mapping"
    assert response.candidates[0]["transferable"] is False


@pytest.mark.asyncio
async def test_target_resolution_uses_source_calculation_then_mapping_when_no_value_exists():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=305,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
        calculate=_target_fails_source_calculates,
    )

    assert response.status == "resolved"
    assert response.method == "calculation_then_mapping"
    assert response.source_concept == "csrd:E3_5"
    assert response.contract_id == "calc-1"
    assert response.trace["calculation"]["calculation_id"] == "calc-run-1"


@pytest.mark.asyncio
async def test_source_calculation_then_mapping_exposes_bridge_execution_metadata():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302_1_e",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=3021,
                    source_standard="ESRS",
                    source_code="E1-5",
                    target_standard="GRI",
                    target_code="302-1-e",
                    relationship_type="equivalent",
                )
            ]
        ),
        calculate=_target_fails_source_bridge_calculates,
    )

    assert response.status == "resolved"
    assert response.method == "calculation_then_mapping"
    assert response.source_concept == "csrd:E1-5"
    assert response.contract_id == "bridge-gri-302-1e-total-energy"
    assert response.execution_authority == "certified_bridge"
    assert response.bridge_id == "bridge-gri-302-1e-total-energy"
    assert response.trace["calculation"]["calculation_id"] == "bridge-run-1"


@pytest.mark.asyncio
async def test_mapped_value_without_unit_converter_returns_conversion_refusal_with_mapping_trace():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            target_unit="kWh",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", unit="GJ"),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=306,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
    )

    assert response.status == "unsupported_conversion"
    assert response.method == "equivalent_mapping"
    assert response.trace["mapping"]["mapping_row_id"] == "306"


@pytest.mark.asyncio
async def test_mapped_value_can_be_converted_after_equivalent_mapping():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
            target_unit="kWh",
            include_trace=True,
        ),
        value_store=_store_with_value(concept="csrd:E3_5", unit="GJ"),
        mapping_store=FakeMappingStore(
            [
                FakeMapping(
                    id=307,
                    source_standard="CSRD",
                    source_code="E3_5",
                    target_standard="GRI",
                    target_code="303_3",
                    relationship_type="equivalent",
                )
            ]
        ),
        unit_converter=SuccessfulConverter(),
    )

    assert response.status == "resolved"
    assert response.method == "mapping_then_conversion"
    assert response.value == Decimal("1.5")
    assert response.unit == "kWh"
    assert response.conversion_steps[0]["formula"] == "fixture-conversion"


@pytest.mark.asyncio
async def test_list_only_value_store_uses_sorted_period_fallback_path():
    older = _value_row(
        concept="csrd:E3_5",
        value=Decimal("1"),
        period=date(2024, 1, 1),
    )
    newer = _value_row(
        concept="csrd:E3_5",
        value=Decimal("2"),
        period=date(2024, 2, 1),
    )
    store = ListOnlyStore([older, newer])

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
        ),
        value_store=store,
    )

    assert response.status == "resolved"
    assert response.value == Decimal("2")
    assert store.calls[0]["limit"] == 100
    assert store.calls[0]["offset"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("period", "granularity", "expected_start", "expected_end"),
    [
        ("2024-02", TemporalGranularity.MONTHLY, date(2024, 2, 1), date(2024, 2, 29)),
        ("2024-Q2", TemporalGranularity.QUARTERLY, date(2024, 4, 1), date(2024, 6, 30)),
        ("2024H2", TemporalGranularity.SEMESTRAL, date(2024, 7, 1), date(2024, 12, 31)),
        ("2024-W3", TemporalGranularity.WEEKLY, date(2024, 1, 15), date(2024, 1, 21)),
        ("2024-03-15", TemporalGranularity.DAILY, date(2024, 3, 15), date(2024, 3, 15)),
    ],
)
async def test_value_resolution_period_bounds_for_supported_granularities(
    period, granularity, expected_start, expected_end
):
    store = LatestOnlyStore(_value_row(concept="csrd:E3_5", value=Decimal("12")))

    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period=period,
            granularity=granularity,
        ),
        value_store=store,
    )

    assert response.status == "resolved"
    assert store.calls[0]["period_start"] == expected_start
    assert store.calls[0]["period_end"] == expected_end


@pytest.mark.asyncio
async def test_calculation_without_trace_keeps_contract_metadata_empty():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="csrd:E3_5",
            entity="plant-a",
            period="2024",
            include_trace=True,
        ),
        value_store=InMemoryValueStore(),
        calculate=_calculation_without_trace,
    )

    assert response.status == "resolved"
    assert response.method == "calculation"
    assert response.contract_id is None
    assert response.contract_version is None
    assert "calculation" not in response.trace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        HTTPException(status_code=500, detail="calculation unavailable"),
        RuntimeError("db down"),
    ],
)
async def test_calculation_failure_does_not_transfer_from_mapping_fallback(failure):
    def broken_calculation(_request):
        raise failure

    request = ValueResolveRequest(
        source_concept="csrd:E3_5",
        target_concept="gri:303_3",
        entity="plant-a",
        period="2024",
    )
    with pytest.raises(type(failure)):
        await resolve_value_request(
            request,
            value_store=_store_with_value(
                concept="csrd:E3_5", value=Decimal("12"), unit="m3"
            ),
            mapping_store=FakeMappingStore(
                [FakeMapping(1, "ESRS", "E3-5", "GRI", "303-3", "equivalent")]
            ),
            calculate=broken_calculation,
        )


@pytest.mark.asyncio
async def test_contradictory_alias_relationships_refuse_numeric_transfer():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(
            concept="csrd:E3_5", value=Decimal("12"), unit="m3"
        ),
        mapping_store=CandidateRouteMappingStore(
            [
                FakeMapping(1, "ESRS", "E3-5", "GRI", "303-3", "equivalent"),
                FakeMapping(2, "CSRD", "E3_5", "GRI", "303_3", "partial"),
            ]
        ),
        calculate=_calculation_raises_http,
    )
    assert response.status == "ambiguous_mapping"


@pytest.mark.asyncio
async def test_contradictory_alias_relationships_refuse_candidate_resolution():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3", entity="plant-a", period="2024"
        ),
        value_store=_store_with_value(
            concept="csrd:E3-5", value=Decimal("12"), unit="m3"
        ),
        mapping_store=CandidateRouteMappingStore(
            [
                FakeMapping(1, "ESRS", "E3-5", "GRI", "303-3", "equivalent"),
                FakeMapping(2, "CSRD", "E3_5", "GRI", "303_3", "partial"),
            ]
        ),
        calculate=_calculation_raises_http,
    )
    assert response.status == "ambiguous_mapping"


@pytest.mark.asyncio
async def test_separator_alias_cannot_hide_conflicting_pair_authority():
    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3-5",
            target_concept="gri:303-3",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(
            concept="csrd:E3-5", value=Decimal("12"), unit="m3"
        ),
        mapping_store=CandidateRouteMappingStore(
            [
                FakeMapping(1, "ESRS", "E3-5", "GRI", "303-3", "equivalent"),
                FakeMapping(2, "CSRD", "E3 5", "GRI", "303 3", "partial"),
            ]
        ),
    )
    assert response.status == "ambiguous_mapping"
    assert response.value is None


@pytest.mark.asyncio
async def test_bounded_candidate_store_cannot_hide_501st_conflicting_route():
    rows = [FakeMapping(1, "ESRS", "E3-5", "GRI", "303-3", "equivalent")]
    rows.extend(
        FakeMapping(i, "ESRS", f"E3-5-{i}", "GRI", "303-3", "partial")
        for i in range(2, 501)
    )
    rows.append(FakeMapping(501, "CSRD", "E3_5", "GRI", "303_3", "partial"))

    class BoundedStore:
        def find_candidate_routes(self, *, limit, **_filters):
            return rows[:limit]

    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3_5",
            target_concept="gri:303_3",
            entity="plant-a",
            period="2024",
        ),
        value_store=_store_with_value(
            concept="csrd:E3_5", value=Decimal("12"), unit="m3"
        ),
        mapping_store=BoundedStore(),
        calculate=_calculation_raises_http,
    )
    assert response.status == "ambiguous_mapping"


@pytest.mark.asyncio
async def test_prefixed_esrs_mapping_source_resolves_stored_sds_urn():
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303_3", entity="plant-a", period="2024"
        ),
        value_store=_store_with_value(
            concept="urn:sds:reg:esrs:e3_5", value=Decimal("12"), unit="m3"
        ),
        mapping_store=CandidateRouteMappingStore(
            [FakeMapping(1, "ESRS", "ESRS E3-5", "GRI", "303-3", "equivalent")]
        ),
        calculate=_calculation_raises_http,
    )
    assert response.status == "resolved"
    assert response.value == Decimal("12")


@pytest.mark.asyncio
async def test_missing_mapping_row_identity_refuses_value_transfer():
    row = SimpleNamespace(
        id=None,
        source_standard="ESRS",
        source_code="E3-5",
        target_standard="GRI",
        target_code="303-3",
        relationship_type="equivalent",
    )

    class MalformedStore:
        def find_candidate_routes(self, **_filters):
            return [row]

    response = await resolve_value_request(
        ValueResolveRequest(
            source_concept="csrd:E3-5",
            target_concept="gri:303-3",
            entity="plant-a",
            period="2024",
            unit="m3",
        ),
        value_store=_store_with_value(concept="csrd:E3-5", unit="m3"),
        mapping_store=MalformedStore(),
        calculate=_calculation_raises_http,
    )
    assert response.status == "ambiguous_mapping"


@pytest.mark.asyncio
async def test_mapping_source_without_code_cannot_resolve_empty_concept_value():
    row = FakeMapping(31, "ESRS", "", "GRI", "303-3", "equivalent")
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:303-3", entity="plant-a", period="2024", unit="m3"
        ),
        value_store=_store_with_value(concept="csrd:", unit="m3"),
        mapping_store=CandidateRouteMappingStore([row]),
        calculate=_calculation_raises_http,
    )
    assert response.status == "ambiguous_mapping"


@pytest.mark.asyncio
async def test_explicit_esrs_source_resolves_value_stored_under_csrd_alias():
    row = FakeMapping(1, "ESRS", "E1-5_12", "GRI", "302-1.e", "equivalent")
    response = await resolve_value_request(
        ValueResolveRequest(
            target_concept="gri:302-1.e",
            source_concept="esrs:E1-5_12",
            entity="plant-a",
            period="2024",
            unit="MWh",
        ),
        value_store=_store_with_value(
            concept="csrd:E1-5_12", value=Decimal("12"), unit="MWh"
        ),
        mapping_store=CandidateRouteMappingStore([row]),
        calculate=_calculation_raises_http,
    )

    assert response.status == "resolved"
    assert response.value == Decimal("12")
