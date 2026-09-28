"""Tests for CalculationEngine."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from rdflib import Graph, Namespace, URIRef

from src.calculation.engine import (
    AggregationMethod,
    CalculationContext,
    CalculationEngine,
    CalculationError,
    CalculationResult,
    CalculationTrace,
    OntologyCalculationNotFoundError,
    TemporalGranularity,
    VariableDependency,
)


class TestCalculationEngine:
    """Test cases for CalculationEngine."""

    def setup_method(self):
        """Set up test fixtures."""
        self.engine = CalculationEngine(precision=2)
        self.context = CalculationContext(
            entity_id="test_entity",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=1,
        )

    def test_missing_ontology_route_is_distinct_from_present_formula_failure(self):
        concept = "urn:sds:test:missing_formula_contract"
        graph = Graph()
        with pytest.raises(OntologyCalculationNotFoundError):
            self.engine.calculate_indicator(concept, self.context, graph, Mock())

        sds = Namespace("https://sustainabilitydataspace.com/ontology#")
        graph.add((URIRef(concept), sds.hasFormula, URIRef("urn:sds:test:formula")))
        with pytest.raises(CalculationError) as failure:
            self.engine.calculate_indicator(concept, self.context, graph, Mock())
        assert not isinstance(failure.value, OntologyCalculationNotFoundError)

    def test_init(self):
        """Test CalculationEngine initialization."""
        engine = CalculationEngine(precision=4, timeout=60)
        assert engine.precision == 4
        assert engine.timeout == 60
        assert len(engine.safe_operators) > 0
        assert len(engine.safe_functions) > 0
        assert len(engine.aggregation_methods) == len(AggregationMethod)

    def test_execute_formula_simple(self):
        """Test simple formula execution."""
        formula = "a + b"
        variables = {"a": 10, "b": 20}

        result = self.engine.execute_formula(formula, variables, self.context)

        assert result.value == Decimal("30.00")
        assert result.formula_used == formula
        assert result.input_values == variables
        assert result.context == self.context
        assert len(result.trace.formula_steps) > 0

    def test_execute_formula_complex(self):
        """Test complex formula execution."""
        formula = "sqrt(a**2 + b**2)"
        variables = {"a": 3, "b": 4}

        result = self.engine.execute_formula(formula, variables, self.context)

        assert result.value == Decimal("5.00")
        assert result.formula_used == formula

    def test_execute_formula_with_functions(self):
        """Test formula with mathematical functions."""
        formula = "max(a, b) + min(a, b)"
        variables = {"a": 15, "b": 25}

        result = self.engine.execute_formula(formula, variables, self.context)

        assert result.value == Decimal("40.00")

    def test_execute_formula_accepts_local_name_for_curie_variable_key(self):
        """Current semantic formulas use local names while stores keep URN/CURIE keys."""
        result = self.engine.execute_formula(
            "e3_5_01 + plain",
            {
                "sds:reg:esrs:e3_5_01": Decimal("3"),
                "plain": Decimal("2"),
            },
            self.context,
        )

        assert result.value == Decimal("5")

    def test_execute_formula_rejects_colliding_local_names_from_distinct_namespaces(
        self,
    ):
        with pytest.raises(CalculationError, match="Ambiguous formula variable alias"):
            self.engine.execute_formula(
                "value",
                {
                    "alpha:value": Decimal("1"),
                    "beta:value": Decimal("2"),
                },
                self.context,
            )

    def test_execute_formula_invalid_syntax(self):
        """Test formula with invalid syntax."""
        formula = "a +"  # Invalid syntax
        variables = {"a": 10}

        with pytest.raises(CalculationError, match="Invalid formula syntax"):
            self.engine.execute_formula(formula, variables, self.context)

    def test_execute_formula_unsafe_function(self):
        """Test formula with unsafe function."""
        formula = "eval('1+1')"  # Unsafe function
        variables = {}

        with pytest.raises(CalculationError, match="Unsafe function"):
            self.engine.execute_formula(formula, variables, self.context)

    def test_execute_formula_undefined_variable(self):
        """Test formula with undefined variable."""
        formula = "a + undefined_var"
        variables = {"a": 10}

        with pytest.raises(CalculationError, match="Undefined variable"):
            self.engine.execute_formula(formula, variables, self.context)

    def test_execute_formula_none_value(self):
        """Test formula with None variable value."""
        formula = "a + b"
        variables = {"a": 10, "b": None}

        with pytest.raises(CalculationError, match="has None value"):
            self.engine.execute_formula(formula, variables, self.context)

    def test_resolve_dependencies_csrd_indicator(self):
        """Test dependency resolution for CSRD indicator."""
        indicator_uri = "csrd:E3_5"

        dependencies = self.engine.resolve_dependencies(indicator_uri, self.context)

        assert len(dependencies) == 1
        assert dependencies[0].variable_uri == "urn:sds:reg:esrs:e3_5_01"
        assert dependencies[0].temporal_granularity == TemporalGranularity.ANNUAL

        for dep in dependencies:
            assert dep.entity_id == self.context.entity_id
            assert dep.aggregation_method == AggregationMethod.SUM

    def test_resolve_dependencies_gri_indicator(self):
        """Test dependency resolution for GRI indicator."""
        indicator_uri = "gri:303_3"

        dependencies = self.engine.resolve_dependencies(indicator_uri, self.context)

        assert len(dependencies) == 1
        assert (
            dependencies[0].variable_uri
            == "urn:sds:reg:gri:gri_303_3_a_total_all_areas"
        )

    def test_resolve_dependencies_unknown_indicator(self):
        """Test dependency resolution for unknown indicator."""
        indicator_uri = "unknown:indicator"

        dependencies = self.engine.resolve_dependencies(indicator_uri, self.context)

        assert len(dependencies) == 0

    def test_resolve_dependencies_caching(self):
        """Test that dependency resolution uses caching."""
        indicator_uri = "csrd:E3_5"

        # First call
        dependencies1 = self.engine.resolve_dependencies(indicator_uri, self.context)

        # Second call should use cache
        dependencies2 = self.engine.resolve_dependencies(indicator_uri, self.context)

        assert dependencies1 == dependencies2
        assert len(self.engine._dependency_cache) > 0

    def test_dependency_cache_eviction_and_resolution_error(self):
        """Dependency cache stays bounded and resolver errors are wrapped."""
        for index in range(1000):
            self.engine._dependency_cache[f"old-{index}"] = []

        self.engine.resolve_dependencies("unknown:indicator", self.context)

        assert "old-0" not in self.engine._dependency_cache
        assert len(self.engine._dependency_cache) == 1000

        assert self.engine._resolve_generic_dependencies("anything", self.context) == []

        with patch.object(
            self.engine,
            "_get_ontology_graph",
            side_effect=RuntimeError("ontology unavailable"),
        ):
            with pytest.raises(CalculationError, match="Dependency resolution failed"):
                self.engine.resolve_dependencies("unknown:failing", self.context)

    def test_calculate_indicator_full_flow(self):
        """Test full indicator calculation flow."""
        indicator_uri = "csrd:E3_5"

        # Mock value provider
        def mock_value_provider(variable_uri, entity_id, period):
            if "e3_5_01" in variable_uri:
                return 300.0
            return 0.0

        result = self.engine.calculate_indicator(
            indicator_uri, self.context, value_provider=mock_value_provider
        )

        assert result.value == Decimal("300.00")
        assert result.context == self.context
        assert len(result.trace.source_variables) == 1
        assert len(result.trace.formula_steps) > 0

    @pytest.mark.parametrize(
        "dependency",
        [
            VariableDependency(
                variable_uri="urn:sds:monthly",
                entity_id="test_entity",
                period="2024-01",
                temporal_granularity=TemporalGranularity.MONTHLY,
            ),
            VariableDependency(
                variable_uri="urn:sds:child",
                entity_id="child_entity",
                period="2024",
                temporal_granularity=TemporalGranularity.ANNUAL,
            ),
        ],
    )
    def test_ontology_fallback_refuses_unimplemented_aggregation(self, dependency):
        with pytest.raises(CalculationError, match="does not implement"):
            self.engine._apply_aggregations(
                {"value": Decimal("1")}, [dependency], self.context
            )

    def test_calculate_indicator_gri_full_flow(self):
        """Test full indicator calculation flow for a GRI indicator."""
        indicator_uri = "gri:303_3"

        def mock_value_provider(variable_uri, entity_id, period):
            if "gri_303_3_a_total_all_areas" in variable_uri:
                return 123.0
            return 0.0

        result = self.engine.calculate_indicator(
            indicator_uri,
            self.context,
            value_provider=mock_value_provider,
        )

        assert result.value == Decimal("123.00")
        assert result.context == self.context
        assert result.trace is not None

    def test_calculate_indicator_returns_currency_unit(self):
        """Current ESRS E3-5 runtime disclosure is a financial-effects measure."""
        result = self.engine.calculate_indicator(
            "csrd:E3_5",
            self.context,
            value_provider=lambda *_args: 1.0,
        )

        assert result.unit == "Currency"

    def test_calculate_indicator_caching(self):
        """Test that indicator calculation uses caching."""
        indicator_uri = "csrd:E3_5"

        def mock_value_provider(variable_uri, entity_id, period):
            if "e3_5_01" in variable_uri:
                return 1.0
            return 0.0

        # First calculation
        result1 = self.engine.calculate_indicator(
            indicator_uri, self.context, value_provider=mock_value_provider
        )

        # Second calculation should use cache
        result2 = self.engine.calculate_indicator(
            indicator_uri, self.context, value_provider=mock_value_provider
        )

        assert result1.value == result2.value
        assert len(self.engine._calculation_cache) > 0

    def test_calculation_cache_eviction(self):
        """Calculation cache evicts oldest entries after reaching its cap."""
        for index in range(1000):
            self.engine._calculation_cache[f"old-{index}"] = "cached"

        result = self.engine.calculate_indicator(
            "csrd:E3_5",
            self.context,
            value_provider=lambda *_args: 1.0,
        )

        assert result.value == Decimal("1.00")
        assert "old-0" not in self.engine._calculation_cache
        assert len(self.engine._calculation_cache) == 1000

    def test_generate_calculation_trace(self):
        """Test calculation trace generation."""
        indicator_uri = "csrd:E3_5"
        dependencies = [
            VariableDependency(
                variable_uri="syg:Water_Cooling",
                entity_id="test_entity",
                period="2024-01",
                temporal_granularity=TemporalGranularity.MONTHLY,
            )
        ]

        result = CalculationResult(
            value=Decimal("100.00"),
            unit="m3",
            context=self.context,
            formula_used="Water_Cooling",
            input_values={"Water_Cooling": 100},
            calculation_timestamp=datetime.now(),
        )

        trace = self.engine.generate_calculation_trace(
            indicator_uri, dependencies, result
        )

        assert len(trace.source_variables) == 1
        assert trace.source_variables[0] == dependencies[0]

    def test_aggregate_values_sum(self):
        """Test value aggregation with SUM method."""
        values = [10, 20, 30, 40]

        result = self.engine.aggregate_values(
            values, AggregationMethod.SUM, context=self.context
        )

        assert result.value == Decimal("100.00")
        assert result.formula_used == "SUM(4 values)"

    def test_aggregate_values_average(self):
        """Test value aggregation with AVERAGE method."""
        values = [10, 20, 30, 40]

        result = self.engine.aggregate_values(
            values, AggregationMethod.AVERAGE, context=self.context
        )

        assert result.value == Decimal("25.00")

    def test_aggregate_values_weighted_average(self):
        """Test value aggregation with WEIGHTED_AVERAGE method."""
        values = [10, 20, 30]
        weights = [1, 2, 3]

        result = self.engine.aggregate_values(
            values,
            AggregationMethod.WEIGHTED_AVERAGE,
            weights=weights,
            context=self.context,
        )

        # (10*1 + 20*2 + 30*3) / (1+2+3) = 140/6 = 23.33
        assert result.value == Decimal("23.33")

    def test_aggregate_values_weighted_average_no_weights(self):
        """Test weighted average without weights raises error."""
        values = [10, 20, 30]

        with pytest.raises(CalculationError, match="Weights required"):
            self.engine.aggregate_values(
                values, AggregationMethod.WEIGHTED_AVERAGE, context=self.context
            )

    def test_aggregate_values_weighted_average_mismatched_lengths(self):
        """Test weighted average with mismatched lengths raises error."""
        values = [10, 20, 30]
        weights = [1, 2]  # Different length

        with pytest.raises(CalculationError, match="same length"):
            self.engine.aggregate_values(
                values,
                AggregationMethod.WEIGHTED_AVERAGE,
                weights=weights,
                context=self.context,
            )

    def test_aggregate_values_min_max(self):
        """Test MIN and MAX aggregation methods."""
        values = [10, 5, 30, 15]

        min_result = self.engine.aggregate_values(
            values, AggregationMethod.MIN, context=self.context
        )
        max_result = self.engine.aggregate_values(
            values, AggregationMethod.MAX, context=self.context
        )

        assert min_result.value == Decimal("5.00")
        assert max_result.value == Decimal("30.00")

    def test_aggregate_values_count(self):
        """Test COUNT aggregation method."""
        values = [10, 20, 30, 40, 50]

        result = self.engine.aggregate_values(
            values, AggregationMethod.COUNT, context=self.context
        )

        assert result.value == Decimal("5.00")

    def test_aggregate_values_first_last(self):
        """Test FIRST and LAST aggregation methods."""
        values = [10, 20, 30, 40]

        first_result = self.engine.aggregate_values(
            values, AggregationMethod.FIRST, context=self.context
        )
        last_result = self.engine.aggregate_values(
            values, AggregationMethod.LAST, context=self.context
        )

        assert first_result.value == Decimal("10.00")
        assert last_result.value == Decimal("40.00")

    def test_aggregate_values_empty_list(self):
        """Test aggregation with empty list raises error."""
        values = []

        with pytest.raises(CalculationError, match="empty list"):
            self.engine.aggregate_values(
                values, AggregationMethod.SUM, context=self.context
            )

    def test_clear_cache(self):
        """Test cache clearing."""
        # Add some items to cache
        self.engine._dependency_cache["test"] = []
        self.engine._calculation_cache["test"] = Mock()

        assert len(self.engine._dependency_cache) > 0
        assert len(self.engine._calculation_cache) > 0

        self.engine.clear_cache()

        assert len(self.engine._dependency_cache) == 0
        assert len(self.engine._calculation_cache) == 0

    def test_get_calculation_stats(self):
        """Test calculation statistics."""
        stats = self.engine.get_calculation_stats()

        assert "precision" in stats
        assert "timeout" in stats
        assert "supported_operators" in stats
        assert "supported_functions" in stats
        assert "aggregation_methods" in stats
        assert "temporal_granularities" in stats
        assert "cached_dependencies" in stats
        assert "cached_calculations" in stats

        assert stats["precision"] == self.engine.precision
        assert stats["timeout"] == self.engine.timeout
        assert len(stats["aggregation_methods"]) == len(AggregationMethod)
        assert len(stats["temporal_granularities"]) == len(TemporalGranularity)

    def test_precision_handling(self):
        """Test decimal precision handling."""
        engine = CalculationEngine(precision=3)
        formula = "a / b"
        variables = {"a": 10, "b": 3}

        result = engine.execute_formula(formula, variables, self.context)

        # 10/3 = 3.333... should be rounded to 3 decimal places
        assert result.value == Decimal("3.333")

    def test_format_period_different_granularities(self):
        """Test period formatting for different granularities."""
        test_date = date(2024, 7, 15)

        daily = self.engine._format_period(test_date, TemporalGranularity.DAILY)
        monthly = self.engine._format_period(test_date, TemporalGranularity.MONTHLY)
        quarterly = self.engine._format_period(test_date, TemporalGranularity.QUARTERLY)
        semestral = self.engine._format_period(test_date, TemporalGranularity.SEMESTRAL)
        annual = self.engine._format_period(test_date, TemporalGranularity.ANNUAL)

        assert daily == "2024-07-15"
        assert monthly == "2024-07"
        assert quarterly == "2024-Q3"
        assert semestral == "2024-S2"
        assert annual == "2024"
        assert self.engine._format_period(test_date, object()) == "2024-07-15"


class TestCalculationContext:
    """Test cases for CalculationContext."""

    def test_calculation_context_creation(self):
        """Test CalculationContext creation."""
        context = CalculationContext(
            entity_id="test_entity",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=2,
            metadata={"test": "value"},
        )

        assert context.entity_id == "test_entity"
        assert context.period_start == date(2024, 1, 1)
        assert context.period_end == date(2024, 12, 31)
        assert context.temporal_granularity == TemporalGranularity.ANNUAL
        assert context.organizational_level == 2
        assert context.metadata == {"test": "value"}


class TestVariableDependency:
    """Test cases for VariableDependency."""

    def test_variable_dependency_creation(self):
        """Test VariableDependency creation."""
        dependency = VariableDependency(
            variable_uri="syg:Water_Cooling",
            entity_id="madrid_plant",
            period="2024-01",
            temporal_granularity=TemporalGranularity.MONTHLY,
            aggregation_method=AggregationMethod.SUM,
            weight=0.5,
        )

        assert dependency.variable_uri == "syg:Water_Cooling"
        assert dependency.entity_id == "madrid_plant"
        assert dependency.period == "2024-01"
        assert dependency.temporal_granularity == TemporalGranularity.MONTHLY
        assert dependency.aggregation_method == AggregationMethod.SUM
        assert dependency.weight == 0.5

    def test_variable_dependency_defaults(self):
        """Test VariableDependency with default values."""
        dependency = VariableDependency(
            variable_uri="syg:Water_Cooling",
            entity_id="madrid_plant",
            period="2024-01",
            temporal_granularity=TemporalGranularity.MONTHLY,
        )

        assert dependency.aggregation_method == AggregationMethod.SUM
        assert dependency.weight is None


class TestCalculationTrace:
    """Test cases for CalculationTrace."""

    def test_calculation_trace_creation(self):
        """Test CalculationTrace creation."""
        trace = CalculationTrace()

        assert trace.source_variables == []
        assert trace.aggregations_applied == []
        assert trace.conversions_applied == []
        assert trace.formula_steps == []
        assert trace.execution_time_ms is None

    def test_calculation_trace_with_data(self):
        """Test CalculationTrace with data."""
        dependency = VariableDependency(
            variable_uri="syg:Water_Cooling",
            entity_id="test_entity",
            period="2024-01",
            temporal_granularity=TemporalGranularity.MONTHLY,
        )

        trace = CalculationTrace(
            source_variables=[dependency],
            aggregations_applied=["temporal_monthly_to_annual"],
            conversions_applied=[{"from": "m3", "to": "liters"}],
            formula_steps=["Step 1: Load variables"],
            execution_time_ms=150.5,
        )

        assert len(trace.source_variables) == 1
        assert trace.source_variables[0] == dependency
        assert trace.aggregations_applied == ["temporal_monthly_to_annual"]
        assert trace.conversions_applied == [{"from": "m3", "to": "liters"}]
        assert trace.formula_steps == ["Step 1: Load variables"]
        assert trace.execution_time_ms == 150.5
