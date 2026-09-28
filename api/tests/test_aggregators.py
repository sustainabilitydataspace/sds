"""Tests for aggregation utilities."""

from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.calculation.aggregators import (
    CrossHierarchyAggregator,
    HierarchyLevel,
    OrganizationalAggregator,
    TemporalAggregator,
)
from src.calculation.engine import (
    AggregationMethod,
    CalculationContext,
    CalculationEngine,
    CalculationError,
    CalculationResult,
    TemporalGranularity,
)


class TestTemporalAggregator:
    """Test cases for TemporalAggregator."""

    def setup_method(self):
        """Set up test fixtures."""
        self.engine = CalculationEngine(precision=2)
        self.aggregator = TemporalAggregator(self.engine)
        self.context = CalculationContext(
            entity_id="test_entity",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=1,
        )

    def test_aggregate_temporal_values_monthly_to_quarterly(self):
        """Test temporal aggregation from monthly to quarterly."""
        values = {
            date(2024, 1, 15): 100,
            date(2024, 2, 15): 150,
            date(2024, 3, 15): 200,
            date(2024, 4, 15): 120,
            date(2024, 5, 15): 180,
            date(2024, 6, 15): 160,
        }

        results = self.aggregator.aggregate_temporal_values(
            values,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            AggregationMethod.SUM,
            self.context,
        )

        assert len(results) == 2  # Q1 and Q2
        assert "2024-Q1" in results
        assert "2024-Q2" in results

        # Q1: Jan + Feb + Mar = 100 + 150 + 200 = 450
        assert results["2024-Q1"].value == Decimal("450.00")

        # Q2: Apr + May + Jun = 120 + 180 + 160 = 460
        assert results["2024-Q2"].value == Decimal("460.00")

        # Check trace information
        for result in results.values():
            assert any(
                "temporal_monthly_to_quarterly" in agg
                for agg in result.trace.aggregations_applied
            )

    def test_aggregate_temporal_values_monthly_to_annual(self):
        """Test temporal aggregation from monthly to annual."""
        values = {
            date(2024, 1, 15): 100,
            date(2024, 6, 15): 200,
            date(2024, 12, 15): 300,
        }

        results = self.aggregator.aggregate_temporal_values(
            values,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.ANNUAL,
            AggregationMethod.SUM,
            self.context,
        )

        assert len(results) == 1
        assert "2024" in results
        assert results["2024"].value == Decimal("600.00")

    def test_aggregate_temporal_values_average_method(self):
        """Test temporal aggregation with AVERAGE method."""
        values = {
            date(2024, 1, 15): 100,
            date(2024, 2, 15): 200,
            date(2024, 3, 15): 300,
        }

        results = self.aggregator.aggregate_temporal_values(
            values,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            AggregationMethod.AVERAGE,
            self.context,
        )

        assert len(results) == 1
        assert "2024-Q1" in results
        # Average: (100 + 200 + 300) / 3 = 200
        assert results["2024-Q1"].value == Decimal("200.00")

    def test_aggregate_temporal_values_empty_input(self):
        """Test temporal aggregation with empty input."""
        values = {}

        results = self.aggregator.aggregate_temporal_values(
            values,
            TemporalGranularity.MONTHLY,
            TemporalGranularity.QUARTERLY,
            AggregationMethod.SUM,
            self.context,
        )

        assert len(results) == 0

    def test_validate_temporal_hierarchy_valid(self):
        """Test valid temporal hierarchy validation."""
        # Should not raise exception
        self.aggregator._validate_temporal_hierarchy(
            TemporalGranularity.MONTHLY, TemporalGranularity.QUARTERLY
        )

        self.aggregator._validate_temporal_hierarchy(
            TemporalGranularity.DAILY, TemporalGranularity.ANNUAL
        )

    def test_validate_temporal_hierarchy_invalid(self):
        """Test invalid temporal hierarchy validation."""
        with pytest.raises(CalculationError, match="Invalid temporal aggregation"):
            self.aggregator._validate_temporal_hierarchy(
                TemporalGranularity.ANNUAL, TemporalGranularity.MONTHLY
            )

        with pytest.raises(CalculationError, match="Invalid temporal aggregation"):
            self.aggregator._validate_temporal_hierarchy(
                TemporalGranularity.QUARTERLY, TemporalGranularity.MONTHLY
            )

    def test_get_period_identifier(self):
        """Test period identifier generation."""
        test_date = date(2024, 7, 15)

        daily = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.DAILY
        )
        weekly = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.WEEKLY
        )
        monthly = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.MONTHLY
        )
        quarterly = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.QUARTERLY
        )
        semestral = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.SEMESTRAL
        )
        annual = self.aggregator._get_period_identifier(
            test_date, TemporalGranularity.ANNUAL
        )

        assert daily == "2024-07-15"
        assert weekly.startswith("2024-W")
        assert monthly == "2024-07"
        assert quarterly == "2024-Q3"
        assert semestral == "2024-S2"
        assert annual == "2024"

    def test_get_temporal_periods_in_range(self):
        """Test getting temporal periods in date range."""
        start_date = date(2024, 1, 1)
        end_date = date(2024, 3, 31)

        monthly_periods = self.aggregator.get_temporal_periods_in_range(
            start_date, end_date, TemporalGranularity.MONTHLY
        )

        assert len(monthly_periods) == 3
        assert "2024-01" in monthly_periods
        assert "2024-02" in monthly_periods
        assert "2024-03" in monthly_periods

        quarterly_periods = self.aggregator.get_temporal_periods_in_range(
            start_date, end_date, TemporalGranularity.QUARTERLY
        )

        assert len(quarterly_periods) == 1
        assert "2024-Q1" in quarterly_periods


class TestOrganizationalAggregator:
    """Test cases for OrganizationalAggregator."""

    def setup_method(self):
        """Set up test fixtures."""
        self.engine = CalculationEngine(precision=2)
        self.aggregator = OrganizationalAggregator(self.engine)
        self.context = CalculationContext(
            entity_id="test_entity",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=1,
        )

        # Sample hierarchy configuration
        self.hierarchy_config = {
            "levels": [
                {"id": "global", "name": "Global", "parent": None},
                {"id": "europe", "name": "Europe", "parent": "global"},
                {"id": "spain", "name": "Spain", "parent": "europe"},
                {"id": "madrid_plant", "name": "Madrid Plant", "parent": "spain"},
                {"id": "barcelona_plant", "name": "Barcelona Plant", "parent": "spain"},
            ]
        }

    def test_build_hierarchy_tree(self):
        """Test hierarchy tree building."""
        tree = self.aggregator._build_hierarchy_tree(self.hierarchy_config)

        assert len(tree) == 5
        assert "global" in tree
        assert "madrid_plant" in tree

        # Check parent-child relationships
        assert tree["global"].parent_id is None
        assert tree["europe"].parent_id == "global"
        assert tree["spain"].parent_id == "europe"
        assert tree["madrid_plant"].parent_id == "spain"

        # Check children
        assert "europe" in tree["global"].children
        assert "spain" in tree["europe"].children
        assert "madrid_plant" in tree["spain"].children
        assert "barcelona_plant" in tree["spain"].children

        # Check levels
        assert tree["global"].level == 0
        assert tree["europe"].level == 1
        assert tree["spain"].level == 2
        assert tree["madrid_plant"].level == 3

    def test_aggregate_organizational_values(self):
        """Test organizational aggregation."""
        entity_values = {"madrid_plant": 100, "barcelona_plant": 200}

        results = self.aggregator.aggregate_organizational_values(
            entity_values, self.hierarchy_config, AggregationMethod.SUM, self.context
        )

        # Should have results for all levels
        assert "madrid_plant" in results
        assert "barcelona_plant" in results
        assert "spain" in results
        assert "europe" in results
        assert "global" in results

        # Check aggregated values
        assert results["madrid_plant"].value == Decimal("100.00")
        assert results["barcelona_plant"].value == Decimal("200.00")
        assert results["spain"].value == Decimal("300.00")  # 100 + 200
        assert results["europe"].value == Decimal("300.00")  # Same as Spain
        assert results["global"].value == Decimal("300.00")  # Same as Europe

        # Check organizational levels in context
        assert results["madrid_plant"].context.organizational_level == 3
        assert results["spain"].context.organizational_level == 2
        assert results["global"].context.organizational_level == 0

    def test_aggregate_organizational_values_average(self):
        """Test organizational aggregation with AVERAGE method."""
        entity_values = {"madrid_plant": 100, "barcelona_plant": 200}

        results = self.aggregator.aggregate_organizational_values(
            entity_values,
            self.hierarchy_config,
            AggregationMethod.AVERAGE,
            self.context,
        )

        # Spain should be average of its plants: (100 + 200) / 2 = 150
        assert results["spain"].value == Decimal("150.00")

    def test_aggregate_organizational_values_empty_input(self):
        """Test organizational aggregation with empty input."""
        entity_values = {}

        results = self.aggregator.aggregate_organizational_values(
            entity_values, self.hierarchy_config, AggregationMethod.SUM, self.context
        )

        assert len(results) == 0

    def test_get_entity_path(self):
        """Test getting entity path to root."""
        tree = self.aggregator._build_hierarchy_tree(self.hierarchy_config)

        path = self.aggregator.get_entity_path("madrid_plant", tree)

        assert path == ["madrid_plant", "spain", "europe", "global"]

    def test_get_entity_descendants(self):
        """Test getting entity descendants."""
        tree = self.aggregator._build_hierarchy_tree(self.hierarchy_config)

        descendants = self.aggregator.get_entity_descendants("spain", tree)

        assert len(descendants) == 2
        assert "madrid_plant" in descendants
        assert "barcelona_plant" in descendants

        global_descendants = self.aggregator.get_entity_descendants("global", tree)

        assert len(global_descendants) == 4  # All except global itself
        assert "europe" in global_descendants
        assert "spain" in global_descendants
        assert "madrid_plant" in global_descendants
        assert "barcelona_plant" in global_descendants


class TestCrossHierarchyAggregator:
    """Test cases for CrossHierarchyAggregator."""

    def setup_method(self):
        """Set up test fixtures."""
        self.engine = CalculationEngine(precision=2)
        self.aggregator = CrossHierarchyAggregator(self.engine)
        self.context = CalculationContext(
            entity_id="spain",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.QUARTERLY,
            organizational_level=2,
        )

        self.hierarchy_config = {
            "levels": [
                {"id": "spain", "name": "Spain", "parent": None},
                {"id": "madrid_plant", "name": "Madrid Plant", "parent": "spain"},
                {"id": "barcelona_plant", "name": "Barcelona Plant", "parent": "spain"},
            ]
        }

    def test_aggregate_cross_hierarchy(self):
        """Test cross-hierarchy aggregation (temporal + organizational)."""
        # Values for different entities and dates
        values = {
            ("madrid_plant", date(2024, 1, 15)): 100,
            ("madrid_plant", date(2024, 2, 15)): 150,
            ("madrid_plant", date(2024, 3, 15)): 200,
            ("barcelona_plant", date(2024, 1, 15)): 80,
            ("barcelona_plant", date(2024, 2, 15)): 120,
            ("barcelona_plant", date(2024, 3, 15)): 160,
        }

        results = self.aggregator.aggregate_cross_hierarchy(
            values,
            self.hierarchy_config,
            target_entity="spain",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            method=AggregationMethod.SUM,
            context=self.context,
        )

        assert len(results) == 1  # One quarter (Q1)
        assert "2024-Q1" in results

        # Q1 total: Madrid (100+150+200) + Barcelona (80+120+160) = 450 + 360 = 810
        assert results["2024-Q1"].value == Decimal("810.00")

        # Check that both aggregation types are recorded in trace
        trace = results["2024-Q1"].trace
        assert any("cross_hierarchy" in agg for agg in trace.aggregations_applied)

    def test_aggregate_cross_hierarchy_multiple_quarters(self):
        """Test cross-hierarchy aggregation across multiple quarters."""
        values = {
            # Q1 data
            ("madrid_plant", date(2024, 1, 15)): 100,
            ("madrid_plant", date(2024, 2, 15)): 150,
            ("madrid_plant", date(2024, 3, 15)): 200,
            # Q2 data
            ("madrid_plant", date(2024, 4, 15)): 120,
            ("madrid_plant", date(2024, 5, 15)): 180,
            ("madrid_plant", date(2024, 6, 15)): 160,
            # Barcelona Q1
            ("barcelona_plant", date(2024, 1, 15)): 80,
            ("barcelona_plant", date(2024, 2, 15)): 120,
            ("barcelona_plant", date(2024, 3, 15)): 160,
            # Barcelona Q2
            ("barcelona_plant", date(2024, 4, 15)): 90,
            ("barcelona_plant", date(2024, 5, 15)): 130,
            ("barcelona_plant", date(2024, 6, 15)): 140,
        }

        results = self.aggregator.aggregate_cross_hierarchy(
            values,
            self.hierarchy_config,
            target_entity="spain",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            method=AggregationMethod.SUM,
            context=self.context,
        )

        assert len(results) == 2  # Q1 and Q2
        assert "2024-Q1" in results
        assert "2024-Q2" in results

        # Q1: Madrid (450) + Barcelona (360) = 810
        assert results["2024-Q1"].value == Decimal("810.00")

        # Q2: Madrid (460) + Barcelona (360) = 820
        assert results["2024-Q2"].value == Decimal("820.00")

    def test_aggregate_cross_hierarchy_empty_input(self):
        """Test cross-hierarchy aggregation with empty input."""
        values = {}

        results = self.aggregator.aggregate_cross_hierarchy(
            values,
            self.hierarchy_config,
            target_entity="spain",
            target_granularity=TemporalGranularity.QUARTERLY,
            source_granularity=TemporalGranularity.MONTHLY,
            method=AggregationMethod.SUM,
            context=self.context,
        )

        assert len(results) == 0


class TestHierarchyLevel:
    """Test cases for HierarchyLevel."""

    def test_hierarchy_level_creation(self):
        """Test HierarchyLevel creation."""
        level = HierarchyLevel(
            id="madrid_plant",
            name="Madrid Plant",
            parent_id="spain",
            level=3,
            children=["dept1", "dept2"],
        )

        assert level.id == "madrid_plant"
        assert level.name == "Madrid Plant"
        assert level.parent_id == "spain"
        assert level.level == 3
        assert level.children == ["dept1", "dept2"]

    def test_hierarchy_level_no_parent(self):
        """Test HierarchyLevel with no parent (root level)."""
        level = HierarchyLevel(
            id="global",
            name="Global",
            parent_id=None,
            level=0,
            children=["europe", "americas"],
        )

        assert level.parent_id is None
        assert level.level == 0
        assert len(level.children) == 2
