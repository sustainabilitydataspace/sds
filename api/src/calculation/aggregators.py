"""Aggregation system for organizational and temporal hierarchies."""

import calendar
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import structlog
from src.calculation.engine import (
    AggregationMethod,
    CalculationContext,
    CalculationError,
    CalculationResult,
    CalculationTrace,
    TemporalGranularity,
)

logger = structlog.get_logger(__name__)


class HierarchyType(Enum):
    """Types of hierarchies supported."""

    ORGANIZATIONAL = "organizational"
    TEMPORAL = "temporal"


@dataclass
class HierarchyNode:
    """Node in a hierarchy tree."""

    id: str
    name: str
    level: int
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def children(self) -> List[str]:
        """Alias for children_ids for backward compatibility."""
        return self.children_ids


@dataclass
class AggregationRule:
    """Rule for aggregating values in hierarchies."""

    metric_type: str
    aggregation_method: AggregationMethod
    hierarchy_type: HierarchyType
    source_level: int
    target_level: int
    weight_field: Optional[str] = None
    conditions: Dict[str, Any] = field(default_factory=dict)


class OrganizationalAggregator:
    """Handles aggregation across organizational hierarchies."""

    def __init__(self, engine_or_precision=4):
        """Initialize organizational aggregator.

        Args:
            engine_or_precision: CalculationEngine instance or precision integer
        """
        if hasattr(engine_or_precision, "precision"):
            # It's a CalculationEngine
            self.precision = engine_or_precision.precision
            self.engine = engine_or_precision
        else:
            # It's a precision integer
            self.precision = engine_or_precision
            self.engine = None
        self.logger = logger.bind(component="OrganizationalAggregator")

    def aggregate_up_hierarchy(
        self,
        values: Dict[str, Union[int, float, Decimal]],
        hierarchy: Dict[str, HierarchyNode],
        aggregation_rules: List[AggregationRule],
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Aggregate values up the organizational hierarchy.

        Args:
            values: Dictionary mapping entity IDs to values
            hierarchy: Organizational hierarchy structure
            aggregation_rules: Rules for aggregation
            context: Calculation context

        Returns:
            Dictionary mapping entity IDs to aggregated results
        """
        try:
            self.logger.info(
                "Starting organizational aggregation",
                entities_count=len(values),
                hierarchy_nodes=len(hierarchy),
            )

            if not values or not hierarchy:
                return {}

            # Validate hierarchy structure
            self._validate_hierarchy(hierarchy)

            # Initialize results with leaf values
            results = {}
            for entity_id, value in values.items():
                if entity_id in hierarchy:
                    node = hierarchy[entity_id]
                    entity_context = CalculationContext(
                        entity_id=entity_id,
                        period_start=context.period_start,
                        period_end=context.period_end,
                        temporal_granularity=context.temporal_granularity,
                        organizational_level=node.level,
                        metadata=context.metadata,
                    )

                    results[entity_id] = CalculationResult(
                        value=Decimal(str(value)),
                        unit=None,
                        context=entity_context,
                        formula_used="direct_value",
                        input_values={"original_value": value},
                        calculation_timestamp=datetime.now(),
                    )

            # Get levels sorted from deepest to shallowest
            levels = sorted(
                set(node.level for node in hierarchy.values()), reverse=True
            )

            # Aggregate level by level
            for level in levels:
                if level == max(levels):
                    continue  # Skip leaf level

                level_nodes = [
                    node for node in hierarchy.values() if node.level == level
                ]

                for node in level_nodes:
                    if node.id not in results:
                        # Find applicable aggregation rule
                        rule = self._find_aggregation_rule(
                            aggregation_rules,
                            HierarchyType.ORGANIZATIONAL,
                            level + 1,  # Source level (children)
                            level,  # Target level (current)
                        )

                        if rule:
                            aggregated_result = self._aggregate_children(
                                node, hierarchy, results, rule, context
                            )
                            if aggregated_result:
                                results[node.id] = aggregated_result

            self.logger.info(
                "Organizational aggregation completed", results_count=len(results)
            )

            return results

        except Exception as e:
            self.logger.error("Organizational aggregation failed", error=str(e))
            raise CalculationError(f"Organizational aggregation failed: {e}")

    def _validate_hierarchy(self, hierarchy: Dict[str, HierarchyNode]):
        """Validate hierarchy structure for cycles and consistency."""
        # Check for cycles using DFS
        visited = set()
        rec_stack = set()

        def has_cycle(node_id: str) -> bool:
            if node_id in rec_stack:
                return True
            if node_id in visited:
                return False

            visited.add(node_id)
            rec_stack.add(node_id)

            node = hierarchy.get(node_id)
            if node:
                for child_id in node.children_ids:
                    if has_cycle(child_id):
                        return True

            rec_stack.remove(node_id)
            return False

        for node_id in hierarchy:
            if has_cycle(node_id):
                raise CalculationError(
                    f"Cycle detected in hierarchy at node: {node_id}"
                )

        # Validate parent-child relationships
        for node_id, node in hierarchy.items():
            if node.parent_id:
                parent = hierarchy.get(node.parent_id)
                if not parent:
                    raise CalculationError(
                        f"Parent {node.parent_id} not found for node {node_id}"
                    )
                if node_id not in parent.children_ids:
                    raise CalculationError(
                        f"Node {node_id} not in parent's children list"
                    )

    def _find_aggregation_rule(
        self,
        rules: List[AggregationRule],
        hierarchy_type: HierarchyType,
        source_level: int,
        target_level: int,
    ) -> Optional[AggregationRule]:
        """Find applicable aggregation rule."""
        for rule in rules:
            if (
                rule.hierarchy_type == hierarchy_type
                and (rule.source_level == source_level or rule.source_level == -1)
                and (rule.target_level == target_level or rule.target_level == -1)
            ):
                return rule

        # Default rule if none found - use the first rule's method if available
        default_method = AggregationMethod.SUM
        if rules:
            default_method = rules[0].aggregation_method

        return AggregationRule(
            metric_type="default",
            aggregation_method=default_method,
            hierarchy_type=hierarchy_type,
            source_level=source_level,
            target_level=target_level,
        )

    def _aggregate_children(
        self,
        node: HierarchyNode,
        hierarchy: Dict[str, HierarchyNode],
        results: Dict[str, CalculationResult],
        rule: AggregationRule,
        context: CalculationContext,
    ) -> Optional[CalculationResult]:
        """Aggregate children values for a node."""
        child_values = []
        child_weights = []
        input_values = {}

        for child_id in node.children_ids:
            if child_id in results:
                child_result = results[child_id]
                child_values.append(float(child_result.value))
                input_values[child_id] = float(child_result.value)

                # Get weight if specified
                if rule.weight_field:
                    child_node = hierarchy.get(child_id)
                    weight = (
                        child_node.metadata.get(rule.weight_field, 1.0)
                        if child_node
                        else 1.0
                    )
                    child_weights.append(weight)

        if not child_values:
            return None

        # Apply aggregation method
        if rule.aggregation_method == AggregationMethod.SUM:
            aggregated_value = sum(child_values)
        elif rule.aggregation_method == AggregationMethod.AVERAGE:
            aggregated_value = sum(child_values) / len(child_values)
        elif rule.aggregation_method == AggregationMethod.WEIGHTED_AVERAGE:
            if not child_weights:
                child_weights = [1.0] * len(child_values)
            total_weight = sum(child_weights)
            if total_weight > 0:
                aggregated_value = (
                    sum(v * w for v, w in zip(child_values, child_weights))
                    / total_weight
                )
            else:
                aggregated_value = 0
        elif rule.aggregation_method == AggregationMethod.MIN:
            aggregated_value = min(child_values)
        elif rule.aggregation_method == AggregationMethod.MAX:
            aggregated_value = max(child_values)
        elif rule.aggregation_method == AggregationMethod.COUNT:
            aggregated_value = len(child_values)
        else:
            aggregated_value = sum(child_values)  # Default to sum

        # Apply precision
        aggregated_value = Decimal(str(aggregated_value)).quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )

        # Create result
        node_context = CalculationContext(
            entity_id=node.id,
            period_start=context.period_start,
            period_end=context.period_end,
            temporal_granularity=context.temporal_granularity,
            organizational_level=node.level,
            metadata=context.metadata,
        )

        return CalculationResult(
            value=aggregated_value,
            unit=None,
            context=node_context,
            formula_used=f"{rule.aggregation_method.value}({len(child_values)} children)",
            input_values=input_values,
            calculation_timestamp=datetime.now(),
            metadata={"aggregation_rule": rule.metric_type},
        )

    def _build_hierarchy_tree(
        self, hierarchy_config: Dict[str, Any]
    ) -> Dict[str, HierarchyNode]:
        """Build hierarchy tree from configuration."""
        tree = {}
        levels = hierarchy_config.get("levels", [])

        # Create nodes
        for level in levels:
            entity_id = level["id"]
            tree[entity_id] = HierarchyNode(
                id=entity_id,
                name=level["name"],
                level=0,  # Will be calculated later
                parent_id=level.get("parent"),
                children_ids=[],
                metadata=level.get("metadata", {}),
            )

        # Build parent-child relationships
        for entity_id, node in tree.items():
            parent_id = node.parent_id
            if parent_id and parent_id in tree:
                tree[parent_id].children_ids.append(entity_id)

        # Calculate hierarchy levels
        self._calculate_hierarchy_levels(tree)

        return tree

    def _calculate_hierarchy_levels(self, tree: Dict[str, HierarchyNode]):
        """Calculate hierarchy levels (0 = root, 1 = first level, etc.)."""
        # Find root nodes
        roots = [entity_id for entity_id, node in tree.items() if not node.parent_id]

        # BFS to assign levels
        queue = [(root_id, 0) for root_id in roots]

        while queue:
            entity_id, level = queue.pop(0)
            tree[entity_id].level = level

            for child_id in tree[entity_id].children_ids:
                queue.append((child_id, level + 1))

    def aggregate_organizational_values(
        self,
        entity_values: Dict[str, Union[int, float, Decimal]],
        hierarchy_config: Dict[str, Any],
        method: AggregationMethod,
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Aggregate values across organizational hierarchy.

        Args:
            entity_values: Dictionary mapping entity IDs to values
            hierarchy_config: Organizational hierarchy configuration
            method: Aggregation method
            context: Calculation context

        Returns:
            Dictionary mapping entity IDs to aggregated results
        """
        # Create aggregation rules for this method
        rules = [
            AggregationRule(
                metric_type="default",
                aggregation_method=method,
                hierarchy_type=HierarchyType.ORGANIZATIONAL,
                source_level=-1,
                target_level=-1,
            )
        ]

        # Build hierarchy
        hierarchy = self._build_hierarchy_tree(hierarchy_config)

        return self.aggregate_up_hierarchy(entity_values, hierarchy, rules, context)

    def get_entity_path(
        self, entity_id: str, tree: Dict[str, HierarchyNode]
    ) -> List[str]:
        """Get path from entity to root."""
        path = []
        current_id = entity_id

        while current_id and current_id in tree:
            path.append(current_id)
            current_id = tree[current_id].parent_id

        return path

    def get_entity_descendants(
        self, entity_id: str, tree: Dict[str, HierarchyNode]
    ) -> set:
        """Get all descendants of an entity."""
        descendants = set()

        if entity_id not in tree:
            return descendants

        # BFS to find all descendants
        queue = list(tree[entity_id].children_ids)

        while queue:
            child_id = queue.pop(0)
            if child_id in tree:
                descendants.add(child_id)
                queue.extend(tree[child_id].children_ids)

        return descendants


class TemporalAggregator:
    """Handles aggregation across temporal hierarchies."""

    def __init__(self, engine_or_precision=4):
        """Initialize temporal aggregator.

        Args:
            engine_or_precision: CalculationEngine instance or precision integer
        """
        if hasattr(engine_or_precision, "precision"):
            # It's a CalculationEngine
            self.precision = engine_or_precision.precision
            self.engine = engine_or_precision
        else:
            # It's a precision integer
            self.precision = engine_or_precision
            self.engine = None
        self.logger = logger.bind(component="TemporalAggregator")

    def aggregate_temporal_periods(
        self,
        values: Dict[date, Union[int, float, Decimal]],
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
        aggregation_rules: List[AggregationRule],
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Aggregate values across temporal periods.

        Args:
            values: Dictionary mapping dates to values
            source_granularity: Source temporal granularity
            target_granularity: Target temporal granularity
            aggregation_rules: Rules for aggregation
            context: Calculation context

        Returns:
            Dictionary mapping period identifiers to aggregated results
        """
        try:
            self.logger.info(
                "Starting temporal aggregation",
                source_granularity=source_granularity.value,
                target_granularity=target_granularity.value,
                values_count=len(values),
            )

            if not values:
                return {}

            # Validate granularity hierarchy
            if not self._is_valid_granularity_aggregation(
                source_granularity, target_granularity
            ):
                raise CalculationError(
                    f"Cannot aggregate from {source_granularity.value} to {target_granularity.value}"
                )

            # Group values by target periods
            period_groups = self._group_by_target_period(values, target_granularity)

            # Find aggregation rule
            rule = self._find_temporal_aggregation_rule(
                aggregation_rules, source_granularity, target_granularity
            )

            results = {}
            for period_id, period_values in period_groups.items():
                if not period_values:
                    continue

                # Create period context
                period_dates = list(period_values.keys())
                period_context = CalculationContext(
                    entity_id=context.entity_id,
                    period_start=min(period_dates),
                    period_end=max(period_dates),
                    temporal_granularity=target_granularity,
                    organizational_level=context.organizational_level,
                    metadata=context.metadata,
                )

                # Aggregate period values
                aggregated_result = self._aggregate_period_values(
                    period_values, rule, period_context, period_id
                )

                results[period_id] = aggregated_result

            self.logger.info(
                "Temporal aggregation completed", periods_created=len(results)
            )

            return results

        except Exception as e:
            self.logger.error(
                "Temporal aggregation failed",
                error=str(e),
                source_granularity=source_granularity.value,
                target_granularity=target_granularity.value,
            )
            raise CalculationError(f"Temporal aggregation failed: {e}")

    def _is_valid_granularity_aggregation(
        self, source: TemporalGranularity, target: TemporalGranularity
    ) -> bool:
        """Check if aggregation from source to target granularity is valid."""
        granularity_hierarchy = {
            TemporalGranularity.DAILY: 0,
            TemporalGranularity.WEEKLY: 1,
            TemporalGranularity.MONTHLY: 2,
            TemporalGranularity.QUARTERLY: 3,
            TemporalGranularity.SEMESTRAL: 4,
            TemporalGranularity.ANNUAL: 5,
        }

        return granularity_hierarchy[source] < granularity_hierarchy[target]

    def _group_by_target_period(
        self,
        values: Dict[date, Union[int, float, Decimal]],
        target_granularity: TemporalGranularity,
    ) -> Dict[str, Dict[date, Union[int, float, Decimal]]]:
        """Group values by target temporal period."""
        groups = defaultdict(dict)

        for value_date, value in values.items():
            period_id = self._get_period_identifier(value_date, target_granularity)
            groups[period_id][value_date] = value

        return dict(groups)

    def _get_period_identifier(
        self, value_date: date, granularity: TemporalGranularity
    ) -> str:
        """Get period identifier for a date and granularity."""
        if granularity == TemporalGranularity.DAILY:
            return value_date.strftime("%Y-%m-%d")
        elif granularity == TemporalGranularity.WEEKLY:
            year, week, _ = value_date.isocalendar()
            return f"{year}-W{week:02d}"
        elif granularity == TemporalGranularity.MONTHLY:
            return value_date.strftime("%Y-%m")
        elif granularity == TemporalGranularity.QUARTERLY:
            quarter = (value_date.month - 1) // 3 + 1
            return f"{value_date.year}-Q{quarter}"
        elif granularity == TemporalGranularity.SEMESTRAL:
            semester = 1 if value_date.month <= 6 else 2
            return f"{value_date.year}-S{semester}"
        elif granularity == TemporalGranularity.ANNUAL:
            return str(value_date.year)
        else:
            raise CalculationError(f"Unsupported granularity: {granularity}")

    def _find_temporal_aggregation_rule(
        self,
        rules: List[AggregationRule],
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
    ) -> AggregationRule:
        """Find applicable temporal aggregation rule."""
        for rule in rules:
            if rule.hierarchy_type == HierarchyType.TEMPORAL:
                return rule

        # Default temporal aggregation rule
        return AggregationRule(
            metric_type="default",
            aggregation_method=AggregationMethod.SUM,
            hierarchy_type=HierarchyType.TEMPORAL,
            source_level=0,
            target_level=1,
        )

    def _aggregate_period_values(
        self,
        period_values: Dict[date, Union[int, float, Decimal]],
        rule: AggregationRule,
        context: CalculationContext,
        period_id: str,
    ) -> CalculationResult:
        """Aggregate values within a period."""
        values_list = list(period_values.values())
        numeric_values = [float(v) for v in values_list]

        # Apply aggregation method
        if rule.aggregation_method == AggregationMethod.SUM:
            aggregated_value = sum(numeric_values)
        elif rule.aggregation_method == AggregationMethod.AVERAGE:
            aggregated_value = sum(numeric_values) / len(numeric_values)
        elif rule.aggregation_method == AggregationMethod.MIN:
            aggregated_value = min(numeric_values)
        elif rule.aggregation_method == AggregationMethod.MAX:
            aggregated_value = max(numeric_values)
        elif rule.aggregation_method == AggregationMethod.COUNT:
            aggregated_value = len(numeric_values)
        elif rule.aggregation_method == AggregationMethod.FIRST:
            # Get first chronologically
            sorted_dates = sorted(period_values.keys())
            aggregated_value = float(period_values[sorted_dates[0]])
        elif rule.aggregation_method == AggregationMethod.LAST:
            # Get last chronologically
            sorted_dates = sorted(period_values.keys())
            aggregated_value = float(period_values[sorted_dates[-1]])
        else:
            aggregated_value = sum(numeric_values)  # Default to sum

        # Apply precision
        aggregated_value = Decimal(str(aggregated_value)).quantize(
            Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
        )

        # Create input values dict
        input_values = {
            date_str: float(value) for date_str, value in period_values.items()
        }

        # Create trace
        trace = CalculationTrace(
            calculation_id=f"temporal_{period_id}",
            formula_steps=[f"Temporal aggregation for period {period_id}"],
            aggregations_applied=[f"temporal_monthly_to_quarterly"],
            dependencies_resolved=list(input_values.keys()),
        )

        return CalculationResult(
            value=aggregated_value,
            unit=None,
            context=context,
            formula_used=f"{rule.aggregation_method.value}({len(numeric_values)} values)",
            input_values=input_values,
            calculation_timestamp=datetime.now(),
            trace=trace,
            metadata={
                "period_id": period_id,
                "aggregation_rule": rule.metric_type,
                "source_dates_count": len(period_values),
            },
        )

    def aggregate_temporal_values(
        self,
        values: Dict[date, Union[int, float, Decimal]],
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
        method: AggregationMethod,
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Aggregate temporal values with specified method.

        Args:
            values: Dictionary mapping dates to values
            source_granularity: Source temporal granularity
            target_granularity: Target temporal granularity
            method: Aggregation method
            context: Calculation context

        Returns:
            Dictionary mapping period identifiers to aggregated results
        """
        # Create aggregation rules for this method
        rules = [
            AggregationRule(
                metric_type="default",
                aggregation_method=method,
                hierarchy_type=HierarchyType.TEMPORAL,
                source_level=0,
                target_level=1,
            )
        ]

        return self.aggregate_temporal_periods(
            values, source_granularity, target_granularity, rules, context
        )

    def _validate_temporal_hierarchy(
        self,
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
    ):
        """Validate that temporal aggregation is valid."""
        if not self._is_valid_granularity_aggregation(
            source_granularity, target_granularity
        ):
            raise CalculationError(
                f"Invalid temporal aggregation from {source_granularity.value} to {target_granularity.value}"
            )

    def get_temporal_periods_in_range(
        self, start_date: date, end_date: date, granularity: TemporalGranularity
    ) -> List[str]:
        """Get all temporal periods within a date range.

        Args:
            start_date: Start date of range
            end_date: End date of range
            granularity: Temporal granularity

        Returns:
            List of period identifiers
        """
        periods = set()
        current_date = start_date

        while current_date <= end_date:
            period_id = self._get_period_identifier(current_date, granularity)
            periods.add(period_id)

            # Move to next period
            if granularity == TemporalGranularity.DAILY:
                current_date += timedelta(days=1)
            elif granularity == TemporalGranularity.WEEKLY:
                current_date += timedelta(weeks=1)
            elif granularity == TemporalGranularity.MONTHLY:
                if current_date.month == 12:
                    current_date = current_date.replace(
                        year=current_date.year + 1, month=1
                    )
                else:
                    current_date = current_date.replace(month=current_date.month + 1)
            elif granularity == TemporalGranularity.QUARTERLY:
                new_month = current_date.month + 3
                if new_month > 12:
                    current_date = current_date.replace(
                        year=current_date.year + 1, month=new_month - 12
                    )
                else:
                    current_date = current_date.replace(month=new_month)
            elif granularity == TemporalGranularity.SEMESTRAL:
                new_month = current_date.month + 6
                if new_month > 12:
                    current_date = current_date.replace(
                        year=current_date.year + 1, month=new_month - 12
                    )
                else:
                    current_date = current_date.replace(month=new_month)
            elif granularity == TemporalGranularity.ANNUAL:
                current_date = current_date.replace(year=current_date.year + 1)
            else:
                break

        return sorted(list(periods))


@dataclass
class HierarchyLevel:
    """Represents a level in an organizational hierarchy."""

    id: str
    name: str
    parent_id: Optional[str]
    level: int
    children: List[str] = field(default_factory=list)


class CrossHierarchyAggregator:
    """Handles aggregation across both temporal and organizational hierarchies."""

    def __init__(self, engine):
        """Initialize cross-hierarchy aggregator.

        Args:
            engine: CalculationEngine instance
        """
        self.engine = engine
        self.precision = engine.precision
        self.logger = logger.bind(component="CrossHierarchyAggregator")
        self.organizational_aggregator = OrganizationalAggregator()
        self.temporal_aggregator = TemporalAggregator()

    def aggregate_cross_hierarchy(
        self,
        values: Dict[Tuple[str, date], Union[int, float, Decimal]],
        hierarchy_config: Dict[str, Any],
        target_entity: str,
        target_granularity: TemporalGranularity,
        source_granularity: TemporalGranularity,
        method: AggregationMethod,
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Aggregate values across both temporal and organizational hierarchies.

        Args:
            values: Dictionary mapping (entity_id, date) tuples to values
            hierarchy_config: Organizational hierarchy configuration
            target_entity: Target entity for organizational aggregation
            target_granularity: Target temporal granularity
            source_granularity: Source temporal granularity
            method: Aggregation method
            context: Calculation context

        Returns:
            Dictionary mapping period identifiers to aggregated results
        """
        try:
            self.logger.info(
                "Starting cross-hierarchy aggregation",
                target_entity=target_entity,
                target_granularity=target_granularity.value,
                source_granularity=source_granularity.value,
                values_count=len(values),
            )

            if not values:
                return {}

            # Build hierarchy tree
            hierarchy_tree = self.organizational_aggregator._build_hierarchy_tree(
                hierarchy_config
            )

            # Get descendants of target entity
            descendants = self.organizational_aggregator.get_entity_descendants(
                target_entity, hierarchy_tree
            )
            descendants.add(target_entity)  # Include the target entity itself

            # Filter values to only include relevant entities
            filtered_values = {
                (entity_id, value_date): value
                for (entity_id, value_date), value in values.items()
                if entity_id in descendants
            }

            # Group by temporal periods first
            period_groups = defaultdict(dict)
            for (entity_id, value_date), value in filtered_values.items():
                period_id = self.temporal_aggregator._get_period_identifier(
                    value_date, target_granularity
                )
                if entity_id not in period_groups[period_id]:
                    period_groups[period_id][entity_id] = []
                period_groups[period_id][entity_id].append(value)

            # Aggregate within each period
            results = {}
            for period_id, period_entity_values in period_groups.items():
                # First aggregate temporally within each entity
                entity_aggregated = {}
                for entity_id, entity_values in period_entity_values.items():
                    if method == AggregationMethod.SUM:
                        entity_aggregated[entity_id] = sum(entity_values)
                    elif method == AggregationMethod.AVERAGE:
                        entity_aggregated[entity_id] = sum(entity_values) / len(
                            entity_values
                        )
                    elif method == AggregationMethod.MIN:
                        entity_aggregated[entity_id] = min(entity_values)
                    elif method == AggregationMethod.MAX:
                        entity_aggregated[entity_id] = max(entity_values)
                    else:
                        entity_aggregated[entity_id] = sum(entity_values)

                # Then aggregate organizationally
                if method == AggregationMethod.SUM:
                    total_value = sum(entity_aggregated.values())
                elif method == AggregationMethod.AVERAGE:
                    total_value = sum(entity_aggregated.values()) / len(
                        entity_aggregated
                    )
                elif method == AggregationMethod.MIN:
                    total_value = min(entity_aggregated.values())
                elif method == AggregationMethod.MAX:
                    total_value = max(entity_aggregated.values())
                else:
                    total_value = sum(entity_aggregated.values())

                # Apply precision
                total_value = Decimal(str(total_value)).quantize(
                    Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
                )

                # Create result with trace
                trace = CalculationTrace(
                    calculation_id=f"cross_hierarchy_{period_id}",
                    formula_steps=[f"Cross-hierarchy aggregation for {period_id}"],
                    aggregations_applied=[f"cross_hierarchy_{method.value}"],
                    dependencies_resolved=list(entity_aggregated.keys()),
                )

                period_context = CalculationContext(
                    entity_id=target_entity,
                    period_start=context.period_start,
                    period_end=context.period_end,
                    temporal_granularity=target_granularity,
                    organizational_level=(
                        hierarchy_tree[target_entity].level
                        if target_entity in hierarchy_tree
                        else 0
                    ),
                    metadata=context.metadata,
                )

                results[period_id] = CalculationResult(
                    value=total_value,
                    unit=None,
                    context=period_context,
                    formula_used=f"cross_hierarchy_{method.value}",
                    input_values=entity_aggregated,
                    calculation_timestamp=datetime.now(),
                    trace=trace,
                    metadata={
                        "period_id": period_id,
                        "entities_aggregated": list(entity_aggregated.keys()),
                        "aggregation_method": method.value,
                    },
                )

            self.logger.info(
                "Cross-hierarchy aggregation completed", periods_created=len(results)
            )

            return results

        except Exception as e:
            self.logger.error(
                "Cross-hierarchy aggregation failed",
                error=str(e),
                target_entity=target_entity,
            )
            raise CalculationError(f"Cross-hierarchy aggregation failed: {e}")


class MetricRuleEngine:
    """Engine for applying metric-specific aggregation rules."""

    def __init__(self):
        """Initialize metric rule engine."""
        self.logger = logger.bind(component="MetricRuleEngine")
        self.organizational_aggregator = OrganizationalAggregator()
        self.temporal_aggregator = TemporalAggregator()

    def get_aggregation_rules_for_metric(
        self, metric_type: str
    ) -> List[AggregationRule]:
        """Get aggregation rules for a specific metric type.

        Args:
            metric_type: Type of metric (e.g., 'emissions', 'energy', 'water')

        Returns:
            List of applicable aggregation rules
        """
        # Define metric-specific rules
        metric_rules = {
            # Emissions - typically summed across org and time
            "emissions": [
                AggregationRule(
                    metric_type="emissions",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,  # Any child level
                    target_level=-1,  # Any parent level
                ),
                AggregationRule(
                    metric_type="emissions",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Energy consumption - summed
            "energy": [
                AggregationRule(
                    metric_type="energy",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                ),
                AggregationRule(
                    metric_type="energy",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Water consumption - summed
            "water": [
                AggregationRule(
                    metric_type="water",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                ),
                AggregationRule(
                    metric_type="water",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Efficiency ratios - weighted average by size/volume
            "efficiency": [
                AggregationRule(
                    metric_type="efficiency",
                    aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                    weight_field="volume",
                ),
                AggregationRule(
                    metric_type="efficiency",
                    aggregation_method=AggregationMethod.AVERAGE,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Intensity ratios - weighted average
            "intensity": [
                AggregationRule(
                    metric_type="intensity",
                    aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                    weight_field="production_volume",
                ),
                AggregationRule(
                    metric_type="intensity",
                    aggregation_method=AggregationMethod.AVERAGE,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Percentages - weighted average
            "percentage": [
                AggregationRule(
                    metric_type="percentage",
                    aggregation_method=AggregationMethod.WEIGHTED_AVERAGE,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                    weight_field="total_count",
                ),
                AggregationRule(
                    metric_type="percentage",
                    aggregation_method=AggregationMethod.AVERAGE,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
            # Counts - summed
            "count": [
                AggregationRule(
                    metric_type="count",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                ),
                AggregationRule(
                    metric_type="count",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
        }

        # Return rules for the metric type, or default rules
        return metric_rules.get(
            metric_type,
            [
                AggregationRule(
                    metric_type="default",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.ORGANIZATIONAL,
                    source_level=-1,
                    target_level=-1,
                ),
                AggregationRule(
                    metric_type="default",
                    aggregation_method=AggregationMethod.SUM,
                    hierarchy_type=HierarchyType.TEMPORAL,
                    source_level=0,
                    target_level=1,
                ),
            ],
        )

    def apply_organizational_aggregation(
        self,
        values: Dict[str, Union[int, float, Decimal]],
        hierarchy: Dict[str, HierarchyNode],
        metric_type: str,
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Apply organizational aggregation for a specific metric type."""
        rules = self.get_aggregation_rules_for_metric(metric_type)
        org_rules = [
            r for r in rules if r.hierarchy_type == HierarchyType.ORGANIZATIONAL
        ]

        return self.organizational_aggregator.aggregate_up_hierarchy(
            values, hierarchy, org_rules, context
        )

    def apply_temporal_aggregation(
        self,
        values: Dict[date, Union[int, float, Decimal]],
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
        metric_type: str,
        context: CalculationContext,
    ) -> Dict[str, CalculationResult]:
        """Apply temporal aggregation for a specific metric type."""
        rules = self.get_aggregation_rules_for_metric(metric_type)
        temporal_rules = [
            r for r in rules if r.hierarchy_type == HierarchyType.TEMPORAL
        ]

        return self.temporal_aggregator.aggregate_temporal_periods(
            values, source_granularity, target_granularity, temporal_rules, context
        )
