from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.calculation.contracts import (
    CalculationContract,
    CalculationContractInput,
    CalculationContractNotFoundError,
    ContractExecutionError,
    ContractResolutionError,
    ContractResolutionMetadata,
    DerivedCalculationNode,
)
from src.calculation.engine import (
    CalculationContext,
    CalculationEngine,
    CalculationError,
    CalculationResult,
    CalculationTrace,
)
from src.calculation.value_provider import (
    ObservationRecord,
    ObservationSnapshot,
    StaticObservationProvider,
)


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
    contract_id: str = "contract-1",
    concept: str = "csrd:E3_5",
    runtime_status: str = "executable",
    nodes: list[DerivedCalculationNode] | None = None,
    hierarchy_config_id: str | None = None,
    hierarchy: dict[str, list[str]] | None = None,
    contract_hash: str = "sha256:contract-a",
    result_unit: str = "kg",
    result_currency: str | None = None,
    missing_data: str | None = None,
) -> CalculationContract:
    return CalculationContract(
        contract_id=contract_id,
        concept=concept,
        contract_version="2026.05",
        contract_hash=contract_hash,
        runtime_status=runtime_status,
        formula=formula,
        result_unit=result_unit,
        result_currency=result_currency,
        missing_data=missing_data,
        inputs=tuple(inputs),
        derived_nodes=tuple(nodes or ()),
        hierarchy_config_id=hierarchy_config_id,
        hierarchy=hierarchy or {},
        resolution=ContractResolutionMetadata(source="canonical_db"),
    )


def _obs(
    value_id: str,
    *,
    concept: str,
    entity: str = "group",
    value: Decimal | int | str = 1,
    unit: str = "kg",
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
        updated_at=datetime.now(timezone.utc),
    )


@dataclass
class FakeUnitNormalizer:
    factors: dict[tuple[str, str], Decimal]

    def normalize(self, value: Decimal, from_unit: str, to_unit: str):
        if from_unit == to_unit:
            return value, None
        factor = self.factors[(from_unit, to_unit)]
        return value * factor, {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "factor": str(factor),
        }


class FakeContractResolver:
    def __init__(self, contracts: dict[str, CalculationContract]):
        self.contracts = contracts

    def resolve(self, concept: str) -> CalculationContract:
        return self.contracts[concept]


def _nested_result(
    *,
    value: Decimal = Decimal("10"),
    unit: str = "kg",
    currency: str | None = None,
) -> CalculationResult:
    return CalculationResult(
        value=value,
        unit=unit,
        context=_context(),
        formula_used="child",
        input_values={},
        calculation_timestamp=datetime.now(timezone.utc),
        trace=CalculationTrace(contract_id="child-contract"),
        currency=currency,
    )


def test_contract_formula_rejects_non_numeric_result():
    contract = _contract(
        formula='"not-a-number"',
        inputs=[CalculationContractInput("value", "input", unit="kg")],
    )
    provider = StaticObservationProvider([_obs("v1", concept="input", value=1)])

    with pytest.raises(ContractExecutionError, match="numeric"):
        CalculationEngine().calculate_contract(contract, _context(), provider)


def test_formula_validation_rejects_resource_amplifying_exponents():
    with pytest.raises(CalculationError, match="exponent"):
        CalculationEngine()._validate_formula("10 ** 1000")

    with pytest.raises(CalculationError, match="exponent"):
        CalculationEngine()._validate_formula("pow(10, 1000)")


class FakeConvertHook:
    def convert(self, value: Decimal, from_unit: str, to_unit: str):
        return SimpleNamespace(
            converted_value=value / Decimal("1000"),
            conversion_factor=Decimal("0.001"),
        )


def test_nested_contract_normalization_currency_and_unit_edge_branches():
    engine = CalculationEngine(precision=2)
    expected_eur = CalculationContractInput(
        "child_value",
        "child:concept",
        unit="kg",
        expected_currency="EUR",
        fx_policy_id="ecb-monthly",
    )
    plain_kg = CalculationContractInput("child_value", "child:concept", unit="kg")

    with pytest.raises(ContractExecutionError, match="source currency"):
        engine._normalize_nested_value(
            _nested_result(currency=None),
            expected_eur,
            unit_normalizer=None,
        )

    with pytest.raises(ContractExecutionError, match="parent result_currency"):
        engine._normalize_nested_value(
            _nested_result(currency="GBP"),
            plain_kg,
            unit_normalizer=None,
            parent_result_currency="EUR",
        )

    with pytest.raises(ContractExecutionError, match="conversion_engine"):
        engine._normalize_nested_value(
            _nested_result(currency="GBP"),
            expected_eur,
            unit_normalizer=None,
        )

    conversion_engine = SimpleNamespace(
        normalize=lambda request: SimpleNamespace(
            value=Decimal("11"),
            trace=[
                {
                    "kind": "fx",
                    "from": request.currency,
                    "to": request.expected_currency,
                }
            ],
        )
    )
    converted, detail = engine._normalize_nested_value(
        _nested_result(currency="GBP"),
        expected_eur,
        unit_normalizer=None,
        conversion_engine=conversion_engine,
    )
    assert converted == Decimal("11")
    assert detail == {
        "nested_contract": "child-contract",
        "trace": [{"kind": "fx", "from": "GBP", "to": "EUR"}],
    }

    failing_conversion_engine = SimpleNamespace(
        normalize=lambda _request: (_ for _ in ()).throw(ValueError("fx missing"))
    )
    with pytest.raises(ContractExecutionError, match="conversion failed"):
        engine._normalize_nested_value(
            _nested_result(currency="GBP"),
            expected_eur,
            unit_normalizer=None,
            conversion_engine=failing_conversion_engine,
        )

    with pytest.raises(ContractExecutionError, match="unit normalizer"):
        engine._normalize_nested_value(
            _nested_result(unit="g"),
            plain_kg,
            unit_normalizer=None,
        )

    converted, detail = engine._normalize_nested_value(
        _nested_result(value=Decimal("1000"), unit="g"),
        plain_kg,
        unit_normalizer=FakeUnitNormalizer({("g", "kg"): Decimal("0.001")}),
    )
    assert converted == Decimal("1.000")
    assert detail == {"from_unit": "g", "to_unit": "kg", "factor": "0.001"}

    converted, detail = engine._normalize_nested_value(
        _nested_result(value=Decimal("1000"), unit="g"),
        plain_kg,
        unit_normalizer=FakeConvertHook(),
    )
    assert converted == Decimal("1")
    assert detail == {"from_unit": "g", "to_unit": "kg", "factor": "0.001"}

    with pytest.raises(ContractExecutionError, match="Invalid unit normalizer"):
        engine._normalize_nested_value(
            _nested_result(unit="g"),
            plain_kg,
            unit_normalizer=object(),
        )


def test_weighted_average_error_and_observation_normalization_branches():
    engine = CalculationEngine(precision=2)
    contract = _contract(
        formula="weighted_average(score, headcount)",
        inputs=[
            CalculationContractInput("score", "score", unit="points"),
            CalculationContractInput("headcount", "headcount", unit="FTE"),
        ],
        result_unit="points",
    )
    score_input, headcount_input = contract.inputs
    context = _context()

    with pytest.raises(ContractExecutionError, match="backed by contract inputs"):
        engine._try_execute_weighted_average_formula(
            contract,
            context,
            inputs_by_variable={"score": score_input},
            raw_observations_by_variable={},
            unit_normalizer=None,
        )

    with pytest.raises(ContractExecutionError, match="raw observations"):
        engine._try_execute_weighted_average_formula(
            contract,
            context,
            inputs_by_variable={"score": score_input, "headcount": headcount_input},
            raw_observations_by_variable={"score": []},
            unit_normalizer=None,
        )

    with pytest.raises(ContractExecutionError, match="paired observations"):
        engine._try_execute_weighted_average_formula(
            contract,
            context,
            inputs_by_variable={"score": score_input, "headcount": headcount_input},
            raw_observations_by_variable={
                "score": [_obs("s1", concept="score", entity="plant-a", unit="points")],
                "headcount": [
                    _obs("h1", concept="headcount", entity="plant-b", unit="FTE")
                ],
            },
            unit_normalizer=None,
        )

    with pytest.raises(ContractExecutionError, match="denominator is zero"):
        engine._try_execute_weighted_average_formula(
            contract,
            context,
            inputs_by_variable={"score": score_input, "headcount": headcount_input},
            raw_observations_by_variable={
                "score": [_obs("s1", concept="score", value=10, unit="points")],
                "headcount": [_obs("h1", concept="headcount", value=0, unit="FTE")],
            },
            unit_normalizer=None,
        )

    assert engine._format_weighted_keys(set()) == "none"
    assert engine._format_weighted_keys({("plant-a", date(2024, 1, 1))}) == (
        "plant-a/2024-01-01"
    )

    with pytest.raises(ContractExecutionError, match="Mixed units"):
        engine._normalize_contract_observation_value(
            _obs("s2", concept="score", value=1000, unit="g"),
            score_input,
            unit_normalizer=None,
        )
    assert engine._normalize_contract_observation_value(
        _obs("s3", concept="score", value=1000, unit="g"),
        score_input,
        unit_normalizer=FakeUnitNormalizer({("g", "points"): Decimal("0.001")}),
    ) == Decimal("1.000")
    assert engine._normalize_contract_observation_value(
        _obs("s4", concept="score", value=1000, unit="g"),
        score_input,
        unit_normalizer=FakeConvertHook(),
    ) == Decimal("1")
    with pytest.raises(ContractExecutionError, match="Invalid unit normalizer"):
        engine._normalize_contract_observation_value(
            _obs("s5", concept="score", value=1000, unit="g"),
            score_input,
            unit_normalizer=object(),
        )


def test_formula_validation_variable_and_ast_edge_branches():
    import ast

    engine = CalculationEngine(precision=2)

    with pytest.raises(CalculationError, match="non-empty string"):
        engine._validate_formula("")
    with pytest.raises(CalculationError, match="Unsafe operator"):
        engine._validate_formula("a << b")
    with pytest.raises(CalculationError, match="Unsafe unary operator"):
        engine._validate_formula("~a")
    with pytest.raises(CalculationError, match="simple function"):
        engine._validate_formula("obj.method()")
    with pytest.raises(CalculationError, match="Unsafe AST node"):
        engine._validate_formula("[1, a]")
    with pytest.raises(CalculationError, match="Unsafe AST node"):
        engine._check_ast_safety(ast.parse("{'a': 1}", mode="eval"))
    engine._check_ast_safety(ast.parse("-a", mode="eval"))

    with pytest.raises(CalculationError, match="dictionary"):
        engine._validate_variables([("a", 1)])
    with pytest.raises(CalculationError, match="Variable name"):
        engine._validate_variables({1: 2})
    with pytest.raises(CalculationError, match="non-numeric"):
        engine._validate_variables({"values": [1, "bad"]})

    assert engine._evaluate_formula("-a + +b + max([1, 2])", {"a": 1, "b": 2}) == 3
    with pytest.raises(CalculationError, match="Unsupported AST node"):
        engine._eval_ast_node(ast.parse("{'a': 1}", mode="eval").body, {})


@pytest.mark.parametrize(
    ("formula", "reason"),
    [
        ("pow(a)", "requires two arguments"),
        ("max(a, b=2)", "Keyword arguments"),
        ("1e999", "non-finite"),
        ("1e101", "supported bounds"),
        ("a if b else 0", "Unsafe AST node"),
    ],
)
def test_formula_validator_refuses_unbounded_or_dynamic_syntax(formula, reason):
    engine = CalculationEngine()
    with pytest.raises(CalculationError, match=reason):
        engine._validate_formula(formula)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), float("inf")])
def test_formula_validator_refuses_nonfinite_runtime_inputs(value):
    engine = CalculationEngine()
    with pytest.raises(CalculationError, match="non-finite"):
        engine._validate_variables({"a": value})


def test_additive_parent_annual_sum_uses_contract_hierarchy_and_lineage():
    contract = _contract(
        formula="stationary + mobile",
        hierarchy_config_id="org-2024",
        hierarchy={"group": ["plant-a", "plant-b"]},
        inputs=[
            CalculationContractInput(
                local_variable="stationary",
                concept="ghg:stationary",
                unit="kg",
                entity_scope="children",
                temporal_aggregation="sum",
                perimeter_aggregation="sum",
            ),
            CalculationContractInput(
                local_variable="mobile",
                concept="ghg:mobile",
                unit="kg",
                entity_scope="children",
                temporal_aggregation="sum",
                perimeter_aggregation="sum",
            ),
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("v1", concept="ghg:stationary", entity="plant-a", value=10),
            _obs("v2", concept="ghg:stationary", entity="plant-b", value=5),
            _obs("v3", concept="ghg:mobile", entity="plant-a", value=2),
            _obs("v4", concept="ghg:mobile", entity="plant-b", value=3),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("20.00")
    assert result.input_values == {"stationary": Decimal("15"), "mobile": Decimal("5")}
    assert result.trace.contract_hash == "sha256:contract-a"
    assert result.trace.contract_version == "2026.05"
    assert result.trace.resolver_source == "canonical_db"
    assert result.trace.source_value_ids == ["v1", "v2", "v3", "v4"]
    assert result.trace.aggregation_policy == {
        "stationary": "temporal=sum;perimeter=sum;entities=children",
        "mobile": "temporal=sum;perimeter=sum;entities=children",
    }
    assert result.trace.hierarchy_config_id == "org-2024"
    assert result.trace.completeness["status"] == "complete"


def test_static_provider_returns_typed_snapshot_without_presumming():
    contract_input = CalculationContractInput(
        local_variable="water",
        concept="syg:water",
        unit="m3",
    )
    provider = StaticObservationProvider(
        [
            _obs("w1", concept="syg:water", value=1, unit="m3"),
            _obs("w2", concept="syg:water", value=2, unit="m3"),
        ]
    )

    snapshot = provider.get_snapshot(
        contract_input,
        entities=["group"],
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )

    assert isinstance(snapshot, ObservationSnapshot)
    assert snapshot.local_variable == "water"
    assert [item.value_id for item in snapshot.observations] == ["w1", "w2"]


def test_ratio_contract_recomputes_from_aggregated_inputs():
    contract = _contract(
        formula="emissions / revenue",
        inputs=[
            CalculationContractInput("emissions", "ghg:scope1", unit="kg"),
            CalculationContractInput("revenue", "finance:revenue", unit="eur"),
        ],
        result_unit="kg/eur",
    )
    provider = StaticObservationProvider(
        [
            _obs("e1", concept="ghg:scope1", value=50),
            _obs("e2", concept="ghg:scope1", value=50),
            _obs("r1", concept="finance:revenue", value=200, unit="eur"),
            _obs("r2", concept="finance:revenue", value=800, unit="eur"),
        ]
    )

    result = CalculationEngine(precision=4).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("0.1000")
    assert result.formula_used == "emissions / revenue"


def test_contract_runtime_recursively_calculates_missing_calculated_component():
    total_contract = _contract(
        formula="stationary + mobile",
        contract_id="contract-total",
        concept="urn:sds:reg:test:scope1_total_emissions",
        inputs=[
            CalculationContractInput("stationary", "fixture_stationary", unit="tCO2e"),
            CalculationContractInput("mobile", "fixture_mobile", unit="tCO2e"),
        ],
        contract_hash="sha256:total",
        result_unit="tCO2e",
    )
    ratio_contract = _contract(
        formula="scope1_total / revenue",
        contract_id="contract-ratio",
        concept="urn:sds:reg:test:emissions_intensity",
        inputs=[
            CalculationContractInput(
                "scope1_total",
                "urn:sds:reg:test:scope1_total_emissions",
                unit="tCO2e",
            ),
            CalculationContractInput("revenue", "fixture_revenue", unit="EURm"),
        ],
        contract_hash="sha256:ratio",
        result_unit="tCO2e/EURm",
    )
    provider = StaticObservationProvider(
        [
            _obs("s1", concept="fixture_stationary", value=1.5, unit="tCO2e"),
            _obs("m1", concept="fixture_mobile", value=500, unit="kgCO2e"),
            _obs("r1", concept="fixture_revenue", value=25, unit="EURm"),
        ]
    )
    resolver = FakeContractResolver(
        {"urn:sds:reg:test:scope1_total_emissions": total_contract}
    )

    result = CalculationEngine(precision=4).calculate_contract(
        ratio_contract,
        _context(),
        provider,
        unit_normalizer=FakeUnitNormalizer({("kgCO2e", "tCO2e"): Decimal("0.001")}),
        contract_resolver=resolver,
    )

    assert result.value == Decimal("0.0800")
    assert result.input_values == {
        "scope1_total": Decimal("2.00"),
        "revenue": Decimal("25"),
    }
    assert result.trace.source_value_ids == ["s1", "m1", "r1"]
    assert (
        result.trace.aggregation_policy["scope1_total"]
        == "nested_contract=contract-total;temporal=sum;perimeter=sum"
    )


def test_contract_cycle_and_nested_conversion_trace_branches(monkeypatch):
    engine = CalculationEngine(precision=2)
    contract = _contract(
        formula="child_value",
        contract_id="parent",
        result_unit="kg",
        inputs=[
            CalculationContractInput(
                "child_value",
                "child:concept",
                unit="kg",
                expected_currency="EUR",
                fx_policy_id="ecb-test",
            )
        ],
    )
    child = _contract(
        formula="source",
        contract_id="child",
        concept="child:concept",
        result_unit="kg",
        result_currency="GBP",
        inputs=[CalculationContractInput("source", "source:concept", unit="kg")],
        contract_hash="sha256:child",
    )
    provider = StaticObservationProvider(
        [_obs("source-1", concept="source:concept", value=7, unit="kg")]
    )
    resolver = FakeContractResolver({"child:concept": child})
    conversion_engine = SimpleNamespace(
        normalize=lambda request: SimpleNamespace(
            value=request.value,
            currency="EUR",
            trace=[{"step_type": "fx", "from_currency": "GBP", "to_currency": "EUR"}],
        )
    )

    original_execute_formula = engine.execute_formula

    def _child_result_with_currency(formula, variables, context, **kwargs):
        result = original_execute_formula(formula, variables, context, **kwargs)
        result.currency = "GBP"
        return result

    monkeypatch.setattr(engine, "execute_formula", _child_result_with_currency)

    result = engine.calculate_contract(
        contract,
        _context(),
        provider,
        conversion_engine=conversion_engine,
        contract_resolver=resolver,
    )

    assert result.value == Decimal("7.00")
    assert result.trace.conversions_applied[-1]["variable"] == "child_value"
    assert result.trace.conversions_applied[-1]["trace"][0]["step_type"] == "fx"

    with pytest.raises(ContractExecutionError, match="Cycle detected"):
        engine.calculate_contract(
            contract, _context(), provider, _calculation_stack={"parent"}
        )


def test_contract_observation_conversion_missing_currency_and_engine_errors():
    engine = CalculationEngine(precision=2)
    contract_input = CalculationContractInput(
        "amount",
        "finance:amount",
        unit="kg",
        expected_currency="EUR",
        fx_policy_id="ecb-test",
    )
    observation = _obs(
        "amount-1",
        concept="finance:amount",
        value=1,
        unit="kg",
        period=date(2024, 1, 1),
    )
    failing_engine = SimpleNamespace(
        normalize=lambda _request: (_ for _ in ()).throw(ValueError("bad conversion"))
    )

    with pytest.raises(ContractExecutionError, match="source currency"):
        engine._normalize_contract_observation_with_engine(
            contract_input,
            observation,
            conversion_engine=failing_engine,
            target_currency="EUR",
        )

    observation = _obs(
        "amount-1",
        concept="finance:amount",
        value=1,
        unit="kg",
        currency="GBP",
        period=date(2024, 1, 1),
    )
    with pytest.raises(ContractExecutionError, match="bad conversion"):
        engine._normalize_contract_observation_with_engine(
            contract_input,
            observation,
            conversion_engine=failing_engine,
            target_currency="EUR",
        )


def test_contract_calculation_wraps_unexpected_formula_errors(monkeypatch):
    engine = CalculationEngine(precision=2)
    contract = _contract(
        formula="source",
        inputs=[CalculationContractInput("source", "source:concept", unit="kg")],
    )
    provider = StaticObservationProvider(
        [_obs("source-1", concept="source:concept", value=7, unit="kg")]
    )
    monkeypatch.setattr(
        engine,
        "execute_formula",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(
        ContractExecutionError, match="Contract calculation failed: boom"
    ):
        engine.calculate_contract(contract, _context(), provider)


def test_subtraction_contract_calculates_net_value_after_aggregation():
    contract = _contract(
        formula="withdrawals - discharges",
        inputs=[
            CalculationContractInput("withdrawals", "water:withdrawals", unit="m3"),
            CalculationContractInput("discharges", "water:discharges", unit="m3"),
        ],
        result_unit="m3",
    )
    provider = StaticObservationProvider(
        [
            _obs("w1", concept="water:withdrawals", value=100, unit="m3"),
            _obs("w2", concept="water:withdrawals", value=25, unit="m3"),
            _obs("d1", concept="water:discharges", value=40, unit="m3"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("85.00")


def test_percentage_contract_calculates_percent_from_aggregated_denominator():
    contract = _contract(
        formula="renewable_energy / total_energy * 100",
        inputs=[
            CalculationContractInput(
                "renewable_energy", "energy:renewable", unit="MWh"
            ),
            CalculationContractInput("total_energy", "energy:total", unit="MWh"),
        ],
        result_unit="%",
    )
    provider = StaticObservationProvider(
        [
            _obs("r1", concept="energy:renewable", value=20, unit="MWh"),
            _obs("r2", concept="energy:renewable", value=30, unit="MWh"),
            _obs("t1", concept="energy:total", value=200, unit="MWh"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("25.00")
    assert result.unit == "%"


def test_weighted_average_contract_pairs_values_and_weights_before_aggregation():
    contract = _contract(
        formula="weighted_average(score, employee_count)",
        hierarchy={"group": ["plant-a", "plant-b"]},
        inputs=[
            CalculationContractInput(
                "score",
                "training:score",
                unit="points",
                entity_scope="children",
                temporal_aggregation="average",
                perimeter_aggregation="average",
            ),
            CalculationContractInput(
                "employee_count",
                "workforce:fte",
                unit="FTE",
                entity_scope="children",
                temporal_aggregation="sum",
                perimeter_aggregation="sum",
            ),
        ],
        result_unit="points",
    )
    provider = StaticObservationProvider(
        [
            _obs(
                "score-a-jan",
                concept="training:score",
                entity="plant-a",
                value=80,
                unit="points",
                period=date(2024, 1, 31),
            ),
            _obs(
                "score-a-feb",
                concept="training:score",
                entity="plant-a",
                value=100,
                unit="points",
                period=date(2024, 2, 29),
            ),
            _obs(
                "score-b-jan",
                concept="training:score",
                entity="plant-b",
                value=50,
                unit="points",
                period=date(2024, 1, 31),
            ),
            _obs(
                "fte-a-jan",
                concept="workforce:fte",
                entity="plant-a",
                value=10,
                unit="FTE",
                period=date(2024, 1, 31),
            ),
            _obs(
                "fte-a-feb",
                concept="workforce:fte",
                entity="plant-a",
                value=30,
                unit="FTE",
                period=date(2024, 2, 29),
            ),
            _obs(
                "fte-b-jan",
                concept="workforce:fte",
                entity="plant-b",
                value=60,
                unit="FTE",
                period=date(2024, 1, 31),
            ),
        ]
    )

    result = CalculationEngine(precision=4).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("68.0000")
    assert result.input_values == {
        "score": Decimal("70"),
        "employee_count": Decimal("100"),
    }
    assert result.trace.formula_steps[:2] == [
        "Contract formula: weighted_average(score, employee_count)",
        "Weighted average: score weighted by employee_count",
    ]


def test_division_by_zero_in_ratio_contract_fails_closed():
    contract = _contract(
        formula="incidents / hours",
        inputs=[
            CalculationContractInput("incidents", "safety:incidents", unit="count"),
            CalculationContractInput("hours", "safety:hours", unit="hour"),
        ],
        result_unit="incidents/hour",
    )
    provider = StaticObservationProvider(
        [
            _obs("i1", concept="safety:incidents", value=1, unit="count"),
            _obs("h1", concept="safety:hours", value=0, unit="hour"),
        ]
    )

    with pytest.raises(ContractExecutionError, match="division"):
        CalculationEngine(precision=4).calculate_contract(
            contract, _context(), provider
        )


def test_lowercase_sum_formula_wrapper_accepts_scalar_expression():
    contract = _contract(
        formula="sum(emissions * equity_share)",
        inputs=[
            CalculationContractInput("emissions", "ghg:investee_scope12", unit="tCO2e"),
            CalculationContractInput("equity_share", "ghg:equity_share", unit="ratio"),
        ],
        result_unit="tCO2e",
    )
    provider = StaticObservationProvider(
        [
            _obs("e1", concept="ghg:investee_scope12", value=100, unit="tCO2e"),
            _obs("s1", concept="ghg:equity_share", value="0.25", unit="ratio"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("25.00")


def test_complex_equation_contract_supports_derived_nodes_and_power():
    contract = _contract(
        formula="sqrt(weighted_score)",
        inputs=[
            CalculationContractInput("base", "score:base", unit="points"),
            CalculationContractInput("adjustment", "score:adjustment", unit="points"),
            CalculationContractInput("penalty", "score:penalty", unit="points"),
        ],
        nodes=[
            DerivedCalculationNode("net_score", "(base + adjustment - penalty) / 2"),
            DerivedCalculationNode("weighted_score", "pow(net_score, 2)"),
        ],
        result_unit="points",
    )
    provider = StaticObservationProvider(
        [
            _obs("b1", concept="score:base", value=80, unit="points"),
            _obs("a1", concept="score:adjustment", value=30, unit="points"),
            _obs("p1", concept="score:penalty", value=10, unit="points"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.value == Decimal("50.00")
    assert result.trace.formula_steps[:3] == [
        "Contract formula: sqrt(weighted_score)",
        "Derived net_score: (base + adjustment - penalty) / 2",
        "Derived weighted_score: pow(net_score, 2)",
    ]


def test_temporal_average_and_last_rules_are_applied_before_formula():
    contract = _contract(
        formula="average_energy + latest_meter",
        inputs=[
            CalculationContractInput(
                "average_energy",
                "energy:kwh",
                unit="kWh",
                temporal_aggregation="average",
            ),
            CalculationContractInput(
                "latest_meter",
                "meter:reading",
                unit="kWh",
                temporal_aggregation="last",
            ),
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("a1", concept="energy:kwh", value=10, unit="kWh"),
            _obs("a2", concept="energy:kwh", value=20, unit="kWh"),
            _obs(
                "m1",
                concept="meter:reading",
                value=5,
                unit="kWh",
                period=date(2024, 1, 31),
            ),
            _obs(
                "m2",
                concept="meter:reading",
                value=7,
                unit="kWh",
                period=date(2024, 12, 31),
            ),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.input_values == {
        "average_energy": Decimal("15"),
        "latest_meter": Decimal("7"),
    }
    assert result.value == Decimal("22.00")


def test_temporal_and_perimeter_aggregation_are_separate_axes():
    contract = _contract(
        formula="headcount",
        hierarchy={"group": ["plant-a", "plant-b"]},
        inputs=[
            CalculationContractInput(
                "headcount",
                "workforce:fte",
                unit="FTE",
                entity_scope="children",
                temporal_aggregation="average",
                perimeter_aggregation="sum",
            )
        ],
        result_unit="FTE",
    )
    provider = StaticObservationProvider(
        [
            _obs("a1", concept="workforce:fte", entity="plant-a", value=10, unit="FTE"),
            _obs("a2", concept="workforce:fte", entity="plant-a", value=14, unit="FTE"),
            _obs("b1", concept="workforce:fte", entity="plant-b", value=20, unit="FTE"),
            _obs("b2", concept="workforce:fte", entity="plant-b", value=30, unit="FTE"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.input_values == {"headcount": Decimal("37")}
    assert result.value == Decimal("37.00")
    assert result.trace.aggregation_policy == {
        "headcount": "temporal=average;perimeter=sum;entities=children"
    }


def test_all_temporal_aggregation_rules_are_applied_before_perimeter():
    contract = _contract(
        formula="first_value + minimum + maximum + observation_count",
        inputs=[
            CalculationContractInput(
                "first_value",
                "metric:first",
                unit="u",
                temporal_aggregation="first",
            ),
            CalculationContractInput(
                "minimum",
                "metric:min",
                unit="u",
                temporal_aggregation="min",
            ),
            CalculationContractInput(
                "maximum",
                "metric:max",
                unit="u",
                temporal_aggregation="max",
            ),
            CalculationContractInput(
                "observation_count",
                "metric:count",
                unit="u",
                temporal_aggregation="count",
            ),
        ],
        result_unit="u",
    )
    provider = StaticObservationProvider(
        [
            _obs(
                "f2",
                concept="metric:first",
                value=99,
                unit="u",
                period=date(2024, 2, 1),
            ),
            _obs(
                "f1",
                concept="metric:first",
                value=10,
                unit="u",
                period=date(2024, 1, 1),
            ),
            _obs("mn1", concept="metric:min", value=8, unit="u"),
            _obs("mn2", concept="metric:min", value=3, unit="u"),
            _obs("mx1", concept="metric:max", value=6, unit="u"),
            _obs("mx2", concept="metric:max", value=14, unit="u"),
            _obs("c1", concept="metric:count", value=123, unit="u"),
            _obs("c2", concept="metric:count", value=456, unit="u"),
            _obs("c3", concept="metric:count", value=789, unit="u"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.input_values == {
        "first_value": Decimal("10"),
        "minimum": Decimal("3"),
        "maximum": Decimal("14"),
        "observation_count": Decimal("3"),
    }
    assert result.value == Decimal("30.00")


def test_all_perimeter_aggregation_rules_are_applied_after_temporal_axis():
    hierarchy = {"group": ["plant-a", "plant-b", "plant-c"]}
    contract = _contract(
        formula="average_value + min_value + max_value + entity_count + first_entity + last_entity + no_perimeter",
        hierarchy=hierarchy,
        inputs=[
            CalculationContractInput(
                "average_value",
                "metric:average",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="average",
            ),
            CalculationContractInput(
                "min_value",
                "metric:min",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="min",
            ),
            CalculationContractInput(
                "max_value",
                "metric:max",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="max",
            ),
            CalculationContractInput(
                "entity_count",
                "metric:count",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="count",
            ),
            CalculationContractInput(
                "first_entity",
                "metric:first",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="first",
            ),
            CalculationContractInput(
                "last_entity",
                "metric:last",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="last",
            ),
            CalculationContractInput(
                "no_perimeter",
                "metric:none",
                unit="u",
                entity_scope="self",
                perimeter_aggregation="none",
            ),
        ],
        result_unit="u",
    )
    provider = StaticObservationProvider(
        [
            _obs(
                "avg-a", concept="metric:average", entity="plant-a", value=10, unit="u"
            ),
            _obs(
                "avg-b", concept="metric:average", entity="plant-b", value=20, unit="u"
            ),
            _obs(
                "avg-c", concept="metric:average", entity="plant-c", value=30, unit="u"
            ),
            _obs("min-a", concept="metric:min", entity="plant-a", value=7, unit="u"),
            _obs("min-b", concept="metric:min", entity="plant-b", value=4, unit="u"),
            _obs("min-c", concept="metric:min", entity="plant-c", value=9, unit="u"),
            _obs("max-a", concept="metric:max", entity="plant-a", value=7, unit="u"),
            _obs("max-b", concept="metric:max", entity="plant-b", value=14, unit="u"),
            _obs("max-c", concept="metric:max", entity="plant-c", value=9, unit="u"),
            _obs("cnt-a", concept="metric:count", entity="plant-a", value=1, unit="u"),
            _obs("cnt-b", concept="metric:count", entity="plant-b", value=1, unit="u"),
            _obs("cnt-c", concept="metric:count", entity="plant-c", value=1, unit="u"),
            _obs(
                "fst-a", concept="metric:first", entity="plant-a", value=100, unit="u"
            ),
            _obs(
                "fst-b", concept="metric:first", entity="plant-b", value=200, unit="u"
            ),
            _obs(
                "fst-c", concept="metric:first", entity="plant-c", value=300, unit="u"
            ),
            _obs("lst-a", concept="metric:last", entity="plant-a", value=100, unit="u"),
            _obs("lst-b", concept="metric:last", entity="plant-b", value=200, unit="u"),
            _obs("lst-c", concept="metric:last", entity="plant-c", value=300, unit="u"),
            _obs("none", concept="metric:none", entity="group", value=5, unit="u"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract, _context(), provider
    )

    assert result.input_values == {
        "average_value": Decimal("20"),
        "min_value": Decimal("4"),
        "max_value": Decimal("14"),
        "entity_count": Decimal("3"),
        "first_entity": Decimal("100"),
        "last_entity": Decimal("300"),
        "no_perimeter": Decimal("5"),
    }
    assert result.value == Decimal("446.00")


def test_perimeter_none_fails_closed_for_multiple_entities():
    contract = _contract(
        formula="value",
        hierarchy={"group": ["plant-a", "plant-b"]},
        inputs=[
            CalculationContractInput(
                "value",
                "metric:none",
                unit="u",
                entity_scope="children",
                perimeter_aggregation="none",
            )
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("a", concept="metric:none", entity="plant-a", value=1, unit="u"),
            _obs("b", concept="metric:none", entity="plant-b", value=2, unit="u"),
        ]
    )

    with pytest.raises(ContractExecutionError, match="exactly one entity"):
        CalculationEngine(precision=2).calculate_contract(
            contract, _context(), provider
        )


def test_zero_observation_is_not_treated_as_missing_but_missing_required_input_fails():
    zero_contract = _contract(
        formula="water",
        inputs=[CalculationContractInput("water", "syg:water", unit="m3")],
    )
    zero_provider = StaticObservationProvider(
        [_obs("z1", concept="syg:water", value=0, unit="m3")]
    )

    zero_result = CalculationEngine(precision=2).calculate_contract(
        zero_contract, _context(), zero_provider
    )

    assert zero_result.value == Decimal("0.00")
    assert zero_result.trace.completeness["missing_required"] == []

    missing_provider = StaticObservationProvider([])
    with pytest.raises(ContractExecutionError, match="Missing required observations"):
        CalculationEngine(precision=2).calculate_contract(
            zero_contract, _context(), missing_provider
        )


def test_mixed_units_are_normalized_before_aggregation():
    contract = _contract(
        formula="emissions",
        inputs=[
            CalculationContractInput(
                "emissions",
                "ghg:scope1",
                unit="kg",
                temporal_aggregation="sum",
            )
        ],
    )
    provider = StaticObservationProvider(
        [
            _obs("kg1", concept="ghg:scope1", value=500, unit="kg"),
            _obs("t1", concept="ghg:scope1", value=1, unit="t"),
        ]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
        unit_normalizer=FakeUnitNormalizer({("t", "kg"): Decimal("1000")}),
    )

    assert result.value == Decimal("1500.00")
    assert result.trace.conversions_applied == [
        {
            "variable": "emissions",
            "value_id": "t1",
            "from_unit": "t",
            "to_unit": "kg",
            "factor": "1000",
        }
    ]


def test_cycle_in_derived_contract_dag_fails_closed():
    contract = _contract(
        formula="total",
        inputs=[CalculationContractInput("raw", "source:raw", unit="kg")],
        nodes=[
            DerivedCalculationNode("a", "b + raw"),
            DerivedCalculationNode("b", "a + raw"),
            DerivedCalculationNode("total", "a + b"),
        ],
    )
    provider = StaticObservationProvider([_obs("raw1", concept="source:raw")])

    with pytest.raises(ContractExecutionError, match="Cycle detected"):
        CalculationEngine().calculate_contract(contract, _context(), provider)


def test_non_executable_contract_resolves_but_direct_execution_fails_closed():
    contract = _contract(
        formula="water",
        inputs=[CalculationContractInput("water", "syg:water", unit="m3")],
        runtime_status="draft",
    )
    provider = StaticObservationProvider(
        [_obs("w1", concept="syg:water", value=1, unit="m3")]
    )

    with pytest.raises(ContractExecutionError, match="not executable"):
        CalculationEngine().calculate_contract(contract, _context(), provider)


def test_contract_calculation_does_not_return_stale_values_after_value_or_contract_change():
    contract_a = _contract(
        formula="water",
        inputs=[CalculationContractInput("water", "syg:water", unit="m3")],
        contract_hash="sha256:a",
    )
    provider = StaticObservationProvider(
        [_obs("w1", concept="syg:water", value=1, unit="m3")]
    )
    engine = CalculationEngine(precision=2)

    first = engine.calculate_contract(contract_a, _context(), provider)
    provider.replace([_obs("w2", concept="syg:water", value=2, unit="m3")])
    second = engine.calculate_contract(contract_a, _context(), provider)
    contract_b = _contract(
        formula="water * 10",
        inputs=[CalculationContractInput("water", "syg:water", unit="m3")],
        contract_hash="sha256:b",
    )
    third = engine.calculate_contract(contract_b, _context(), provider)

    assert first.value == Decimal("1.00")
    assert second.value == Decimal("2.00")
    assert second.trace.source_value_ids == ["w2"]
    assert third.value == Decimal("20.00")
    assert third.trace.contract_hash == "sha256:b"


def test_optional_missing_inputs_use_default_or_zero_with_warnings():
    contract = _contract(
        formula="required + optional_default + optional_zero",
        inputs=[
            CalculationContractInput("required", "metric:required", unit="kg"),
            CalculationContractInput(
                "optional_default",
                "metric:default",
                unit="kg",
                required=False,
                default_value=Decimal("4"),
            ),
            CalculationContractInput(
                "optional_zero",
                "metric:zero",
                unit="kg",
                required=False,
            ),
        ],
    )
    provider = StaticObservationProvider(
        [_obs("r1", concept="metric:required", value=6, unit="kg")]
    )

    result = CalculationEngine(precision=2).calculate_contract(
        contract,
        _context(),
        provider,
    )

    assert result.value == Decimal("10.00")
    assert result.input_values["optional_default"] == Decimal("4")
    assert result.input_values["optional_zero"] == Decimal("0")
    assert "optional_default: default value used" in result.trace.warnings
    assert "optional_zero: optional input missing, zero used" in result.trace.warnings


def test_nested_contract_resolution_error_self_cycle_and_external_cycle():
    engine = CalculationEngine()
    contract_input = CalculationContractInput("child", "metric:child", unit="kg")

    class FailingResolver:
        def resolve(self, concept):
            raise CalculationContractNotFoundError(concept)

    assert (
        engine._try_calculate_nested_contract(
            contract_input,
            _context(),
            StaticObservationProvider([]),
            unit_normalizer=None,
            conversion_engine=None,
            contract_resolver=FailingResolver(),
            calculation_stack={"parent"},
            current_contract_id="parent",
        )
        is None
    )

    class MalformedResolver:
        def resolve(self, concept):
            raise ContractResolutionError("present nested contract is malformed")

    with pytest.raises(
        ContractResolutionError, match="present nested contract is malformed"
    ):
        engine._try_calculate_nested_contract(
            contract_input,
            _context(),
            StaticObservationProvider([]),
            unit_normalizer=None,
            conversion_engine=None,
            contract_resolver=MalformedResolver(),
            calculation_stack={"parent"},
            current_contract_id="parent",
        )

    child_contract = _contract(
        formula="x",
        inputs=[CalculationContractInput("x", "metric:x", unit="kg")],
        contract_id="child-contract",
        concept="metric:child",
    )
    resolver = FakeContractResolver({"metric:child": child_contract})

    assert (
        engine._try_calculate_nested_contract(
            contract_input,
            _context(),
            StaticObservationProvider([]),
            unit_normalizer=None,
            conversion_engine=None,
            contract_resolver=resolver,
            calculation_stack={"child-contract"},
            current_contract_id="child-contract",
        )
        is None
    )

    with pytest.raises(ContractExecutionError, match="Cycle detected"):
        engine._try_calculate_nested_contract(
            contract_input,
            _context(),
            StaticObservationProvider([]),
            unit_normalizer=None,
            conversion_engine=None,
            contract_resolver=resolver,
            calculation_stack={"parent", "child-contract"},
            current_contract_id="parent",
        )


def test_weighted_average_rejects_duplicate_entity_period_pairs():
    engine = CalculationEngine()
    contract_input = CalculationContractInput("score", "score", unit="points")
    observations = [
        _obs("s1", concept="score", value=1, unit="points"),
        _obs("s2", concept="score", value=2, unit="points"),
    ]

    with pytest.raises(ContractExecutionError, match="duplicate group/2024-01-01"):
        engine._normalized_observations_by_entity_period(
            "score",
            contract_input,
            observations,
            unit_normalizer=None,
        )


def test_missing_data_block_fails_closed_on_missing_optional_input():
    """codex F07 M1: aggregation.missing_data=BLOCK must block ANY missing input,
    even one marked required=false, instead of defaulting to zero + 'complete'."""
    contract = _contract(
        formula="a + b",
        inputs=[
            CalculationContractInput("a", "concept_a", unit="kg"),
            CalculationContractInput("b", "concept_b", unit="kg", required=False),
        ],
        missing_data="BLOCK",
    )
    # only 'a' is observed; optional 'b' is missing
    provider = StaticObservationProvider([_obs("v1", concept="concept_a", value=5)])

    with pytest.raises(ContractExecutionError, match="Missing required observations"):
        CalculationEngine().calculate_contract(contract, _context(), provider)


def test_missing_data_default_policy_zero_fills_optional_input():
    """Without a BLOCK policy, an optional missing input keeps the prior behavior:
    it is zero-filled and the calculation completes."""
    contract = _contract(
        formula="a + b",
        inputs=[
            CalculationContractInput("a", "concept_a", unit="kg"),
            CalculationContractInput("b", "concept_b", unit="kg", required=False),
        ],
        missing_data=None,
    )
    provider = StaticObservationProvider([_obs("v1", concept="concept_a", value=5)])

    result = CalculationEngine().calculate_contract(contract, _context(), provider)
    assert result.value == Decimal("5")
    assert result.trace.completeness["status"] == "complete"
