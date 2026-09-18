"""Metric-specific aggregation rules for sustainability indicators."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import structlog

from .aggregators import (
    CrossHierarchyAggregator,
    OrganizationalAggregator,
    TemporalAggregator,
)
from .engine import (
    AggregationMethod,
    CalculationContext,
    CalculationEngine,
    CalculationError,
    CalculationResult,
    TemporalGranularity,
)

logger = structlog.get_logger(__name__)


class MetricType(Enum):
    """Types of sustainability metrics with different aggregation rules."""

    CONSUMPTION = "consumption"  # Water, energy consumption (SUM)
    EMISSION = "emission"  # CO2, pollutants (SUM)
    INTENSITY = "intensity"  # Per unit ratios (WEIGHTED_AVERAGE)
    EFFICIENCY = "efficiency"  # Efficiency ratios (WEIGHTED_AVERAGE)
    PERCENTAGE = "percentage"  # Percentages (WEIGHTED_AVERAGE)
    COUNT = "count"  # Number of incidents, employees (SUM)
    RATE = "rate"  # Accident rates, turnover rates (WEIGHTED_AVERAGE)
    MAXIMUM = "maximum"  # Peak values, maximum capacity (MAX)
    MINIMUM = "minimum"  # Minimum thresholds (MIN)
    BINARY = "binary"  # Yes/No, compliance flags (FIRST for temporal, SUM for organizational)


@dataclass
class MetricAggregationRule:
    """Aggregation rule for a specific metric type."""

    metric_type: MetricType
    temporal_method: AggregationMethod
    organizational_method: AggregationMethod
    requires_weights: bool = False
    weight_variable: Optional[str] = (
        None  # Variable to use for weights (e.g., "production_volume")
    )
    description: str = ""


class MetricAggregationRules:
    """Manages aggregation rules for different types of sustainability metrics."""

    def __init__(self, calculation_engine: CalculationEngine):
        """Initialize metric aggregation rules.

        Args:
            calculation_engine: Reference to CalculationEngine instance
        """
        self.engine = calculation_engine
        self.temporal_aggregator = TemporalAggregator(calculation_engine)
        self.organizational_aggregator = OrganizationalAggregator(calculation_engine)
        self.cross_aggregator = CrossHierarchyAggregator(calculation_engine)
        self.logger = logger.bind(component="MetricAggregationRules")

        # Define default aggregation rules for each metric type
        self.rules = {
            MetricType.CONSUMPTION: MetricAggregationRule(
                metric_type=MetricType.CONSUMPTION,
                temporal_method=AggregationMethod.SUM,
                organizational_method=AggregationMethod.SUM,
                description="Water, energy, material consumption - sum across time and entities",
            ),
            MetricType.EMISSION: MetricAggregationRule(
                metric_type=MetricType.EMISSION,
                temporal_method=AggregationMethod.SUM,
                organizational_method=AggregationMethod.SUM,
                description="CO2, pollutant emissions - sum across time and entities",
            ),
            MetricType.INTENSITY: MetricAggregationRule(
                metric_type=MetricType.INTENSITY,
                temporal_method=AggregationMethod.WEIGHTED_AVERAGE,
                organizational_method=AggregationMethod.WEIGHTED_AVERAGE,
                requires_weights=True,
                weight_variable="production_volume",
                description="Per-unit intensities - weighted average by production volume",
            ),
            MetricType.EFFICIENCY: MetricAggregationRule(
                metric_type=MetricType.EFFICIENCY,
                temporal_method=AggregationMethod.WEIGHTED_AVERAGE,
                organizational_method=AggregationMethod.WEIGHTED_AVERAGE,
                requires_weights=True,
                weight_variable="capacity",
                description="Efficiency ratios - weighted average by capacity",
            ),
            MetricType.PERCENTAGE: MetricAggregationRule(
                metric_type=MetricType.PERCENTAGE,
                temporal_method=AggregationMethod.WEIGHTED_AVERAGE,
                organizational_method=AggregationMethod.WEIGHTED_AVERAGE,
                requires_weights=True,
                weight_variable="total_base",
                description="Percentages - weighted average by base value",
            ),
            MetricType.COUNT: MetricAggregationRule(
                metric_type=MetricType.COUNT,
                temporal_method=AggregationMethod.SUM,
                organizational_method=AggregationMethod.SUM,
                description="Counts (incidents, employees) - sum across time and entities",
            ),
            MetricType.RATE: MetricAggregationRule(
                metric_type=MetricType.RATE,
                temporal_method=AggregationMethod.WEIGHTED_AVERAGE,
                organizational_method=AggregationMethod.WEIGHTED_AVERAGE,
                requires_weights=True,
                weight_variable="exposure_hours",
                description="Rates (accident, turnover) - weighted average by exposure",
            ),
            MetricType.MAXIMUM: MetricAggregationRule(
                metric_type=MetricType.MAXIMUM,
                temporal_method=AggregationMethod.MAX,
                organizational_method=AggregationMethod.MAX,
                description="Maximum values - take maximum across time and entities",
            ),
            MetricType.MINIMUM: MetricAggregationRule(
                metric_type=MetricType.MINIMUM,
                temporal_method=AggregationMethod.MIN,
                organizational_method=AggregationMethod.MIN,
                description="Minimum values - take minimum across time and entities",
            ),
            MetricType.BINARY: MetricAggregationRule(
                metric_type=MetricType.BINARY,
                temporal_method=AggregationMethod.FIRST,  # Take first value in period
                organizational_method=AggregationMethod.SUM,  # Sum for compliance counting
                description="Binary flags - first for temporal, sum for organizational",
            ),
        }

    def get_rule(self, metric_type: MetricType) -> MetricAggregationRule:
        """Get aggregation rule for a metric type.

        Args:
            metric_type: Type of metric

        Returns:
            Aggregation rule for the metric type

        Raises:
            CalculationError: If metric type is not supported
        """
        if metric_type not in self.rules:
            raise CalculationError(f"Unsupported metric type: {metric_type}")

        return self.rules[metric_type]

    def infer_metric_type(
        self, indicator_uri: str, unit: Optional[str] = None
    ) -> MetricType:
        """Infer metric type from indicator URI and unit.

        Args:
            indicator_uri: URI of the indicator
            unit: Unit of measurement (optional)

        Returns:
            Inferred metric type
        """
        uri_lower = indicator_uri.lower()
        unit_lower = unit.lower() if unit else ""

        # Check unit first for specific patterns
        if unit_lower:
            # Percentage indicators by unit
            if "%" in unit_lower or "percent" in unit_lower:
                return MetricType.PERCENTAGE

            # Intensity indicators by unit (per something)
            if any(pattern in unit_lower for pattern in ["/", "per_", "_per_"]):
                return MetricType.INTENSITY

        # Intensity indicators (per unit) - check before consumption
        if any(
            keyword in uri_lower
            for keyword in ["intensity", "per_unit", "per_tonne", "per_kwh"]
        ):
            return MetricType.INTENSITY

        # Binary indicators - check early
        if any(
            keyword in uri_lower
            for keyword in ["compliance", "certified", "flag", "binary"]
        ):
            return MetricType.BINARY

        # Count indicators - check before consumption
        if any(
            keyword in uri_lower
            for keyword in ["count", "number", "incidents", "employees", "accident"]
        ):
            return MetricType.COUNT

        # Percentage indicators by URI
        if (
            any(keyword in uri_lower for keyword in ["percent", "ratio"])
            and "efficiency" not in uri_lower
        ):
            return MetricType.PERCENTAGE

        # Rate indicators
        if any(
            keyword in uri_lower
            for keyword in ["rate", "frequency", "per_hour", "per_year"]
        ):
            return MetricType.RATE

        # Maximum indicators
        if any(keyword in uri_lower for keyword in ["max", "peak", "capacity"]):
            return MetricType.MAXIMUM

        # Minimum indicators
        if any(keyword in uri_lower for keyword in ["min", "threshold", "limit"]):
            return MetricType.MINIMUM

        # Efficiency indicators - check after percentage to avoid conflicts
        if any(
            keyword in uri_lower for keyword in ["efficiency", "utilization", "yield"]
        ):
            return MetricType.EFFICIENCY

        # Emission indicators
        if any(
            keyword in uri_lower
            for keyword in ["co2", "emission", "ghg", "carbon", "pollutant"]
        ):
            return MetricType.EMISSION

        # Consumption indicators - check last as it's most general
        if any(
            keyword in uri_lower
            for keyword in ["water", "energy", "fuel", "material", "consumption"]
        ):
            return MetricType.CONSUMPTION

        # Default to consumption for unknown types
        self.logger.warning(
            "Could not infer metric type, defaulting to CONSUMPTION",
            indicator_uri=indicator_uri,
            unit=unit,
        )
        return MetricType.CONSUMPTION

    def aggregate_with_rules(
        self,
        values: Dict[Any, Union[int, float, Decimal]],
        metric_type: MetricType,
        aggregation_type: str,  # "temporal" or "organizational"
        weights: Optional[Dict[Any, Union[int, float, Decimal]]] = None,
        context: Optional[CalculationContext] = None,
        **kwargs,
    ) -> CalculationResult:
        """Aggregate values using metric-specific rules.

        Args:
            values: Values to aggregate (keyed by date for temporal, entity for organizational)
            metric_type: Type of metric
            aggregation_type: "temporal" or "organizational"
            weights: Optional weights for weighted aggregation
            context: Calculation context
            **kwargs: Additional arguments for specific aggregation types

        Returns:
            Aggregated result following metric-specific rules

        Raises:
            CalculationError: If aggregation fails
        """
        try:
            rule = self.get_rule(metric_type)

            # Select appropriate aggregation method
            if aggregation_type == "temporal":
                method = rule.temporal_method
            elif aggregation_type == "organizational":
                method = rule.organizational_method
            else:
                raise CalculationError(f"Invalid aggregation type: {aggregation_type}")

            self.logger.info(
                "Applying metric-specific aggregation",
                metric_type=metric_type.value,
                aggregation_type=aggregation_type,
                method=method.value,
                values_count=len(values),
            )

            # Prepare weights if required
            aggregation_weights = None
            if rule.requires_weights:
                if weights:
                    # Use provided weights
                    aggregation_weights = list(weights.values())
                else:
                    # Log warning but continue with equal weights
                    self.logger.warning(
                        "Metric requires weights but none provided, using equal weights",
                        metric_type=metric_type.value,
                        weight_variable=rule.weight_variable,
                    )
                    aggregation_weights = [1.0] * len(values)

            # Perform aggregation
            result = self.engine.aggregate_values(
                list(values.values()),
                method,
                weights=aggregation_weights,
                context=context,
            )

            # Add metric rule information to trace
            result.trace.aggregations_applied.append(
                f"metric_rule_{metric_type.value}_{aggregation_type}_{method.value}"
            )

            if rule.requires_weights and weights:
                result.trace.aggregations_applied.append(
                    f"weighted_by_{rule.weight_variable or 'provided_weights'}"
                )

            self.logger.info(
                "Metric-specific aggregation completed",
                metric_type=metric_type.value,
                result_value=str(result.value),
                method=method.value,
            )

            return result

        except Exception as e:
            self.logger.error(
                "Metric-specific aggregation failed",
                error=str(e),
                metric_type=metric_type.value,
                aggregation_type=aggregation_type,
            )
            raise CalculationError(f"Metric aggregation failed: {e}")

    def aggregate_temporal_with_rules(
        self,
        values: Dict[date, Union[int, float, Decimal]],
        metric_type: MetricType,
        source_granularity: TemporalGranularity,
        target_granularity: TemporalGranularity,
        weights: Optional[Dict[date, Union[int, float, Decimal]]] = None,
        context: Optional[CalculationContext] = None,
    ) -> Dict[str, CalculationResult]:
        """Aggregate temporal values using metric-specific rules.

        Args:
            values: Dictionary mapping dates to values
            metric_type: Type of metric
            source_granularity: Source temporal granularity
            target_granularity: Target temporal granularity
            weights: Optional weights for weighted aggregation
            context: Calculation context

        Returns:
            Dictionary mapping period identifiers to aggregated results
        """
        try:
            rule = self.get_rule(metric_type)

            self.logger.info(
                "Starting temporal aggregation with metric rules",
                metric_type=metric_type.value,
                source_granularity=source_granularity.value,
                target_granularity=target_granularity.value,
                method=rule.temporal_method.value,
            )

            # Use temporal aggregator with metric-specific method
            results = self.temporal_aggregator.aggregate_temporal_values(
                values,
                source_granularity,
                target_granularity,
                rule.temporal_method,
                context,
            )

            # Add metric rule information to all results
            for result in results.values():
                result.trace.aggregations_applied.append(
                    f"temporal_metric_rule_{metric_type.value}_{rule.temporal_method.value}"
                )

                if rule.requires_weights:
                    if weights:
                        result.trace.aggregations_applied.append(
                            f"weighted_by_{rule.weight_variable or 'provided_weights'}"
                        )
                    else:
                        result.trace.aggregations_applied.append("equal_weights_used")

            return results

        except Exception as e:
            self.logger.error(
                "Temporal aggregation with rules failed",
                error=str(e),
                metric_type=metric_type.value,
            )
            raise CalculationError(f"Temporal aggregation with rules failed: {e}")

    def aggregate_organizational_with_rules(
        self,
        entity_values: Dict[str, Union[int, float, Decimal]],
        metric_type: MetricType,
        hierarchy_config: Dict[str, Any],
        weights: Optional[Dict[str, Union[int, float, Decimal]]] = None,
        context: Optional[CalculationContext] = None,
    ) -> Dict[str, CalculationResult]:
        """Aggregate organizational values using metric-specific rules.

        Args:
            entity_values: Dictionary mapping entity IDs to values
            metric_type: Type of metric
            hierarchy_config: Organizational hierarchy configuration
            weights: Optional weights for weighted aggregation
            context: Calculation context

        Returns:
            Dictionary mapping entity IDs to aggregated results
        """
        try:
            rule = self.get_rule(metric_type)

            self.logger.info(
                "Starting organizational aggregation with metric rules",
                metric_type=metric_type.value,
                method=rule.organizational_method.value,
                entities_count=len(entity_values),
            )

            # Use organizational aggregator with metric-specific method
            results = self.organizational_aggregator.aggregate_organizational_values(
                entity_values, hierarchy_config, rule.organizational_method, context
            )

            # Add metric rule information to all results
            for result in results.values():
                result.trace.aggregations_applied.append(
                    f"organizational_metric_rule_{metric_type.value}_{rule.organizational_method.value}"
                )

                if rule.requires_weights:
                    if weights:
                        result.trace.aggregations_applied.append(
                            f"weighted_by_{rule.weight_variable or 'provided_weights'}"
                        )
                    else:
                        result.trace.aggregations_applied.append("equal_weights_used")

            return results

        except Exception as e:
            self.logger.error(
                "Organizational aggregation with rules failed",
                error=str(e),
                metric_type=metric_type.value,
            )
            raise CalculationError(f"Organizational aggregation with rules failed: {e}")

    def aggregate_cross_hierarchy_with_rules(
        self,
        values: Dict[tuple, Union[int, float, Decimal]],
        metric_type: MetricType,
        hierarchy_config: Dict[str, Any],
        target_entity: str,
        target_granularity: TemporalGranularity,
        source_granularity: TemporalGranularity = TemporalGranularity.MONTHLY,
        weights: Optional[Dict[tuple, Union[int, float, Decimal]]] = None,
        context: Optional[CalculationContext] = None,
    ) -> Dict[str, CalculationResult]:
        """Aggregate across both hierarchies using metric-specific rules.

        Args:
            values: Dictionary mapping (entity_id, date) tuples to values
            metric_type: Type of metric
            hierarchy_config: Organizational hierarchy configuration
            target_entity: Target entity for organizational aggregation
            target_granularity: Target temporal granularity
            source_granularity: Source temporal granularity
            weights: Optional weights for weighted aggregation
            context: Calculation context

        Returns:
            Dictionary mapping period identifiers to aggregated results
        """
        try:
            rule = self.get_rule(metric_type)

            self.logger.info(
                "Starting cross-hierarchy aggregation with metric rules",
                metric_type=metric_type.value,
                temporal_method=rule.temporal_method.value,
                organizational_method=rule.organizational_method.value,
                target_entity=target_entity,
            )

            # For cross-hierarchy, we need to use the same method for both dimensions
            # Use the temporal method as primary, but log if they differ
            if rule.temporal_method != rule.organizational_method:
                self.logger.warning(
                    "Different methods for temporal and organizational aggregation",
                    metric_type=metric_type.value,
                    temporal_method=rule.temporal_method.value,
                    organizational_method=rule.organizational_method.value,
                    using_method=rule.temporal_method.value,
                )

            # Use cross-hierarchy aggregator with metric-specific method
            results = self.cross_aggregator.aggregate_cross_hierarchy(
                values,
                hierarchy_config,
                target_entity,
                target_granularity,
                source_granularity,
                rule.temporal_method,  # Use temporal method for consistency
                context,
            )

            # Add metric rule information to all results
            for result in results.values():
                result.trace.aggregations_applied.append(
                    f"cross_hierarchy_metric_rule_{metric_type.value}_{rule.temporal_method.value}"
                )

                if rule.requires_weights:
                    if weights:
                        result.trace.aggregations_applied.append(
                            f"weighted_by_{rule.weight_variable or 'provided_weights'}"
                        )
                    else:
                        result.trace.aggregations_applied.append("equal_weights_used")

            return results

        except Exception as e:
            self.logger.error(
                "Cross-hierarchy aggregation with rules failed",
                error=str(e),
                metric_type=metric_type.value,
            )
            raise CalculationError(
                f"Cross-hierarchy aggregation with rules failed: {e}"
            )

    def add_custom_rule(self, metric_type: MetricType, rule: MetricAggregationRule):
        """Add or update a custom aggregation rule.

        Args:
            metric_type: Type of metric
            rule: Aggregation rule to add/update
        """
        self.rules[metric_type] = rule
        self.logger.info(
            "Custom aggregation rule added",
            metric_type=metric_type.value,
            temporal_method=rule.temporal_method.value,
            organizational_method=rule.organizational_method.value,
        )

    def get_supported_metric_types(self) -> List[MetricType]:
        """Get list of supported metric types.

        Returns:
            List of supported metric types
        """
        return list(self.rules.keys())

    def get_rule_description(self, metric_type: MetricType) -> str:
        """Get description of aggregation rule for a metric type.

        Args:
            metric_type: Type of metric

        Returns:
            Description of the aggregation rule
        """
        rule = self.get_rule(metric_type)
        return rule.description
