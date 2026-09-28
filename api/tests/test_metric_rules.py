"""Tests for metric-specific aggregation rules."""

from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.calculation.engine import (
    AggregationMethod,
    CalculationContext,
    CalculationEngine,
    CalculationError,
    TemporalGranularity,
)
from src.calculation.metric_rules import (
    MetricAggregationRule,
    MetricAggregationRules,
    MetricType,
)


class TestMetricAggregationRules:
    """Test cases for MetricAggregationRules."""

    def setup_method(self):
        """Set up test fixtures."""
        self.engine = CalculationEngine(precision=2)
        self.rules = MetricAggregationRules(self.engine)
        self.context = CalculationContext(
            entity_id="test_entity",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=1,
        )

    def test_init(self):
        """Test MetricAggregationRules initialization."""
        assert len(self.rules.rules) == len(MetricType)
        assert self.rules.engine == self.engine
        assert self.rules.temporal_aggregator is not None
        assert self.rules.organizational_aggregator is not None
        assert self.rules.cross_aggregator is not None

    def test_get_rule_consumption(self):
        """Test getting rule for consumption metrics."""
        rule = self.rules.get_rule(MetricType.CONSUMPTION)

        assert rule.metric_type == MetricType.CONSUMPTION
        assert rule.temporal_method == AggregationMethod.SUM
        assert rule.organizational_method == AggregationMethod.SUM
        assert not rule.requires_weights
        assert "consumption" in rule.description.lower()

    def test_get_rule_intensity(self):
        """Test getting rule for intensity metrics."""
        rule = self.rules.get_rule(MetricType.INTENSITY)

        assert rule.metric_type == MetricType.INTENSITY
        assert rule.temporal_method == AggregationMethod.WEIGHTED_AVERAGE
        assert rule.organizational_method == AggregationMethod.WEIGHTED_AVERAGE
        assert rule.requires_weights
        assert rule.weight_variable == "production_volume"

    def test_get_rule_maximum(self):
        """Test getting rule for maximum metrics."""
        rule = self.rules.get_rule(MetricType.MAXIMUM)

        assert rule.metric_type == MetricType.MAXIMUM
        assert rule.temporal_method == AggregationMethod.MAX
        assert rule.organizational_method == AggregationMethod.MAX
        assert not rule.requires_weights

    def test_get_rule_binary(self):
        """Test getting rule for binary metrics."""
        rule = self.rules.get_rule(MetricType.BINARY)

        assert rule.metric_type == MetricType.BINARY
        assert rule.temporal_method == AggregationMethod.FIRST
        assert rule.organizational_method == AggregationMethod.SUM
        assert not rule.requires_weights

    def test_get_rule_unsupported(self):
        """Test getting rule for unsupported metric type."""
        # Create a mock metric type that doesn't exist
        with pytest.raises(CalculationError, match="Unsupported metric type"):
            # This will fail because we're not using a real MetricType
            self.rules.get_rule("invalid_type")

    def test_infer_metric_type_consumption(self):
        """Test inferring consumption metric types."""
        test_cases = [
            ("csrd:water_consumption", "m3", MetricType.CONSUMPTION),
            ("gri:energy_use", "kwh", MetricType.CONSUMPTION),
            ("syg:fuel_consumption", "liters", MetricType.CONSUMPTION),
            ("material_usage", None, MetricType.CONSUMPTION),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_emission(self):
        """Test inferring emission metric types."""
        test_cases = [
            ("csrd:co2_emissions", "tonnes", MetricType.EMISSION),
            ("gri:ghg_scope1", "co2e", MetricType.EMISSION),
            ("carbon_footprint", None, MetricType.EMISSION),
            ("pollutant_discharge", "kg", MetricType.EMISSION),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_intensity(self):
        """Test inferring intensity metric types."""
        test_cases = [
            ("water_intensity", "m3/tonne", MetricType.INTENSITY),
            ("energy_per_unit", "kwh/unit", MetricType.INTENSITY),
            ("co2_per_kwh", "kg/kwh", MetricType.INTENSITY),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_percentage(self):
        """Test inferring percentage metric types."""
        test_cases = [
            ("renewable_ratio", "%", MetricType.PERCENTAGE),
            ("efficiency_percent", "percent", MetricType.PERCENTAGE),
            ("waste_ratio", "ratio", MetricType.PERCENTAGE),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_count(self):
        """Test inferring count metric types."""
        test_cases = [
            ("incident_count", "number", MetricType.COUNT),
            ("employee_number", "count", MetricType.COUNT),
            ("accidents", None, MetricType.COUNT),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_binary(self):
        """Test inferring binary metric types."""
        test_cases = [
            ("iso14001_compliance", "flag", MetricType.BINARY),
            ("certified_facility", "binary", MetricType.BINARY),
            ("compliance_status", None, MetricType.BINARY),
        ]

        for uri, unit, expected_type in test_cases:
            inferred_type = self.rules.infer_metric_type(uri, unit)
            assert inferred_type == expected_type

    def test_infer_metric_type_unknown_defaults_to_consumption(self):
        """Test that unknown metric types default to consumption."""
        inferred_type = self.rules.infer_metric_type("unknown_metric", "unknown_unit")
        assert inferred_type == MetricType.CONSUMPTION

    def test_infer_metric_type_additional_active_keywords(self):
        """Cover the non-default inference keywords used by active SDS metrics."""
        cases = [
            ("custom_per_tonne_metric", None, MetricType.INTENSITY),
            ("energy_intensity", "kwh_per_tonne", MetricType.INTENSITY),
            ("near_miss_rate", None, MetricType.RATE),
            ("peak_capacity", None, MetricType.MAXIMUM),
            ("minimum_threshold", None, MetricType.MINIMUM),
            ("process_efficiency", None, MetricType.EFFICIENCY),
            ("equipment_utilization", None, MetricType.EFFICIENCY),
            ("yield_score", None, MetricType.EFFICIENCY),
        ]

        for uri, unit, expected in cases:
            assert self.rules.infer_metric_type(uri, unit) == expected

    def test_aggregate_with_rules_temporal_sum(self):
        """Test aggregation with rules for temporal SUM."""
        values = {
            date(2024, 1, 15): 100,
            date(2024, 2, 15): 200,
            date(2024, 3, 15): 300,
        }

        result = self.rules.aggregate_with_rules(
            values, MetricType.CONSUMPTION, "temporal", context=self.context
        )

        assert result.value == Decimal("600.00")
        assert any(
            "metric_rule_consumption_temporal_SUM" in agg
            for agg in result.trace.aggregations_applied
        )

    def test_aggregate_with_rules_organizational_weighted_average(self):
        """Test aggregation with rules for organizational WEIGHTED_AVERAGE."""
        values = {"plant_a": 10, "plant_b": 20, "plant_c": 30}
        weights = {"plant_a": 100, "plant_b": 200, "plant_c": 300}  # Production volume

        result = self.rules.aggregate_with_rules(
            values,
            MetricType.INTENSITY,
            "organizational",
            weights=weights,
            context=self.context,
        )

        # Weighted average: (10*100 + 20*200 + 30*300) / (100+200+300) = 14000/600 = 23.33
        assert result.value == Decimal("23.33")
        assert any(
            "metric_rule_intensity_organizational_WEIGHTED_AVERAGE" in agg
            for agg in result.trace.aggregations_applied
        )
        assert any(
            "weighted_by_production_volume" in agg
            for agg in result.trace.aggregations_applied
        )

    def test_aggregate_with_rules_weighted_no_weights_provided(self):
        """Test weighted aggregation when no weights are provided."""
        values = {"plant_a": 10, "plant_b": 20, "plant_c": 30}

        result = self.rules.aggregate_with_rules(
            values, MetricType.INTENSITY, "organizational", context=self.context
        )

        # Should use equal weights: (10*1 + 20*1 + 30*1) / (1+1+1) = 60/3 = 20
        assert result.value == Decimal("20.00")
        assert any(
            "metric_rule_intensity_organizational_WEIGHTED_AVERAGE" in agg
            for agg in result.trace.aggregations_applied
        )

    def test_aggregate_with_rules_maximum(self):
        """Test aggregation with rules for MAX method."""
        values = {"plant_a": 100, "plant_b": 250, "plant_c": 150}

        result = self.rules.aggregate_with_rules(
            values, MetricType.MAXIMUM, "organizational", context=self.context
        )

        assert result.value == Decimal("250.00")
        assert any(
            "metric_rule_maximum_organizational_MAX" in agg
            for agg in result.trace.aggregations_applied
        )

    def test_aggregate_with_rules_invalid_aggregation_type(self):
        """Test aggregation with invalid aggregation type."""
        values = {"test": 100}

        with pytest.raises(CalculationError, match="Invalid aggregation type"):
            self.rules.aggregate_with_rules(
                values, MetricType.CONSUMPTION, "invalid_type", context=self.context
            )

    def test_aggregate_temporal_with_rules(self):
        """Test temporal aggregation with metric rules."""
        values = {
            date(2024, 1, 15): 100,
            date(2024, 2, 15): 200,
            date(2024, 3, 15): 300,
            date(2024, 4, 15): 150,
            date(2024, 5, 15): 250,
            date(2024, 6, 15): 200,
        }

        results = self.rules.aggregate_temporal_with_rules(
            values,
            MetricType.CONSUMPTION,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            context=self.context,
        )

        assert len(results) == 2  # Q1 and Q2
        assert "2024-Q1" in results
        assert "2024-Q2" in results

        # Q1: 100 + 200 + 300 = 600
        assert results["2024-Q1"].value == Decimal("600.00")

        # Q2: 150 + 250 + 200 = 600
        assert results["2024-Q2"].value == Decimal("600.00")

        # Check metric rule trace
        for result in results.values():
            assert any(
                "temporal_metric_rule_consumption_SUM" in agg
                for agg in result.trace.aggregations_applied
            )

    def test_aggregate_temporal_weighted_metric_trace_with_and_without_weights(self):
        values = {
            date(2024, 1, 15): 10,
            date(2024, 2, 15): 20,
            date(2024, 3, 15): 30,
        }
        weights = {day: 100 for day in values}

        weighted = self.rules.aggregate_temporal_with_rules(
            values,
            MetricType.INTENSITY,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            weights=weights,
            context=self.context,
        )
        equal = self.rules.aggregate_temporal_with_rules(
            values,
            MetricType.INTENSITY,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            context=self.context,
        )

        assert any(
            "weighted_by_production_volume" in agg
            for agg in weighted["2024-Q1"].trace.aggregations_applied
        )
        assert any(
            "equal_weights_used" in agg
            for agg in equal["2024-Q1"].trace.aggregations_applied
        )

    def test_aggregate_organizational_with_rules(self):
        """Test organizational aggregation with metric rules."""
        entity_values = {"madrid_plant": 100, "barcelona_plant": 200}

        hierarchy_config = {
            "levels": [
                {"id": "spain", "name": "Spain", "parent": None},
                {"id": "madrid_plant", "name": "Madrid Plant", "parent": "spain"},
                {"id": "barcelona_plant", "name": "Barcelona Plant", "parent": "spain"},
            ]
        }

        results = self.rules.aggregate_organizational_with_rules(
            entity_values, MetricType.EMISSION, hierarchy_config, context=self.context
        )

        assert "madrid_plant" in results
        assert "barcelona_plant" in results
        assert "spain" in results

        # Spain should be sum of plants: 100 + 200 = 300
        assert results["spain"].value == Decimal("300.00")

        # Check metric rule trace
        for result in results.values():
            assert any(
                "organizational_metric_rule_emission_SUM" in agg
                for agg in result.trace.aggregations_applied
            )

    def test_aggregate_organizational_weighted_metric_trace_with_and_without_weights(
        self,
    ):
        entity_values = {"madrid_plant": 10, "barcelona_plant": 20}
        hierarchy_config = {
            "levels": [
                {"id": "spain", "name": "Spain", "parent": None},
                {"id": "madrid_plant", "name": "Madrid Plant", "parent": "spain"},
                {"id": "barcelona_plant", "name": "Barcelona Plant", "parent": "spain"},
            ]
        }

        weighted = self.rules.aggregate_organizational_with_rules(
            entity_values,
            MetricType.INTENSITY,
            hierarchy_config,
            weights={"madrid_plant": 1, "barcelona_plant": 2},
            context=self.context,
        )
        equal = self.rules.aggregate_organizational_with_rules(
            entity_values,
            MetricType.INTENSITY,
            hierarchy_config,
            context=self.context,
        )

        assert any(
            "weighted_by_production_volume" in agg
            for agg in weighted["spain"].trace.aggregations_applied
        )
        assert any(
            "equal_weights_used" in agg
            for agg in equal["spain"].trace.aggregations_applied
        )

    def test_aggregate_cross_hierarchy_with_rules(self):
        """Test cross-hierarchy aggregation with metric rules."""
        values = {
            ("madrid_plant", date(2024, 1, 15)): 100,
            ("madrid_plant", date(2024, 2, 15)): 150,
            ("madrid_plant", date(2024, 3, 15)): 200,
            ("barcelona_plant", date(2024, 1, 15)): 80,
            ("barcelona_plant", date(2024, 2, 15)): 120,
            ("barcelona_plant", date(2024, 3, 15)): 160,
        }

        hierarchy_config = {
            "levels": [
                {"id": "spain", "name": "Spain", "parent": None},
                {"id": "madrid_plant", "name": "Madrid Plant", "parent": "spain"},
                {"id": "barcelona_plant", "name": "Barcelona Plant", "parent": "spain"},
            ]
        }

        results = self.rules.aggregate_cross_hierarchy_with_rules(
            values,
            MetricType.CONSUMPTION,
            hierarchy_config,
            target_entity="spain",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            context=self.context,
        )

        assert len(results) == 1  # Q1
        assert "2024-Q1" in results

        # Q1 total: Madrid (450) + Barcelona (360) = 810
        assert results["2024-Q1"].value == Decimal("810.00")

        # Check metric rule trace
        result = results["2024-Q1"]
        assert any(
            "cross_hierarchy_metric_rule_consumption_SUM" in agg
            for agg in result.trace.aggregations_applied
        )

    def test_add_custom_rule(self):
        """Test adding custom aggregation rule."""
        custom_rule = MetricAggregationRule(
            metric_type=MetricType.CONSUMPTION,
            temporal_method=AggregationMethod.AVERAGE,
            organizational_method=AggregationMethod.AVERAGE,
            description="Custom consumption rule with averages",
        )

        # Add custom rule
        self.rules.add_custom_rule(MetricType.CONSUMPTION, custom_rule)

        # Verify it was added
        retrieved_rule = self.rules.get_rule(MetricType.CONSUMPTION)
        assert retrieved_rule.temporal_method == AggregationMethod.AVERAGE
        assert retrieved_rule.organizational_method == AggregationMethod.AVERAGE
        assert retrieved_rule.description == "Custom consumption rule with averages"

    def test_get_supported_metric_types(self):
        """Test getting supported metric types."""
        supported_types = self.rules.get_supported_metric_types()

        assert len(supported_types) == len(MetricType)
        assert MetricType.CONSUMPTION in supported_types
        assert MetricType.EMISSION in supported_types
        assert MetricType.INTENSITY in supported_types
        assert MetricType.BINARY in supported_types

    def test_get_rule_description(self):
        """Test getting rule description."""
        description = self.rules.get_rule_description(MetricType.CONSUMPTION)

        assert isinstance(description, str)
        assert len(description) > 0
        assert "consumption" in description.lower()

    def test_cross_hierarchy_different_methods_warning(self):
        """Test warning when temporal and organizational methods differ in cross-hierarchy."""
        # Binary type has different temporal (FIRST) and organizational (SUM) methods
        values = {
            ("plant_a", date(2024, 1, 15)): 1,
            ("plant_a", date(2024, 2, 15)): 0,
            ("plant_b", date(2024, 1, 15)): 1,
            ("plant_b", date(2024, 2, 15)): 1,
        }

        hierarchy_config = {
            "levels": [
                {"id": "company", "name": "Company", "parent": None},
                {"id": "plant_a", "name": "Plant A", "parent": "company"},
                {"id": "plant_b", "name": "Plant B", "parent": "company"},
            ]
        }

        # Should complete without error but log warning
        results = self.rules.aggregate_cross_hierarchy_with_rules(
            values,
            MetricType.BINARY,
            hierarchy_config,
            target_entity="company",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            context=self.context,
        )

        assert len(results) == 1
        assert "2024-Q1" in results

    def test_cross_hierarchy_weighted_metric_trace_with_and_without_weights(self):
        values = {
            ("plant_a", date(2024, 1, 15)): 10,
            ("plant_b", date(2024, 1, 15)): 20,
        }
        hierarchy_config = {
            "levels": [
                {"id": "company", "name": "Company", "parent": None},
                {"id": "plant_a", "name": "Plant A", "parent": "company"},
                {"id": "plant_b", "name": "Plant B", "parent": "company"},
            ]
        }

        weighted = self.rules.aggregate_cross_hierarchy_with_rules(
            values,
            MetricType.INTENSITY,
            hierarchy_config,
            target_entity="company",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            weights={key: 1 for key in values},
            context=self.context,
        )
        equal = self.rules.aggregate_cross_hierarchy_with_rules(
            values,
            MetricType.INTENSITY,
            hierarchy_config,
            target_entity="company",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            context=self.context,
        )

        assert any(
            "weighted_by_production_volume" in agg
            for agg in weighted["2024-Q1"].trace.aggregations_applied
        )
        assert any(
            "equal_weights_used" in agg
            for agg in equal["2024-Q1"].trace.aggregations_applied
        )

    def test_metric_rule_aggregation_errors_are_wrapped(self, monkeypatch):
        monkeypatch.setattr(
            self.rules.temporal_aggregator,
            "aggregate_temporal_values",
            Mock(side_effect=RuntimeError("temporal failed")),
        )
        with pytest.raises(
            CalculationError, match="Temporal aggregation with rules failed"
        ):
            self.rules.aggregate_temporal_with_rules(
                {date(2024, 1, 1): 1},
                MetricType.CONSUMPTION,
                TemporalGranularity.DAILY,
                TemporalGranularity.MONTHLY,
                context=self.context,
            )

        monkeypatch.setattr(
            self.rules.organizational_aggregator,
            "aggregate_organizational_values",
            Mock(side_effect=RuntimeError("org failed")),
        )
        with pytest.raises(
            CalculationError, match="Organizational aggregation with rules failed"
        ):
            self.rules.aggregate_organizational_with_rules(
                {"plant": 1},
                MetricType.CONSUMPTION,
                {"levels": [{"id": "plant", "name": "Plant", "parent": None}]},
                context=self.context,
            )

        monkeypatch.setattr(
            self.rules.cross_aggregator,
            "aggregate_cross_hierarchy",
            Mock(side_effect=RuntimeError("cross failed")),
        )
        with pytest.raises(
            CalculationError, match="Cross-hierarchy aggregation with rules failed"
        ):
            self.rules.aggregate_cross_hierarchy_with_rules(
                {("plant", date(2024, 1, 1)): 1},
                MetricType.CONSUMPTION,
                {"levels": [{"id": "plant", "name": "Plant", "parent": None}]},
                target_entity="plant",
                target_granularity=TemporalGranularity.MONTHLY,
                source_granularity=TemporalGranularity.DAILY,
                context=self.context,
            )


class TestMetricAggregationRule:
    """Test cases for MetricAggregationRule."""

    def test_metric_aggregation_rule_creation(self):
        """Test MetricAggregationRule creation."""
        rule = MetricAggregationRule(
            metric_type=MetricType.INTENSITY,
            temporal_method=AggregationMethod.WEIGHTED_AVERAGE,
            organizational_method=AggregationMethod.WEIGHTED_AVERAGE,
            requires_weights=True,
            weight_variable="production_volume",
            description="Intensity metrics with production weighting",
        )

        assert rule.metric_type == MetricType.INTENSITY
        assert rule.temporal_method == AggregationMethod.WEIGHTED_AVERAGE
        assert rule.organizational_method == AggregationMethod.WEIGHTED_AVERAGE
        assert rule.requires_weights is True
        assert rule.weight_variable == "production_volume"
        assert rule.description == "Intensity metrics with production weighting"

    def test_metric_aggregation_rule_defaults(self):
        """Test MetricAggregationRule with default values."""
        rule = MetricAggregationRule(
            metric_type=MetricType.CONSUMPTION,
            temporal_method=AggregationMethod.SUM,
            organizational_method=AggregationMethod.SUM,
        )

        assert rule.requires_weights is False
        assert rule.weight_variable is None
        assert rule.description == ""


class TestMetricType:
    """Test cases for MetricType enum."""

    def test_metric_type_values(self):
        """Test MetricType enum values."""
        assert MetricType.CONSUMPTION.value == "consumption"
        assert MetricType.EMISSION.value == "emission"
        assert MetricType.INTENSITY.value == "intensity"
        assert MetricType.EFFICIENCY.value == "efficiency"
        assert MetricType.PERCENTAGE.value == "percentage"
        assert MetricType.COUNT.value == "count"
        assert MetricType.RATE.value == "rate"
        assert MetricType.MAXIMUM.value == "maximum"
        assert MetricType.MINIMUM.value == "minimum"
        assert MetricType.BINARY.value == "binary"

    def test_metric_type_count(self):
        """Test that all expected metric types are defined."""
        expected_count = 10  # Update if new metric types are added
        assert len(MetricType) == expected_count
