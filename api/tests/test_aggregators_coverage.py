"""Tests for aggregators to achieve full coverage.

Targets uncovered lines in src/calculation/aggregators.py:
- 159-164: Exception handling in organizational aggregation
- 174, 185, 192, 199, 201: Cycle detection and validation
- 216-220, 247-249, 252, 259-274: Aggregation rule and method handling
- 391, 455, 472, 499-506, etc.: Various aggregation methods
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.calculation.aggregators import (
    AggregationRule,
    CrossHierarchyAggregator,
    HierarchyNode,
    HierarchyType,
    MetricRuleEngine,
    OrganizationalAggregator,
    TemporalAggregator,
)
from src.calculation.engine import (
    AggregationMethod,
    CalculationContext,
    CalculationError,
    CalculationResult,
    TemporalGranularity,
)


class TestOrganizationalAggregatorCycleDetection:
    """Tests for cycle detection in hierarchies (lines 174, 185, 192)."""

    def test_cycle_detection_raises_error(self):
        """Test that cycles in hierarchy raise CalculationError."""
        aggregator = OrganizationalAggregator()

        # Create a hierarchy with a cycle
        hierarchy = {
            "node1": HierarchyNode(
                id="node1",
                name="Node 1",
                level=0,
                parent_id="node3",  # Creates cycle: 1 -> 3 -> 2 -> 1
                children_ids=["node2"],
            ),
            "node2": HierarchyNode(
                id="node2",
                name="Node 2",
                level=1,
                parent_id="node1",
                children_ids=["node3"],
            ),
            "node3": HierarchyNode(
                id="node3",
                name="Node 3",
                level=2,
                parent_id="node2",
                children_ids=["node1"],  # Back to node1 - cycle
            ),
        }

        with pytest.raises(CalculationError) as exc_info:
            aggregator._validate_hierarchy(hierarchy)

        assert "cycle" in str(exc_info.value).lower()

    def test_missing_parent_raises_error(self):
        """Test that missing parent raises CalculationError (line 199)."""
        aggregator = OrganizationalAggregator()

        hierarchy = {
            "node1": HierarchyNode(
                id="node1",
                name="Node 1",
                level=0,
                parent_id="nonexistent_parent",  # Parent doesn't exist
                children_ids=[],
            ),
        }

        with pytest.raises(CalculationError) as exc_info:
            aggregator._validate_hierarchy(hierarchy)

        assert "not found" in str(exc_info.value).lower()

    def test_inconsistent_parent_child_raises_error(self):
        """Test that inconsistent parent-child relationship raises error (line 201)."""
        aggregator = OrganizationalAggregator()

        hierarchy = {
            "parent": HierarchyNode(
                id="parent",
                name="Parent",
                level=0,
                parent_id=None,
                children_ids=[],  # Missing child reference
            ),
            "child": HierarchyNode(
                id="child",
                name="Child",
                level=1,
                parent_id="parent",  # Claims parent, but parent doesn't list it
                children_ids=[],
            ),
        }

        with pytest.raises(CalculationError) as exc_info:
            aggregator._validate_hierarchy(hierarchy)

        assert "not in parent's children" in str(exc_info.value).lower()


class TestAggregationRuleFinding:
    """Tests for aggregation rule finding (lines 216-220)."""

    def test_find_matching_rule(self):
        """Test finding an exact matching aggregation rule."""
        aggregator = OrganizationalAggregator()

        rules = [
            AggregationRule(
                metric_type="emissions",
                aggregation_method=AggregationMethod.SUM,
                hierarchy_type=HierarchyType.ORGANIZATIONAL,
                source_level=1,
                target_level=0,
            ),
        ]

        result = aggregator._find_aggregation_rule(
            rules,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        assert result is not None
        assert result.aggregation_method == AggregationMethod.SUM

    def test_find_rule_with_wildcard_levels(self):
        """Test finding rule with wildcard source/target levels (-1)."""
        aggregator = OrganizationalAggregator()

        rules = [
            AggregationRule(
                metric_type="any_metric",
                aggregation_method=AggregationMethod.AVERAGE,
                hierarchy_type=HierarchyType.ORGANIZATIONAL,
                source_level=-1,  # Wildcard
                target_level=-1,  # Wildcard
            ),
        ]

        result = aggregator._find_aggregation_rule(
            rules,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=5,  # Any level
            target_level=2,  # Any level
        )

        assert result is not None
        assert result.aggregation_method == AggregationMethod.AVERAGE

    def test_default_rule_when_none_match(self):
        """Test that default rule is returned when no rules match."""
        aggregator = OrganizationalAggregator()

        rules = []  # No rules

        result = aggregator._find_aggregation_rule(
            rules,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        assert result is not None
        assert result.aggregation_method == AggregationMethod.SUM  # Default

    def test_default_rule_uses_first_rule_method(self):
        """Test that default rule uses first rule's method if available."""
        aggregator = OrganizationalAggregator()

        rules = [
            AggregationRule(
                metric_type="emissions",
                aggregation_method=AggregationMethod.MAX,
                hierarchy_type=HierarchyType.TEMPORAL,  # Different type
                source_level=1,
                target_level=0,
            ),
        ]

        result = aggregator._find_aggregation_rule(
            rules,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,  # No match
            source_level=1,
            target_level=0,
        )

        assert result is not None
        assert (
            result.aggregation_method == AggregationMethod.MAX
        )  # Uses first rule's method


class TestAggregationMethods:
    """Tests for different aggregation methods (lines 259-274)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.aggregator = OrganizationalAggregator()
        self.context = CalculationContext(
            entity_id="test",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=0,
        )

    def _create_hierarchy_with_children(self, child_values: Dict[str, float]) -> tuple:
        """Helper to create a simple parent-children hierarchy."""
        children_ids = list(child_values.keys())

        parent = HierarchyNode(
            id="parent",
            name="Parent",
            level=0,
            parent_id=None,
            children_ids=children_ids,
        )

        hierarchy = {"parent": parent}
        results = {}

        for child_id, value in child_values.items():
            hierarchy[child_id] = HierarchyNode(
                id=child_id,
                name=f"Child {child_id}",
                level=1,
                parent_id="parent",
                children_ids=[],
            )
            results[child_id] = CalculationResult(
                value=Decimal(str(value)),
                unit="kg",
                context=self.context,
                formula_used="test",
                input_values={},
                calculation_timestamp=datetime.now(),
            )

        return parent, hierarchy, results

    def test_aggregation_method_sum(self):
        """Test SUM aggregation method."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
                "c3": 30.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.SUM,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 60.0

    def test_aggregation_method_average(self):
        """Test AVERAGE aggregation method."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
                "c3": 30.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.AVERAGE,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 20.0

    def test_aggregation_method_min(self):
        """Test MIN aggregation method."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
                "c3": 5.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.MIN,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 5.0

    def test_aggregation_method_max(self):
        """Test MAX aggregation method."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 50.0,
                "c3": 30.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.MAX,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 50.0

    def test_aggregation_method_count(self):
        """Test COUNT aggregation method."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
                "c3": 30.0,
                "c4": 40.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.COUNT,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 4.0

    def test_aggregation_method_weighted_average(self):
        """Test WEIGHTED_AVERAGE aggregation method (lines 259-266)."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
            }
        )

        # Add weights to hierarchy nodes
        hierarchy["c1"].metadata = {"weight": 1.0}
        hierarchy["c2"].metadata = {"weight": 3.0}

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
            weight_field="weight",
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        # (10*1 + 20*3) / (1+3) = 70/4 = 17.5
        assert float(result.value) == 17.5

    def test_aggregation_weighted_average_zero_weights(self):
        """Test WEIGHTED_AVERAGE with zero total weight returns 0."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
            }
        )

        # Set weights to zero
        hierarchy["c1"].metadata = {"weight": 0.0}
        hierarchy["c2"].metadata = {"weight": 0.0}

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
            weight_field="weight",
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 0.0

    def test_aggregation_weighted_average_no_weight_field(self):
        """Test WEIGHTED_AVERAGE without weight_field uses equal weights."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
            weight_field=None,  # No weight field
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        # Equal weights means simple average
        assert float(result.value) == 15.0

    def test_aggregation_no_children_returns_none(self):
        """Test that aggregation with no children returns None (line 252)."""
        parent = HierarchyNode(
            id="parent",
            name="Parent",
            level=0,
            parent_id=None,
            children_ids=["c1", "c2"],  # Reference children
        )

        hierarchy = {"parent": parent}
        results = {}  # No results for children

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=AggregationMethod.SUM,
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is None

    def test_aggregation_default_method_fallback(self):
        """Test fallback behavior for unusual aggregation scenarios."""
        parent, hierarchy, results = self._create_hierarchy_with_children(
            {
                "c1": 10.0,
                "c2": 20.0,
            }
        )

        rule = AggregationRule(
            metric_type="test",
            aggregation_method=SimpleNamespace(value="custom"),
            hierarchy_type=HierarchyType.ORGANIZATIONAL,
            source_level=1,
            target_level=0,
        )

        result = self.aggregator._aggregate_children(
            parent, hierarchy, results, rule, self.context
        )

        assert result is not None
        assert float(result.value) == 30.0


class TestOrganizationalAggregationException:
    """Tests for exception handling in organizational aggregation (lines 159-164)."""

    def test_aggregation_error_propagates(self):
        """Test that aggregation errors are properly wrapped."""
        aggregator = OrganizationalAggregator()

        context = CalculationContext(
            entity_id="test",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=0,
        )

        # This should work without error
        result = aggregator.aggregate_organizational_values(
            entity_values={"node2": Decimal("100")},
            hierarchy_config={
                "levels": [
                    {"id": "node1", "name": "Node 1", "parent": None},
                    {"id": "node2", "name": "Node 2", "parent": "node1"},
                ]
            },
            method=AggregationMethod.SUM,
            context=context,
        )

        assert "node1" in result

    def test_aggregate_up_hierarchy_wraps_validation_errors(self):
        aggregator = OrganizationalAggregator()
        context = CalculationContext(
            entity_id="test",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            temporal_granularity=TemporalGranularity.ANNUAL,
            organizational_level=0,
        )

        with pytest.raises(CalculationError, match="Organizational aggregation failed"):
            aggregator.aggregate_up_hierarchy(
                values={"child": Decimal("1")},
                hierarchy={
                    "child": HierarchyNode(
                        id="child",
                        name="Child",
                        level=1,
                        parent_id="missing",
                        children_ids=[],
                    )
                },
                aggregation_rules=[],
                context=context,
            )


class TestBuildHierarchyTree:
    """Tests for hierarchy tree building."""

    def test_build_hierarchy_tree(self):
        """Test building hierarchy tree from config."""
        aggregator = OrganizationalAggregator()

        config = {
            "levels": [
                {"id": "root", "name": "Root", "parent": None},
                {"id": "child1", "name": "Child 1", "parent": "root"},
                {"id": "child2", "name": "Child 2", "parent": "root"},
                {"id": "grandchild", "name": "Grandchild", "parent": "child1"},
            ]
        }

        tree = aggregator._build_hierarchy_tree(config)

        assert len(tree) == 4
        assert tree["root"].level == 0
        assert tree["child1"].level == 1
        assert tree["child2"].level == 1
        assert tree["grandchild"].level == 2
        assert "child1" in tree["root"].children_ids
        assert "child2" in tree["root"].children_ids


class TestTemporalAggregator:
    """Tests for TemporalAggregator."""

    def test_temporal_aggregator_exists(self):
        """Test that TemporalAggregator can be instantiated."""
        aggregator = TemporalAggregator()
        assert aggregator is not None

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            (AggregationMethod.MIN, Decimal("1.0000")),
            (AggregationMethod.MAX, Decimal("5.0000")),
            (AggregationMethod.COUNT, Decimal("3.0000")),
            (AggregationMethod.FIRST, Decimal("5.0000")),
            (AggregationMethod.LAST, Decimal("1.0000")),
        ],
    )
    def test_temporal_period_aggregation_methods(self, method, expected):
        aggregator = TemporalAggregator()
        context = CalculationContext(
            entity_id="entity",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            temporal_granularity=TemporalGranularity.MONTHLY,
            organizational_level=0,
        )
        rule = AggregationRule(
            metric_type="test",
            aggregation_method=method,
            hierarchy_type=HierarchyType.TEMPORAL,
            source_level=0,
            target_level=1,
        )

        result = aggregator._aggregate_period_values(
            {
                date(2026, 1, 1): Decimal("5"),
                date(2026, 1, 2): Decimal("2"),
                date(2026, 1, 3): Decimal("1"),
            },
            rule,
            context,
            "2026-01",
        )

        assert result.value == expected
        assert result.metadata["source_dates_count"] == 3

    def test_temporal_period_identifiers_and_ranges_cover_all_supported_granularities(
        self,
    ):
        aggregator = TemporalAggregator()

        assert (
            aggregator._get_period_identifier(
                date(2026, 1, 2), TemporalGranularity.DAILY
            )
            == "2026-01-02"
        )
        assert (
            aggregator._get_period_identifier(
                date(2026, 1, 2), TemporalGranularity.WEEKLY
            )
            == "2026-W01"
        )
        assert (
            aggregator._get_period_identifier(
                date(2026, 5, 2), TemporalGranularity.SEMESTRAL
            )
            == "2026-S1"
        )
        assert (
            aggregator._get_period_identifier(
                date(2026, 9, 2), TemporalGranularity.SEMESTRAL
            )
            == "2026-S2"
        )

        assert aggregator.get_temporal_periods_in_range(
            date(2026, 1, 1), date(2026, 1, 3), TemporalGranularity.DAILY
        ) == ["2026-01-01", "2026-01-02", "2026-01-03"]
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 1, 1), date(2026, 1, 15), TemporalGranularity.WEEKLY
        ) == ["2026-W01", "2026-W02", "2026-W03"]
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 11, 1), date(2027, 1, 1), TemporalGranularity.MONTHLY
        ) == ["2026-11", "2026-12", "2027-01"]
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 10, 1), date(2027, 1, 1), TemporalGranularity.QUARTERLY
        ) == ["2026-Q4", "2027-Q1"]
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 7, 1), date(2027, 1, 1), TemporalGranularity.SEMESTRAL
        ) == ["2026-S2", "2027-S1"]
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 1, 1), date(2027, 1, 1), TemporalGranularity.ANNUAL
        ) == ["2026", "2027"]

    def test_temporal_default_rule_empty_group_and_error_paths(self, monkeypatch):
        aggregator = TemporalAggregator()
        context = CalculationContext(
            entity_id="entity",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            temporal_granularity=TemporalGranularity.MONTHLY,
            organizational_level=0,
        )

        default_rule = aggregator._find_temporal_aggregation_rule(
            [],
            TemporalGranularity.DAILY,
            TemporalGranularity.MONTHLY,
        )
        assert default_rule.aggregation_method == AggregationMethod.SUM

        monkeypatch.setattr(
            aggregator,
            "_group_by_target_period",
            lambda *_args, **_kwargs: {"empty": {}},
        )
        assert (
            aggregator.aggregate_temporal_periods(
                {date(2026, 1, 1): Decimal("1")},
                TemporalGranularity.DAILY,
                TemporalGranularity.MONTHLY,
                [],
                context,
            )
            == {}
        )

        with pytest.raises(CalculationError, match="Temporal aggregation failed"):
            aggregator.aggregate_temporal_periods(
                {date(2026, 1, 1): Decimal("1")},
                TemporalGranularity.MONTHLY,
                TemporalGranularity.DAILY,
                [],
                context,
            )

        unsupported = SimpleNamespace(value="biweekly")
        with pytest.raises(CalculationError, match="Unsupported granularity"):
            aggregator._get_period_identifier(date(2026, 1, 1), unsupported)

        monkeypatch.setattr(
            aggregator,
            "_get_period_identifier",
            lambda *_args, **_kwargs: "custom",
        )
        assert aggregator.get_temporal_periods_in_range(
            date(2026, 1, 1), date(2026, 1, 2), unsupported
        ) == ["custom"]


class TestCrossHierarchyAndMetricRules:
    def _context(self) -> CalculationContext:
        return CalculationContext(
            entity_id="root",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            temporal_granularity=TemporalGranularity.MONTHLY,
            organizational_level=0,
            metadata={"source": "test"},
        )

    def _hierarchy_config(self) -> dict:
        return {
            "levels": [
                {"id": "root", "name": "Root", "parent": None},
                {"id": "child-a", "name": "A", "parent": "root"},
                {"id": "child-b", "name": "B", "parent": "root"},
            ]
        }

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            (AggregationMethod.SUM, Decimal("100.00")),
            (AggregationMethod.AVERAGE, Decimal("25.00")),
            (AggregationMethod.MIN, Decimal("10.00")),
            (AggregationMethod.MAX, Decimal("40.00")),
        ],
    )
    def test_cross_hierarchy_aggregates_supported_methods(self, method, expected):
        aggregator = CrossHierarchyAggregator(SimpleNamespace(precision=2))

        results = aggregator.aggregate_cross_hierarchy(
            values={
                ("child-a", date(2026, 1, 1)): Decimal("10"),
                ("child-a", date(2026, 1, 2)): Decimal("10"),
                ("child-b", date(2026, 1, 1)): Decimal("40"),
                ("child-b", date(2026, 1, 2)): Decimal("40"),
                ("other", date(2026, 1, 1)): Decimal("999"),
            },
            hierarchy_config=self._hierarchy_config(),
            target_entity="root",
            target_granularity=TemporalGranularity.MONTHLY,
            source_granularity=TemporalGranularity.DAILY,
            method=method,
            context=self._context(),
        )

        assert results["2026-01"].value == expected
        assert set(results["2026-01"].metadata["entities_aggregated"]) == {
            "child-a",
            "child-b",
        }

    def test_cross_hierarchy_empty_and_error_paths(self):
        aggregator = CrossHierarchyAggregator(SimpleNamespace(precision=2))
        assert (
            aggregator.aggregate_cross_hierarchy(
                values={},
                hierarchy_config=self._hierarchy_config(),
                target_entity="root",
                target_granularity=TemporalGranularity.MONTHLY,
                source_granularity=TemporalGranularity.DAILY,
                method=AggregationMethod.SUM,
                context=self._context(),
            )
            == {}
        )

        aggregator.organizational_aggregator._build_hierarchy_tree = MagicMock(
            side_effect=RuntimeError("bad hierarchy")
        )
        with pytest.raises(
            CalculationError, match="Cross-hierarchy aggregation failed"
        ):
            aggregator.aggregate_cross_hierarchy(
                values={("child-a", date(2026, 1, 1)): Decimal("1")},
                hierarchy_config={
                    "levels": [{"id": "child-a", "name": "A", "parent": "missing"}]
                },
                target_entity="root",
                target_granularity=TemporalGranularity.MONTHLY,
                source_granularity=TemporalGranularity.DAILY,
                method=AggregationMethod.SUM,
                context=self._context(),
            )

    def test_metric_rule_engine_known_default_and_apply_paths(self):
        engine = MetricRuleEngine()
        context = self._context()
        known = engine.get_aggregation_rules_for_metric("efficiency")
        default = engine.get_aggregation_rules_for_metric("unknown")

        assert known[0].aggregation_method == AggregationMethod.WEIGHTED_AVERAGE
        assert default[0].metric_type == "default"
        org_results = engine.apply_organizational_aggregation(
            values={"child-a": Decimal("10"), "child-b": Decimal("20")},
            hierarchy={
                "root": HierarchyNode("root", "Root", 0, None, ["child-a", "child-b"]),
                "child-a": HierarchyNode("child-a", "A", 1, "root", []),
                "child-b": HierarchyNode("child-b", "B", 1, "root", []),
            },
            metric_type="emissions",
            context=context,
        )
        temporal_results = engine.apply_temporal_aggregation(
            values={date(2026, 1, 1): Decimal("1"), date(2026, 1, 2): Decimal("2")},
            source_granularity=TemporalGranularity.DAILY,
            target_granularity=TemporalGranularity.MONTHLY,
            metric_type="energy",
            context=context,
        )

        assert org_results["root"].value == Decimal("30.0000")
        assert temporal_results["2026-01"].value == Decimal("3.0000")
