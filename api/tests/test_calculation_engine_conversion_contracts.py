from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    ContractExecutionError,
    ContractResolutionError,
    ContractResolutionMetadata,
)
from src.calculation.engine import CalculationContext, CalculationEngine
from src.calculation.value_provider import ObservationRecord, StaticObservationProvider


def _context(entity: str = "group") -> CalculationContext:
    return CalculationContext(
        entity_id=entity,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        temporal_granularity="annual",
        organizational_level=1,
    )


def _contract(
    *,
    formula: str,
    inputs: list[CalculationContractInput],
    contract_id: str = "contract-fx",
    result_unit: str = "EURm",
    result_currency: str | None = "EUR",
) -> CalculationContract:
    return CalculationContract(
        contract_id=contract_id,
        concept="finance:intensity",
        contract_version="2026.05",
        contract_hash="sha256:fx",
        runtime_status="executable",
        formula=formula,
        result_unit=result_unit,
        result_currency=result_currency,
        inputs=tuple(inputs),
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _obs(
    value_id: str,
    *,
    concept: str = "finance:revenue",
    entity: str = "group",
    value: Decimal | int | str = 1,
    unit: str = "EURm",
    currency: str | None = None,
    period: date = date(2024, 1, 1),
) -> ObservationRecord:
    return ObservationRecord(
        value_id=value_id,
        concept=concept,
        entity=entity,
        period=period,
        value=Decimal(str(value)),
        unit=unit,
        value_type="numeric",
        currency=currency,
        value_date=period,
        period_start=period,
        period_end=period,
        updated_at=datetime.now(timezone.utc),
    )


@dataclass
class FakeConversionEngine:
    rates: dict[str, Decimal] = field(
        default_factory=lambda: {"GBP": Decimal("1.2"), "USD": Decimal("0.9")}
    )
    requests: list[Any] = field(default_factory=list)

    def normalize(self, request):
        self.requests.append(request)
        value = Decimal(str(request.value))
        trace = []
        if request.currency != request.expected_currency:
            value *= self.rates[request.currency]
            trace.append(
                {
                    "step_type": "fx",
                    "from_currency": request.currency,
                    "to_currency": request.expected_currency,
                    "rate_observation_id": f"fx-{request.currency}-EUR",
                }
            )
        return _Normalized(
            value=value,
            unit=request.expected_unit or request.unit,
            currency=request.expected_currency or request.currency,
            trace=trace,
        )


@dataclass(frozen=True)
class _Normalized:
    value: Decimal
    unit: str | None
    currency: str | None
    trace: list[dict[str, Any]]


class _Resolver:
    def __init__(self, contracts: dict[str, CalculationContract]):
        self._contracts = contracts

    def resolve(self, concept: str) -> CalculationContract:
        try:
            return self._contracts[concept]
        except KeyError as exc:
            raise ContractResolutionError(concept) from exc


def test_contract_normalizes_currency_before_aggregation_with_production_input():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            )
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("r1", value=100, currency="GBP"),
            _obs("r2", value=200, currency="GBP"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
        conversion_engine=FakeConversionEngine(),
    )

    assert result.value == Decimal("360.00")
    assert [
        step["rate_observation_id"] for step in result.trace.conversions_applied
    ] == [
        "fx-GBP-EUR",
        "fx-GBP-EUR",
    ]


def test_contract_converts_mixed_row_currencies_per_row_before_aggregation():
    engine = FakeConversionEngine()
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            )
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("gbp", value=100, currency="GBP"),
            _obs("usd", value=100, currency="USD"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
        conversion_engine=engine,
    )

    assert result.value == Decimal("210.00")
    assert [request.currency for request in engine.requests] == ["GBP", "USD"]
    assert [step["from_currency"] for step in result.trace.conversions_applied] == [
        "GBP",
        "USD",
    ]


def test_result_currency_without_input_fx_policy_fails_closed():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
            )
        ],
        result_currency="EUR",
    )
    provider = StaticObservationProvider([_obs("gbp", value=100, currency="GBP")])

    with pytest.raises(
        ContractExecutionError, match="expected_currency and fx_policy_id"
    ):
        CalculationEngine(precision=2).calculate_contract(
            contract,
            _context(),
            provider,
            conversion_engine=FakeConversionEngine(),
        )


def test_mixed_row_currencies_without_target_currency_fails_closed():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
            )
        ],
        result_currency=None,
    )
    provider = StaticObservationProvider(
        [
            _obs("gbp", value=100, currency="GBP"),
            _obs("usd", value=100, currency="USD"),
        ]
    )

    with pytest.raises(ContractExecutionError, match="mixed-currency observations"):
        CalculationEngine(precision=2).calculate_contract(
            contract,
            _context(),
            provider,
            conversion_engine=FakeConversionEngine(),
        )


def test_mixed_row_currencies_fail_closed_even_without_conversion_engine():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
            )
        ],
        result_currency=None,
    )
    provider = StaticObservationProvider(
        [
            _obs("gbp", value=100, currency="GBP"),
            _obs("usd", value=100, currency="USD"),
        ]
    )

    with pytest.raises(ContractExecutionError, match="mixed-currency observations"):
        CalculationEngine(precision=2).calculate_contract(
            contract,
            _context(),
            provider,
        )


def test_fx_required_without_conversion_engine_fails_closed():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            )
        ],
        result_currency="EUR",
    )
    provider = StaticObservationProvider([_obs("gbp", value=100, currency="GBP")])

    with pytest.raises(ContractExecutionError, match="conversion_engine is required"):
        CalculationEngine(precision=2).calculate_contract(
            contract,
            _context(),
            provider,
        )


def test_nested_result_currency_mismatch_without_input_policy_fails_closed():
    nested = _contract(
        formula="revenue",
        contract_id="nested-fx",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue_raw",
                unit="EURm",
            )
        ],
        result_currency="GBP",
    )
    parent = _contract(
        formula="nested_revenue",
        contract_id="parent-fx",
        inputs=[
            CalculationContractInput(
                "nested_revenue",
                "finance:revenue",
                unit="EURm",
            )
        ],
        result_currency="EUR",
    )
    provider = StaticObservationProvider(
        [_obs("gbp", concept="finance:revenue_raw", value=100, currency="GBP")]
    )

    with pytest.raises(ContractExecutionError, match="parent result_currency"):
        CalculationEngine(precision=2).calculate_contract(
            parent,
            _context(),
            provider,
            contract_resolver=_Resolver({"finance:revenue": nested}),
        )


def test_weighted_average_uses_converted_rows_without_double_conversion():
    engine = FakeConversionEngine()
    contract = _contract(
        formula="weighted_average(revenue, weight)",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            ),
            CalculationContractInput("weight", "ops:weight", unit="t"),
        ],
        result_currency="EUR",
    )
    provider = StaticObservationProvider(
        [
            _obs("r1", value=100, currency="GBP", period=date(2024, 1, 1)),
            _obs("r2", value=100, currency="USD", period=date(2024, 2, 1)),
            _obs(
                "w1",
                concept="ops:weight",
                value=2,
                unit="t",
                period=date(2024, 1, 1),
            ),
            _obs(
                "w2",
                concept="ops:weight",
                value=1,
                unit="t",
                period=date(2024, 2, 1),
            ),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
        conversion_engine=engine,
    )

    currency_requests = [request for request in engine.requests if request.currency]
    assert result.value == Decimal("110.00")
    assert [request.currency for request in currency_requests] == ["GBP", "USD"]
    assert [request.expected_currency for request in currency_requests] == [
        "EUR",
        "EUR",
    ]


def test_row_currency_beats_component_currency_hint():
    engine = FakeConversionEngine()
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                currency="USD",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            )
        ],
    )
    provider = StaticObservationProvider([_obs("r1", value=100, currency="GBP")])

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
        conversion_engine=engine,
    )

    assert engine.requests[0].currency == "GBP"
    assert result.value == Decimal("120.00")
    assert result.trace.warnings == [
        "revenue/r1: observation currency GBP overrides component currency hint USD"
    ]


def test_expected_currency_with_missing_row_currency_fails_closed():
    contract = _contract(
        formula="revenue",
        inputs=[
            CalculationContractInput(
                "revenue",
                "finance:revenue",
                unit="EURm",
                currency="GBP",
                expected_currency="EUR",
                fx_policy_id="ecb-monthly",
            )
        ],
    )
    provider = StaticObservationProvider([_obs("r1", value=100, currency=None)])

    with pytest.raises(ContractExecutionError, match="source currency is required"):
        CalculationEngine(precision=2).calculate_contract(
            contract,
            _context(),
            provider,
            conversion_engine=FakeConversionEngine(),
        )
