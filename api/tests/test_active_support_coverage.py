from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from rdflib import Graph, Literal, URIRef

from src.calculation import db_semantic_snapshot as semantic_snapshot
from src.calculation.contracts import ContractExecutionError, DerivedCalculationNode
from src.calculation.engine import (
    SDS,
    AggregationMethod,
    CalculationContext,
    CalculationEngine,
    CalculationError,
    CalculationResult,
    CalculationTrace,
    TemporalGranularity,
    VariableDependency,
    _policy_label,
)
from src.calculation.json_strategy import JSONStrategy


def test_json_strategy_loads_units_categories_rules_and_propagates_backend_errors():
    strategy = JSONStrategy()
    fake_category = SimpleNamespace(value="mass")
    strategy.unit_database = SimpleNamespace(
        database_path="units.json",
        database_data={
            "categories": {
                "mass": {
                    "base_unit": "kg",
                    "description": "Mass",
                    "special_conversions": True,
                }
            }
        },
        get_unit_definitions=lambda: [
            SimpleNamespace(
                symbol="kg",
                name="kilogram",
                category=fake_category,
                base_unit="kg",
                conversion_factor=Decimal("1"),
                conversion_offset=Decimal("0"),
                aliases=["kilogram"],
                metadata={"source": "test"},
            )
        ],
        get_conversion_rules=lambda: [
            SimpleNamespace(
                from_unit="kg",
                to_unit="g",
                formula="value * 1000",
                reverse_formula="value / 1000",
                conditions={"kind": "direct"},
                metadata={"source": "test"},
            )
        ],
    )

    assert strategy.load_units()[0]["symbol"] == "kg"
    assert strategy.load_categories()[0]["special_conversions"] is True
    assert strategy.load_conversion_rules()[0]["to_unit"] == "g"
    strategy._available = True
    assert strategy.get_storage_info().backend == "json"

    failing = JSONStrategy()
    failing.unit_database = SimpleNamespace(
        get_unit_definitions=lambda: (_ for _ in ()).throw(RuntimeError("units")),
        database_data=SimpleNamespace(
            get=lambda *_args: (_ for _ in ()).throw(RuntimeError("categories"))
        ),
        get_conversion_rules=lambda: (_ for _ in ()).throw(RuntimeError("rules")),
    )
    with pytest.raises(RuntimeError, match="units"):
        failing.load_units()
    with pytest.raises(RuntimeError, match="categories"):
        failing.load_categories()
    with pytest.raises(RuntimeError, match="rules"):
        failing.load_conversion_rules()


def test_db_semantic_snapshot_active_formula_selection_and_evaluation():
    active_formula = SimpleNamespace(
        id=2,
        version=2,
        is_active=True,
        expression="a + b",
        expression_language="python",
    )
    old_formula = SimpleNamespace(
        id=1, version=1, is_active=True, expression="a", expression_language="python"
    )
    inactive_formula = SimpleNamespace(id=3, version=3, is_active=False, expression="b")
    concept = SimpleNamespace(
        uri="sds:Concept",
        label="Concept",
        description="Description",
        taxonomy="SDS",
        concept_type="metric",
        unit="https://sustainabilitydataspace.com/ontology#Tonne",
        temporal_granularity="annual",
        concept_state="calculable",
        formulas=[inactive_formula, old_formula, active_formula],
        variables=[
            SimpleNamespace(
                id=2,
                variable_uri="sds:b",
                variable_label="B",
                ordering=2,
                aggregation_method="SUM",
                temporal_granularity="annual",
            ),
            SimpleNamespace(
                id=1,
                variable_uri="sds:a",
                variable_label="A",
                ordering=1,
                aggregation_method="SUM",
                temporal_granularity="annual",
            ),
        ],
        equivalences=[
            SimpleNamespace(
                id=1,
                target_uri="gri:305_1",
                target_taxonomy="GRI",
                relationship_type="equivalent",
            )
        ],
    )

    snapshot = semantic_snapshot._snapshot_from_concept(concept)
    assert snapshot.comparable_formula == "a + b"
    assert snapshot.display_unit == "t"
    assert [variable.uri for variable in snapshot.variables] == ["sds:a", "sds:b"]
    assert semantic_snapshot.build_seeded_variable_values(["sds:a", "plain"]) == {
        "a": Decimal("1"),
        "plain": Decimal("2"),
    }
    result, unit, formula = semantic_snapshot.evaluate_db_semantic_formula(
        snapshot, {"a": Decimal("2"), "b": Decimal("3")}
    )
    assert result == Decimal("5")
    assert unit == "t"
    assert formula == "a + b"

    empty_snapshot = semantic_snapshot.DBSemanticConcept(
        uri="sds:NoFormula",
        label="No formula",
        description=None,
        taxonomy="SDS",
        concept_type="metric",
        unit=None,
        temporal_granularity=None,
        concept_state="draft",
        formula_expression=None,
        formula_language=None,
    )
    with pytest.raises(ValueError, match="No evaluable formula"):
        semantic_snapshot.evaluate_db_semantic_formula(empty_snapshot, {})


def test_calculation_engine_active_helper_edges(monkeypatch):
    engine = CalculationEngine(precision=2)
    context = CalculationContext(
        entity_id="global",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        temporal_granularity=TemporalGranularity.ANNUAL,
        organizational_level=0,
    )

    assert engine._safe_sum([1, 2, 3]) == 6
    assert engine._safe_sum(Decimal("4")) == Decimal("4")
    assert engine._safe_sum(1, 2, 3) == 6
    assert (
        _policy_label(
            SimpleNamespace(
                temporal_aggregation="sum",
                perimeter_aggregation="average",
                entity_scope="children",
            )
        )
        == "temporal=sum;perimeter=average;entities=children"
    )
    with pytest.raises(CalculationError, match="value_provider is required"):
        engine._get_base_values(
            [
                VariableDependency(
                    variable_uri="sds:a",
                    entity_id="global",
                    period="2026",
                    temporal_granularity=TemporalGranularity.ANNUAL,
                )
            ]
        )

    graph = Graph()
    concept = URIRef("https://sustainabilitydataspace.com/ontology#Indicator")
    variable = URIRef("https://sustainabilitydataspace.com/ontology#Variable")
    graph.add(
        (
            variable,
            SDS.hasTemporalGranularity,
            URIRef("https://sustainabilitydataspace.com/ontology#Monthly"),
        )
    )
    graph.add(
        (
            concept,
            SDS.hasUnit,
            URIRef("https://sustainabilitydataspace.com/ontology#Kilogram"),
        )
    )
    assert (
        engine._temporal_granularity_from_ontology(graph, variable)
        == TemporalGranularity.MONTHLY
    )
    assert engine._get_indicator_unit("sds:Indicator", graph) == "kg"
    assert (
        engine._temporal_granularity_from_ontology(
            graph, URIRef("https://example.com/no-tg")
        )
        is None
    )
    assert engine._get_indicator_unit("sds:Missing", graph) is None

    graph.add((concept, SDS.hasVariable, variable))
    engine._dependency_cache = {f"old-{idx}": [] for idx in range(1000)}
    deps = engine.resolve_dependencies("sds:Indicator", context, graph)
    assert deps[0].variable_uri == "sds:Variable"
    assert "old-0" not in engine._dependency_cache

    result = CalculationResult(
        value=Decimal("3"),
        unit="kg",
        context=context,
        formula_used="a + b",
        input_values={"a": Decimal("1"), "b": Decimal("2")},
        calculation_timestamp=engine.execute_formula(
            "1+1", {}, context
        ).calculation_timestamp,
        trace=CalculationTrace(
            formula_steps=["a + b"],
            conversions_applied=[{"from_unit": "g", "to_unit": "kg"}],
            execution_time_ms=1.5,
        ),
    )
    trace = engine.generate_calculation_trace(
        "sds:Indicator",
        [
            VariableDependency(
                variable_uri="sds:a",
                entity_id="plant",
                period="2026-01",
                temporal_granularity=TemporalGranularity.MONTHLY,
                aggregation_method=AggregationMethod.SUM,
            )
        ],
        result,
    )
    assert set(trace.aggregations_applied) == {"monthly -> annual", "plant -> global"}
    assert trace.formula_steps == ["a + b"]
    assert trace.conversions_applied == [{"from_unit": "g", "to_unit": "kg"}]

    empty_trace = engine.generate_calculation_trace(
        "sds:Indicator",
        [],
        SimpleNamespace(input_values=None, context=context),
    )
    assert empty_trace == CalculationTrace()

    with pytest.raises(CalculationError, match="Total weight cannot be zero"):
        engine._aggregate_weighted_average([Decimal("1"), Decimal("2")], weights=[0, 0])
    with pytest.raises(ContractExecutionError, match="Unsupported conversion policy"):
        engine._validate_conversion_policy(
            SimpleNamespace(local_variable="x", conversion_policy="best_effort")
        )
    with pytest.raises(ContractExecutionError, match="Invalid formula syntax"):
        engine._expression_variable_names("a +")
    with pytest.raises(CalculationError, match="No formula found"):
        engine._get_indicator_formula("sds:Missing", Graph(), dependencies=[])
    with pytest.raises(CalculationError, match="Unsafe function"):
        engine._validate_formula("__import__('os')")
    with pytest.raises(CalculationError, match="Unsafe unary operator"):
        engine._check_ast_safety(__import__("ast").parse("~a", mode="eval").body)


def test_calculation_engine_derived_node_error_paths():
    engine = CalculationEngine()
    contract = SimpleNamespace(
        derived_nodes=(
            DerivedCalculationNode(local_variable="derived", expression="missing + 1"),
        ),
    )
    with pytest.raises(ContractExecutionError, match="Unknown variable"):
        engine._evaluate_derived_nodes(contract, {})

    cyclic = SimpleNamespace(
        derived_nodes=(
            DerivedCalculationNode(
                local_variable="a", expression="b + 1", dependencies=("b",)
            ),
            DerivedCalculationNode(
                local_variable="b", expression="a + 1", dependencies=("a",)
            ),
        ),
    )
    with pytest.raises(ContractExecutionError, match="Cycle detected"):
        engine._evaluate_derived_nodes(cyclic, {})

    invalid = SimpleNamespace(
        derived_nodes=(
            DerivedCalculationNode(
                local_variable="a", expression="base / 0", dependencies=("base",)
            ),
        ),
    )
    with pytest.raises(ContractExecutionError, match="Failed to evaluate derived node"):
        engine._evaluate_derived_nodes(invalid, {"base": Decimal("1")})
