from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, SKOS

from src.api.models import ValueCreate
from src.calculation.conversion.orchestrator import ConversionDependencyError
from src.calculation.unit_converter import UnitConversionError
from src.calculation.value_provider import StoreObservationProvider
from src.ontology.curie import DEFAULT_NAMESPACES
from src.services.value_ingest import (
    SDS,
    PreparedValueRecord,
    ValueIngestError,
    _get_standard_unit_for_concept,
    _matches_calculation_value_concept,
    _normalize_value_type,
    _unit_reference_token,
    prepare_value_record,
)
from src.services.value_store import InMemoryValueStore


class _FakeConversionEngine:
    def __init__(self) -> None:
        self.requests = []

    def normalize(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            value=Decimal("90.000000"),
            unit="MWh",
            currency="EUR",
            trace=[
                {
                    "step_type": "fx",
                    "from_currency": "USD",
                    "to_currency": "EUR",
                    "rate": "0.9",
                }
            ],
        )


def test_prepare_value_record_preserves_conversion_trace_and_row_dates():
    engine = _FakeConversionEngine()

    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="syg:Energy_Spend",
            entity="plant-1",
            period="2024-03-31",
            value=100,
            unit="kWh",
            currency="usd",
            expected_unit="MWh",
            expected_currency="eur",
            fx_policy_id="monthly-average",
            value_date="2024-03-15",
            period_start="2024-03-01",
            period_end="2024-03-31",
        ),
        converter=object(),
        conversion_engine=engine,
        hierarchy_store=object(),
        graph=Graph(),
        company_id=None,
        strict=False,
    )

    assert record.value == Decimal("90.000000")
    assert record.unit == "MWh"
    assert record.currency == "EUR"
    assert record.original_value == 100
    assert record.original_unit == "kWh"
    assert record.original_currency == "USD"
    assert record.conversion_applied is True
    assert record.currency_conversion_applied is True
    assert record.conversion_trace == [
        {
            "step_type": "fx",
            "from_currency": "USD",
            "to_currency": "EUR",
            "rate": "0.9",
        }
    ]
    assert record.value_date == date(2024, 3, 15)
    assert record.period_start == date(2024, 3, 1)
    assert record.period_end == date(2024, 3, 31)

    request = engine.requests[0]
    assert request.value == Decimal("100")
    assert request.unit == "kWh"
    assert request.expected_unit == "MWh"
    assert request.currency == "USD"
    assert request.expected_currency == "EUR"
    assert request.value_date == date(2024, 3, 15)
    assert request.period_start == date(2024, 3, 1)
    assert request.period_end == date(2024, 3, 31)
    assert request.fx_policy_id == "monthly-average"


def test_expected_currency_without_conversion_engine_fails_closed_when_source_differs():
    with pytest.raises(ValueIngestError, match="conversion_engine is required"):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="syg:Energy_Spend",
                entity="plant-1",
                period="2024-03-31",
                value=100,
                unit="kWh",
                currency="USD",
                expected_currency="EUR",
            ),
            converter=object(),
            hierarchy_store=object(),
            graph=Graph(),
            company_id=None,
            strict=False,
        )


def test_store_observation_provider_carries_row_currency_and_date_metadata():
    updated_at = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id="value-1",
        concept="syg:Energy_Spend",
        entity="plant-1",
        period=date(2024, 3, 31),
        value=Decimal("90.0"),
        value_type="numeric",
        unit="MWh",
        currency="EUR",
        original_currency="USD",
        value_date=date(2024, 3, 15),
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        conversion_trace=[{"step_type": "fx"}],
        updated_at=updated_at,
    )
    store = SimpleNamespace(
        list=lambda **_kwargs: ([row], 1),
    )
    provider = StoreObservationProvider(store)

    observations = provider.get_observations(
        SimpleNamespace(local_variable="energy_spend", concept="syg:Energy_Spend"),
        entities=["plant-1"],
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.currency == "EUR"
    assert observation.original_currency == "USD"
    assert observation.value_date == date(2024, 3, 15)
    assert observation.period_start == date(2024, 3, 1)
    assert observation.period_end == date(2024, 3, 31)
    assert observation.conversion_trace == [{"step_type": "fx"}]


def test_provider_does_not_use_contract_expected_currency_as_row_currency():
    row = SimpleNamespace(
        id="value-1",
        concept="syg:Energy_Spend",
        entity="plant-1",
        period=date(2024, 3, 31),
        value=Decimal("100.0"),
        value_type="numeric",
        unit="kWh",
        currency="USD",
        original_currency=None,
        value_date=None,
        period_start=None,
        period_end=None,
        conversion_trace=None,
        updated_at=None,
    )
    store = SimpleNamespace(list=lambda **_kwargs: ([row], 1))
    provider = StoreObservationProvider(store)

    observation = provider.get_observations(
        SimpleNamespace(
            local_variable="energy_spend",
            concept="syg:Energy_Spend",
            expected_currency="EUR",
        ),
        entities=["plant-1"],
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
    )[0]

    assert observation.currency == "USD"


def test_provider_prefers_store_iterate_over_offset_pages():
    row = SimpleNamespace(
        id="value-1",
        concept="syg:Energy_Spend",
        entity="plant-1",
        period=date(2024, 3, 31),
        value=Decimal("100.0"),
        value_type="numeric",
        unit="kWh",
        currency="USD",
        original_currency=None,
        value_date=None,
        period_start=None,
        period_end=None,
        conversion_trace=None,
        updated_at=None,
    )

    class IteratingStore:
        def __init__(self):
            self.iterate_calls = []

        def iterate(self, **kwargs):
            self.iterate_calls.append(kwargs)
            return iter([row])

        def list(self, **_kwargs):
            raise AssertionError("iterate-capable stores should not use list pages")

    store = IteratingStore()
    provider = StoreObservationProvider(store)

    observations = provider.get_observations(
        SimpleNamespace(local_variable="energy_spend", concept="syg:Energy_Spend"),
        entities=["plant-1"],
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
    )

    assert [item.value_id for item in observations] == ["value-1"]
    assert len(store.iterate_calls) == 1


def test_provider_deduplicates_revision_rows_by_projected_value_id():
    older = SimpleNamespace(
        id="value-1",
        concept="syg:Energy_Spend",
        entity="plant-1",
        period=date(2024, 3, 31),
        value=Decimal("100.0"),
        value_type="numeric",
        unit="kWh",
        currency=None,
        original_currency=None,
        value_date=None,
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
        conversion_trace=None,
        updated_at=datetime(2024, 4, 1, tzinfo=timezone.utc),
    )
    newer = SimpleNamespace(
        **{
            **older.__dict__,
            "value": Decimal("101.0"),
            "updated_at": datetime(2024, 4, 2, tzinfo=timezone.utc),
        }
    )

    class IteratingStore:
        def iterate(self, **_kwargs):
            return iter([older, newer])

    provider = StoreObservationProvider(IteratingStore())

    observations = provider.get_observations(
        SimpleNamespace(local_variable="energy_spend", concept="syg:Energy_Spend"),
        entities=["plant-1"],
        period_start=date(2024, 3, 1),
        period_end=date(2024, 3, 31),
    )

    assert [item.value_id for item in observations] == ["value-1"]
    assert observations[0].value == Decimal("101.0")


def test_in_memory_value_store_round_trips_conversion_provenance_fields():
    store = InMemoryValueStore()
    response = store.save(
        records=[
            PreparedValueRecord(
                value_id="value-1",
                concept="syg:Energy_Spend",
                entity="plant-1",
                period=date(2024, 3, 31),
                value=Decimal("90.0"),
                value_type="numeric",
                unit="MWh",
                original_value=Decimal("100.0"),
                original_unit="kWh",
                conversion_applied=True,
                metadata={"source": "test"},
                external_key="erp:value-1",
                currency="EUR",
                original_currency="USD",
                currency_conversion_applied=True,
                conversion_trace=[{"step_type": "fx"}],
                value_date=date(2024, 3, 15),
                period_start=date(2024, 3, 1),
                period_end=date(2024, 3, 31),
            )
        ],
        created_by="pytest",
    )[0]

    assert response.value == Decimal("90.0")
    assert response.original_value == Decimal("100.0")
    assert response.currency == "EUR"
    assert response.original_currency == "USD"
    assert response.currency_conversion_applied is True
    assert response.conversion_trace == [{"step_type": "fx"}]
    assert response.value_date == date(2024, 3, 15)
    assert response.period_start == date(2024, 3, 1)
    assert response.period_end == date(2024, 3, 31)


def test_prepare_value_record_rejects_explicit_numeric_non_numeric_value():
    with pytest.raises(ValueIngestError, match="Numeric values"):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="syg:Energy",
                entity="plant-1",
                period="2024-03-31",
                value=True,
                value_type="numeric",
                unit="kWh",
            ),
            converter=object(),
            hierarchy_store=object(),
            graph=Graph(),
            company_id=None,
            strict=False,
        )


@pytest.mark.parametrize(
    "error",
    [ValueError("bad conversion"), ConversionDependencyError("missing rate")],
)
def test_prepare_value_record_wraps_conversion_engine_failures(error):
    class _FailingConversionEngine:
        def normalize(self, _request):
            raise error

    with pytest.raises(ValueIngestError, match=str(error)):
        prepare_value_record(
            value_id="value-1",
            value_data=ValueCreate(
                concept="syg:Energy",
                entity="plant-1",
                period="2024-03-31",
                value=100,
                unit="kWh",
                expected_unit="MWh",
            ),
            converter=object(),
            conversion_engine=_FailingConversionEngine(),
            hierarchy_store=object(),
            graph=Graph(),
            company_id=None,
            strict=False,
        )


def test_prepare_value_record_falls_back_to_row_unit_when_lenient_conversion_fails():
    class _FailingConverter:
        def convert(self, *_args):
            raise UnitConversionError("incompatible")

    record = prepare_value_record(
        value_id="value-1",
        value_data=ValueCreate(
            concept="syg:Energy",
            entity="plant-1",
            period="2024-03-31",
            value=100,
            unit="kWh",
            expected_unit="MWh",
        ),
        converter=_FailingConverter(),
        hierarchy_store=object(),
        graph=Graph(),
        company_id=None,
        strict=False,
    )

    assert record.unit == "kWh"
    assert record.conversion_applied is False


def test_value_ingest_private_helpers_cover_dict_and_unit_edges():
    assert _matches_calculation_value_concept(
        "urn:sds:reg:test", {"indicator_identifier": "urn:sds:reg:test"}
    )
    assert not _matches_calculation_value_concept("urn:sds:reg:test", None)
    assert _normalize_value_type(None, False) == "boolean"
    with pytest.raises(ValueIngestError, match="value_type must be one of"):
        _normalize_value_type("unsupported", "value")

    graph = Graph()
    concept = URIRef(DEFAULT_NAMESPACES.expand("syg:Energy"))
    unit = URIRef("https://example.test/unit/MWh")
    graph.add((concept, RDF.type, SDS.Disclosure))
    graph.add((concept, SDS.hasUnit, unit))
    assert _get_standard_unit_for_concept("syg:Energy", graph, object()) is None

    graph.add((unit, SKOS.altLabel, Literal("bad-unit")))

    class _BadNormalizer:
        def normalize_unit_symbol(self, _symbol):
            raise UnitConversionError("unknown unit")

    assert _get_standard_unit_for_concept("syg:Energy", graph, _BadNormalizer()) is None


def test_unit_reference_token_preserves_local_compound_unit_symbols():
    assert (
        _unit_reference_token(
            "file:///D:/AI%20Projects/sustainabilityDataSpace/api/ontologies/kg CO2e/kg"
        )
        == "kg CO2e/kg"
    )


def test_standard_unit_lookup_preserves_ghg_compound_ontology_units():
    graph = Graph()
    concept = URIRef(DEFAULT_NAMESPACES.expand("syg:EmissionFactor"))
    unit = URIRef(
        "file:///D:/AI%20Projects/sustainabilityDataSpace/api/ontologies/kg CO2e/kg"
    )
    graph.add((concept, RDF.type, SDS.Disclosure))
    graph.add((concept, SDS.hasUnit, unit))

    class _CompoundUnitNormalizer:
        def normalize_unit_symbol(self, symbol):
            if symbol == "kg CO2e/kg":
                return symbol
            raise UnitConversionError(f"unknown unit: {symbol}")

    assert (
        _get_standard_unit_for_concept(
            "syg:EmissionFactor", graph, _CompoundUnitNormalizer()
        )
        == "kg CO2e/kg"
    )


def test_standard_unit_lookup_keeps_concrete_ghg_unit_when_catalog_is_minimal():
    graph = Graph()
    concept = URIRef(DEFAULT_NAMESPACES.expand("syg:EmissionFactor"))
    unit = URIRef(
        "file:///D:/AI%20Projects/sustainabilityDataSpace/api/ontologies/kg CO2e/kg"
    )
    graph.add((concept, RDF.type, SDS.Disclosure))
    graph.add((concept, SDS.hasUnit, unit))

    class _MinimalNormalizer:
        def normalize_unit_symbol(self, _symbol):
            raise UnitConversionError("unknown unit")

    assert (
        _get_standard_unit_for_concept(
            "syg:EmissionFactor", graph, _MinimalNormalizer()
        )
        == "kg CO2e/kg"
    )
