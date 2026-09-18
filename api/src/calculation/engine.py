"""Calculation engine for sustainability indicators and ESG metrics."""

import ast
import math
import operator
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from rdflib import RDF, Graph, Namespace, URIRef

import structlog
from src.calculation.aggregation_policy import aggregate_observations, resolve_entities
from src.calculation.contracts import (
    CalculationContract,
    ContractExecutionError,
    ContractResolutionError,
    DerivedCalculationNode,
)
from src.calculation.conversion import ConversionDependencyError, ConversionRequest
from src.calculation.semantic_formula import (
    build_infix_sum_expression,
    compact_reference,
    display_unit_from_reference,
    local_name,
    normalize_formula_expression,
)
from src.concept_runtime_aliases import concept_uri_candidates
from src.ontology.curie import DEFAULT_NAMESPACES
from src.ontology.local_graph import load_ontology_graph

logger = structlog.get_logger(__name__)

SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
_WEIGHTED_AVERAGE_FORMULA = re.compile(
    r"^\s*weighted_average\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$",
    re.IGNORECASE,
)
MAX_FORMULA_EXPONENT = Decimal("12")
MAX_FORMULA_ABS_RESULT = Decimal("1E100")


def _provider_mapping_events(observation_provider: Any) -> List[Dict[str, Any]]:
    if not hasattr(observation_provider, "get_mapping_events"):
        return []
    return [
        dict(event)
        for event in observation_provider.get_mapping_events()
        if isinstance(event, dict)
    ]


def _policy_label(contract_input) -> str:
    return (
        f"temporal={contract_input.temporal_aggregation};"
        f"perimeter={contract_input.perimeter_aggregation};"
        f"entities={contract_input.entity_scope}"
    )


class CalculationError(Exception):
    """Exception raised for calculation errors."""

    pass


class AggregationMethod(Enum):
    """Supported aggregation methods."""

    SUM = "SUM"
    AVERAGE = "AVERAGE"
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"
    FIRST = "FIRST"
    LAST = "LAST"


class TemporalGranularity(Enum):
    """Temporal granularity levels."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMESTRAL = "semestral"
    ANNUAL = "annual"


@dataclass
class CalculationContext:
    """Context for calculation execution."""

    entity_id: str
    period_start: date
    period_end: date
    temporal_granularity: TemporalGranularity
    organizational_level: int
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class VariableDependency:
    """Represents a variable dependency in calculation."""

    variable_uri: str
    entity_id: str
    period: str
    temporal_granularity: TemporalGranularity
    aggregation_method: AggregationMethod = AggregationMethod.SUM
    weight: Optional[float] = None


@dataclass
class CalculationTrace:
    """Trace information for calculation transparency, debugging, and auditing."""

    calculation_id: str = ""
    source_variables: List[Any] = field(default_factory=list)
    aggregations_applied: List[str] = field(default_factory=list)
    conversions_applied: List[Dict[str, Any]] = field(default_factory=list)
    formula_steps: List[str] = field(default_factory=list)
    dependencies_resolved: List[str] = field(default_factory=list)
    execution_time_ms: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    contract_id: Optional[str] = None
    contract_version: Optional[str] = None
    contract_hash: Optional[str] = None
    resolver_source: Optional[str] = None
    source_value_ids: List[str] = field(default_factory=list)
    aggregation_policy: Dict[str, str] = field(default_factory=dict)
    hierarchy_config_id: Optional[str] = None
    completeness: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class CalculationResult:
    """Result of a calculation."""

    value: Union[Decimal, float, int, str, bool]
    unit: Optional[str]
    context: CalculationContext
    formula_used: Optional[str]
    input_values: Dict[str, Any]
    calculation_timestamp: datetime
    trace: CalculationTrace = field(default_factory=CalculationTrace)
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    currency: Optional[str] = None


class CalculationEngine:
    """Engine for calculating sustainability indicators and ESG metrics."""

    def __init__(self, precision: int = 4, timeout: int = 30):
        """Initialize calculation engine.

        Args:
            precision: Decimal precision for calculations
            timeout: Timeout for calculations in seconds
        """
        self.precision = precision
        self.timeout = timeout
        self.logger = logger.bind(component="CalculationEngine")

        # Safe operators for formula evaluation
        self.safe_operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.Mod: operator.mod,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }

        # Safe functions for formula evaluation
        self.safe_functions = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": self._safe_sum,
            "len": len,
            "sqrt": math.sqrt,
            "pow": pow,
            "weighted_average": self._safe_weighted_average,
            "log": math.log,
            "log10": math.log10,
            "exp": math.exp,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
        }

        # Aggregation method implementations
        self.aggregation_methods = {
            AggregationMethod.SUM: self._aggregate_sum,
            AggregationMethod.AVERAGE: self._aggregate_average,
            AggregationMethod.WEIGHTED_AVERAGE: self._aggregate_weighted_average,
            AggregationMethod.MIN: self._aggregate_min,
            AggregationMethod.MAX: self._aggregate_max,
            AggregationMethod.COUNT: self._aggregate_count,
            AggregationMethod.FIRST: self._aggregate_first,
            AggregationMethod.LAST: self._aggregate_last,
        }

        # Cache for resolved dependencies
        self._dependency_cache: Dict[str, List[VariableDependency]] = {}

        # Cache for calculated values
        self._calculation_cache: Dict[str, CalculationResult] = {}

    @staticmethod
    def _safe_sum(*values):
        if len(values) == 1:
            value = values[0]
            if isinstance(value, (list, tuple, set)):
                if not all(
                    isinstance(item, (int, float, Decimal))
                    and not isinstance(item, bool)
                    for item in value
                ):
                    raise CalculationError("sum() expects numeric values")
                return sum(value)
            if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
                raise CalculationError("sum() expects numeric values")
            return value
        if not all(
            isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
            for value in values
        ):
            raise CalculationError("sum() expects numeric values")
        return sum(values)

    @staticmethod
    def _safe_weighted_average(*_values):
        raise CalculationError("weighted_average requires contract aggregation context")

    @staticmethod
    def _get_ontology_graph(ontology_graph: Optional[Graph]) -> Graph:
        if ontology_graph is not None:
            return ontology_graph
        graph, _path = load_ontology_graph()
        return graph

    @staticmethod
    def _temporal_granularity_from_ontology(
        graph: Graph, variable_iri: URIRef
    ) -> Optional[TemporalGranularity]:
        mapping = {
            "daily": TemporalGranularity.DAILY,
            "weekly": TemporalGranularity.WEEKLY,
            "monthly": TemporalGranularity.MONTHLY,
            "quarterly": TemporalGranularity.QUARTERLY,
            "semestral": TemporalGranularity.SEMESTRAL,
            "annual": TemporalGranularity.ANNUAL,
        }

        for tg in graph.objects(variable_iri, SDS.hasTemporalGranularity):
            tg_str = str(tg)
            local = tg_str.split("#")[-1].split("/")[-1]
            if tg_str.startswith(str(SDS)):
                local = tg_str[len(str(SDS)) :]
            choice = mapping.get(local.lower())
            if choice:
                return choice
        return None

    @staticmethod
    def _indicator_iri_candidates(indicator_uri: str) -> List[URIRef]:
        """Return runtime ontology lookup candidates for public and legacy IDs."""
        candidates = concept_uri_candidates(indicator_uri) or [indicator_uri]
        iris: list[URIRef] = []
        seen: set[str] = set()
        for candidate in candidates:
            expanded = DEFAULT_NAMESPACES.expand(candidate)
            if expanded not in seen:
                seen.add(expanded)
                iris.append(URIRef(expanded))
        return iris

    def resolve_dependencies(
        self,
        indicator_uri: str,
        context: CalculationContext,
        ontology_graph: Optional[Any] = None,
    ) -> List[VariableDependency]:
        """Resolve dependencies for an indicator calculation.

        Args:
            indicator_uri: URI of the indicator to calculate
            context: Calculation context
            ontology_graph: Extended ontology graph (optional)

        Returns:
            List of variable dependencies needed for calculation

        Raises:
            CalculationError: If dependencies cannot be resolved
        """
        try:
            cache_key = f"{indicator_uri}_{context.entity_id}_{context.period_start}_{context.period_end}"

            if cache_key in self._dependency_cache:
                self.logger.debug("Using cached dependencies", indicator=indicator_uri)
                return self._dependency_cache[cache_key]

            self.logger.info(
                "Resolving dependencies",
                indicator=indicator_uri,
                entity=context.entity_id,
                period=f"{context.period_start} to {context.period_end}",
            )

            graph = self._get_ontology_graph(
                ontology_graph if isinstance(ontology_graph, Graph) else None
            )

            dependencies: List[VariableDependency] = []
            for indicator_iri in self._indicator_iri_candidates(indicator_uri):
                for var_iri in graph.objects(indicator_iri, SDS.hasVariable):
                    var_ref = URIRef(str(var_iri))
                    var_curie = DEFAULT_NAMESPACES.compact(str(var_ref))

                    dep_granularity = (
                        self._temporal_granularity_from_ontology(graph, var_ref)
                        or context.temporal_granularity
                    )
                    dependencies.append(
                        VariableDependency(
                            variable_uri=var_curie,
                            entity_id=context.entity_id,
                            period=self._format_period(
                                context.period_start, dep_granularity
                            ),
                            temporal_granularity=dep_granularity,
                            aggregation_method=AggregationMethod.SUM,
                        )
                    )
                if dependencies:
                    break

            # Cache the result (bounded FIFO eviction)
            if len(self._dependency_cache) >= 1000:
                oldest_key = next(iter(self._dependency_cache))
                del self._dependency_cache[oldest_key]
            self._dependency_cache[cache_key] = dependencies

            self.logger.info(
                "Dependencies resolved",
                indicator=indicator_uri,
                dependencies_count=len(dependencies),
            )

            return dependencies

        except Exception as e:
            self.logger.error(
                "Failed to resolve dependencies", error=str(e), indicator=indicator_uri
            )
            raise CalculationError(f"Dependency resolution failed: {e}")

    def _resolve_generic_dependencies(
        self, indicator_uri: str, context: CalculationContext
    ) -> List[VariableDependency]:
        """Resolve dependencies for generic indicators."""
        # This would typically query the ontology graph
        # For now, return empty list for unknown indicators
        return []

    def _format_period(
        self, period_date: date, granularity: TemporalGranularity
    ) -> str:
        """Format period according to granularity."""
        if granularity == TemporalGranularity.DAILY:
            return period_date.strftime("%Y-%m-%d")
        elif granularity == TemporalGranularity.MONTHLY:
            return period_date.strftime("%Y-%m")
        elif granularity == TemporalGranularity.QUARTERLY:
            quarter = (period_date.month - 1) // 3 + 1
            return f"{period_date.year}-Q{quarter}"
        elif granularity == TemporalGranularity.SEMESTRAL:
            semester = 1 if period_date.month <= 6 else 2
            return f"{period_date.year}-S{semester}"
        elif granularity == TemporalGranularity.ANNUAL:
            return str(period_date.year)
        else:
            return period_date.strftime("%Y-%m-%d")

    def execute_formula(
        self,
        formula: str,
        variables: Dict[str, Any],
        context: CalculationContext,
        unit: Optional[str] = None,
    ) -> CalculationResult:
        """Execute a calculation formula with variables.

        Args:
            formula: Mathematical formula as string
            variables: Dictionary of variable names and values
            context: Calculation context
            unit: Unit of the result

        Returns:
            CalculationResult with the computed value

        Raises:
            CalculationError: If formula execution fails
        """
        try:
            start_time = datetime.now()

            self.logger.info(
                "Executing formula",
                formula=formula,
                entity_id=context.entity_id,
                variables_count=len(variables),
            )

            # Create calculation trace
            trace = CalculationTrace()
            trace.formula_steps.append(f"Original formula: {formula}")

            # Validate inputs
            self._validate_formula(formula)
            self._validate_variables(variables)
            trace.formula_steps.append("Formula and variables validated")

            # Evaluate formula
            result_value = self._evaluate_formula(formula, variables)
            result_value = self._validate_formula_result(result_value)
            trace.formula_steps.append(f"Formula evaluated to: {result_value}")

            # Apply precision
            if isinstance(result_value, (int, float)):
                result_value = Decimal(str(result_value)).quantize(
                    Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
                )
                trace.formula_steps.append(f"Precision applied: {result_value}")

            # Calculate execution time
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            trace.execution_time_ms = execution_time

            # Create result
            result = CalculationResult(
                value=result_value,
                unit=unit,
                context=context,
                formula_used=formula,
                input_values=variables.copy(),
                calculation_timestamp=datetime.now(),
                trace=trace,
            )

            self.logger.info(
                "Formula execution completed",
                result_value=str(result_value),
                unit=unit,
                execution_time_ms=execution_time,
            )

            return result

        except Exception as e:
            self.logger.error(
                "Formula execution failed",
                error=str(e),
                formula=formula,
                entity_id=context.entity_id,
            )
            raise CalculationError(f"Formula execution failed: {e}")

    def generate_calculation_trace(
        self,
        indicator_uri: str,
        dependencies: List[VariableDependency],
        result: CalculationResult,
    ) -> CalculationTrace:
        """Generate detailed calculation trace for transparency.

        Args:
            indicator_uri: URI of calculated indicator
            dependencies: List of variable dependencies used
            result: Calculation result

        Returns:
            Detailed calculation trace
        """
        try:
            trace = CalculationTrace()

            # Add source variables
            trace.source_variables = dependencies.copy()
            trace.dependencies_resolved = [dep.variable_uri for dep in dependencies]
            trace.metadata = {"input_values": result.input_values.copy()}

            # Add aggregation information
            temporal_aggregations = set()
            organizational_aggregations = set()

            for dep in dependencies:
                if dep.temporal_granularity != result.context.temporal_granularity:
                    temporal_aggregations.add(
                        f"{dep.temporal_granularity.value} -> {result.context.temporal_granularity.value}"
                    )

                if dep.entity_id != result.context.entity_id:
                    organizational_aggregations.add(
                        f"{dep.entity_id} -> {result.context.entity_id}"
                    )

            trace.aggregations_applied.extend(list(temporal_aggregations))
            trace.aggregations_applied.extend(list(organizational_aggregations))

            # Add formula steps from result trace if available
            if hasattr(result, "trace") and result.trace:
                trace.formula_steps.extend(result.trace.formula_steps)
                trace.conversions_applied.extend(result.trace.conversions_applied)
                trace.execution_time_ms = result.trace.execution_time_ms

            self.logger.info(
                "Calculation trace generated",
                indicator=indicator_uri,
                source_variables_count=len(trace.source_variables),
                aggregations_count=len(trace.aggregations_applied),
            )

            return trace

        except Exception as e:
            self.logger.error(
                "Failed to generate calculation trace",
                error=str(e),
                indicator=indicator_uri,
            )
            # Return empty trace on error
            return CalculationTrace()

    def calculate_indicator(
        self,
        indicator_uri: str,
        context: CalculationContext,
        ontology_graph: Optional[Any] = None,
        value_provider: Optional[Callable] = None,
    ) -> CalculationResult:
        """Calculate an indicator using ontology-defined formula and dependencies.

        Args:
            indicator_uri: URI of the indicator to calculate
            context: Calculation context
            ontology_graph: Extended ontology graph (optional)
            value_provider: Function to retrieve variable values (optional)

        Returns:
            CalculationResult with the computed indicator value

        Raises:
            CalculationError: If calculation fails
        """
        try:
            cache_key = f"{indicator_uri}_{context.entity_id}_{context.period_start}_{context.period_end}"

            if cache_key in self._calculation_cache:
                self.logger.debug(
                    "Using cached calculation result", indicator=indicator_uri
                )
                return self._calculation_cache[cache_key]

            self.logger.info(
                "Starting indicator calculation",
                indicator=indicator_uri,
                entity=context.entity_id,
                period=f"{context.period_start} to {context.period_end}",
            )

            graph = self._get_ontology_graph(
                ontology_graph if isinstance(ontology_graph, Graph) else None
            )

            # Step 1: Resolve dependencies
            dependencies = self.resolve_dependencies(indicator_uri, context, graph)
            if not dependencies:
                raise CalculationError(
                    f"No dependencies found for indicator: {indicator_uri}"
                )

            # Step 2: Get base values for dependencies
            base_values = self._get_base_values(dependencies, value_provider)

            # Step 3: Apply aggregations (temporal and organizational)
            aggregated_values = self._apply_aggregations(
                base_values, dependencies, context
            )

            # Step 4: Get formula for indicator
            formula = self._get_indicator_formula(
                indicator_uri, graph, dependencies=dependencies
            )

            unit = self._get_indicator_unit(indicator_uri, graph)

            # Step 5: Execute formula
            result = self.execute_formula(
                formula, aggregated_values, context, unit=unit
            )

            # Step 6: Generate detailed trace
            detailed_trace = self.generate_calculation_trace(
                indicator_uri, dependencies, result
            )
            result.trace = detailed_trace

            # Cache the result (bounded FIFO eviction)
            if len(self._calculation_cache) >= 1000:
                oldest_key = next(iter(self._calculation_cache))
                del self._calculation_cache[oldest_key]
            self._calculation_cache[cache_key] = result

            self.logger.info(
                "Indicator calculation completed",
                indicator=indicator_uri,
                result_value=str(result.value),
                dependencies_used=len(dependencies),
            )

            return result

        except Exception as e:
            self.logger.error(
                "Indicator calculation failed",
                error=str(e),
                indicator=indicator_uri,
                entity=context.entity_id,
            )
            raise CalculationError(f"Indicator calculation failed: {e}")

    def calculate_contract(
        self,
        contract: CalculationContract,
        context: CalculationContext,
        observation_provider,
        *,
        unit_normalizer: Optional[Any] = None,
        conversion_engine: Optional[Any] = None,
        contract_resolver: Optional[Any] = None,
        _calculation_stack: Optional[set[str]] = None,
    ) -> CalculationResult:
        """Execute an imported runtime calculation contract.

        This path is contract-first: dependencies, local names, units, and
        aggregation behavior come from the imported contract, not from RDF graph
        inference. It intentionally does not use the legacy calculation cache so
        value or contract changes cannot return stale results.
        """

        if not contract.is_executable:
            raise ContractExecutionError(
                f"Calculation contract {contract.contract_id} is not executable "
                f"(runtime_status={contract.runtime_status})"
            )

        calculation_stack = set(_calculation_stack or set())
        if contract.contract_id in calculation_stack:
            raise ContractExecutionError(
                f"Cycle detected in calculation contract dependencies at {contract.contract_id}"
            )
        calculation_stack.add(contract.contract_id)

        start_time = datetime.now()
        input_values: Dict[str, Any] = {}
        source_value_ids: List[str] = []
        conversions: List[Dict[str, Any]] = []
        aggregation_policy: Dict[str, str] = {}
        warnings: List[str] = []
        missing_required: List[str] = []
        observed_variables: List[str] = []
        inputs_by_variable: Dict[str, Any] = {}
        raw_observations_by_variable: Dict[str, List[Any]] = {}

        try:
            for contract_input in contract.inputs:
                inputs_by_variable[contract_input.local_variable] = contract_input
                entities = resolve_entities(contract_input, context, contract.hierarchy)
                observations = observation_provider.get_observations(
                    contract_input,
                    entities=entities,
                    period_start=context.period_start,
                    period_end=context.period_end,
                )
                if not observations:
                    nested_result = self._try_calculate_nested_contract(
                        contract_input,
                        context,
                        observation_provider,
                        unit_normalizer=unit_normalizer,
                        conversion_engine=conversion_engine,
                        contract_resolver=contract_resolver,
                        calculation_stack=calculation_stack,
                        current_contract_id=contract.contract_id,
                    )
                    if nested_result is not None:
                        nested_value, nested_conversion = self._normalize_nested_value(
                            nested_result,
                            contract_input,
                            unit_normalizer=unit_normalizer,
                            conversion_engine=conversion_engine,
                            parent_result_currency=contract.result_currency,
                        )
                        input_values[contract_input.local_variable] = nested_value
                        nested_trace = getattr(nested_result, "trace", None)
                        source_value_ids.extend(
                            list(getattr(nested_trace, "source_value_ids", []) or [])
                        )
                        conversions.extend(
                            list(getattr(nested_trace, "conversions_applied", []) or [])
                        )
                        if nested_conversion:
                            conversions.append(
                                {
                                    "variable": contract_input.local_variable,
                                    **nested_conversion,
                                }
                            )
                        aggregation_policy[contract_input.local_variable] = (
                            f"nested_contract={getattr(nested_trace, 'contract_id', '')};"
                            f"temporal={contract_input.temporal_aggregation};"
                            f"perimeter={contract_input.perimeter_aggregation}"
                        )
                        warnings.extend(
                            list(getattr(nested_trace, "warnings", []) or [])
                        )
                        observed_variables.append(contract_input.local_variable)
                        continue

                    if contract_input.required:
                        missing_required.append(contract_input.local_variable)
                        continue
                    if getattr(contract, "missing_data", None) == "BLOCK":
                        # Contract policy blocks on ANY missing input, even one
                        # marked required=false — never default/zero it and report
                        # complete (codex F07 M1).
                        missing_required.append(contract_input.local_variable)
                        continue
                    if contract_input.default_value is not None:
                        input_values[contract_input.local_variable] = (
                            contract_input.default_value
                        )
                        warnings.append(
                            f"{contract_input.local_variable}: default value used"
                        )
                    else:
                        input_values[contract_input.local_variable] = Decimal("0")
                        warnings.append(
                            f"{contract_input.local_variable}: optional input missing, zero used"
                        )
                    aggregation_policy[contract_input.local_variable] = _policy_label(
                        contract_input
                    )
                    continue

                target_currency = self._resolve_input_target_currency(
                    contract,
                    contract_input,
                    observations,
                )
                if self._requires_currency_conversion(target_currency, observations):
                    if conversion_engine is None:
                        raise ContractExecutionError(
                            f"{contract_input.local_variable}: conversion_engine is "
                            "required for currency conversion"
                        )

                if conversion_engine is not None:
                    (
                        observations,
                        conversion_details,
                        conversion_warnings,
                    ) = self._normalize_contract_observations(
                        contract_input,
                        observations,
                        conversion_engine=conversion_engine,
                        target_currency=target_currency,
                    )
                    conversions.extend(conversion_details)
                    warnings.extend(conversion_warnings)

                raw_observations_by_variable[contract_input.local_variable] = (
                    observations
                )
                outcome = aggregate_observations(
                    contract_input,
                    observations,
                    unit_normalizer=(
                        None if conversion_engine is not None else unit_normalizer
                    ),
                )
                input_values[contract_input.local_variable] = outcome.value
                source_value_ids.extend(outcome.source_value_ids)
                conversions.extend(outcome.conversions)
                aggregation_policy[contract_input.local_variable] = outcome.policy_label
                warnings.extend(outcome.warnings)
                observed_variables.append(contract_input.local_variable)

            if missing_required:
                raise ContractExecutionError(
                    "Missing required observations: " + ", ".join(missing_required)
                )

            all_values = input_values.copy()
            all_values.update(self._evaluate_derived_nodes(contract, all_values))

            result = self._try_execute_weighted_average_formula(
                contract,
                context,
                inputs_by_variable=inputs_by_variable,
                raw_observations_by_variable=raw_observations_by_variable,
                unit_normalizer=unit_normalizer,
                conversion_engine=conversion_engine,
            )
            if result is None:
                result = self.execute_formula(
                    contract.formula,
                    all_values,
                    context,
                    unit=contract.result_unit,
                )
            if isinstance(result.value, Decimal):
                result.value = result.value.quantize(
                    Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
                )

            input_mapping_events = _provider_mapping_events(observation_provider)
            if input_mapping_events:
                warnings.extend(
                    [
                        "input mapping used: "
                        f"{event.get('local_variable')} "
                        f"{event.get('requested_concept')} <- "
                        f"{event.get('source_concept')} "
                        f"({event.get('relationship_type')})"
                        for event in input_mapping_events
                    ]
                )

            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            trace = CalculationTrace(
                calculation_id=contract.contract_id,
                source_variables=list(contract.inputs),
                aggregations_applied=[
                    f"{name}: {policy}" for name, policy in aggregation_policy.items()
                ],
                conversions_applied=conversions,
                formula_steps=[
                    f"Contract formula: {contract.formula}",
                    *(
                        f"Derived {node.local_variable}: {node.expression}"
                        for node in contract.derived_nodes
                    ),
                    *list(getattr(result.trace, "formula_steps", []) or []),
                ],
                dependencies_resolved=[item.concept for item in contract.inputs],
                execution_time_ms=execution_time,
                metadata={
                    "input_values": input_values.copy(),
                    "all_values": all_values.copy(),
                    "input_mappings": input_mapping_events,
                },
                contract_id=contract.contract_id,
                contract_version=contract.contract_version,
                contract_hash=contract.contract_hash,
                resolver_source=contract.resolver_source,
                source_value_ids=source_value_ids,
                aggregation_policy=aggregation_policy,
                hierarchy_config_id=contract.hierarchy_config_id,
                completeness={
                    "status": "complete",
                    "observed_variables": observed_variables,
                    "missing_required": missing_required,
                    "warnings": warnings,
                },
                warnings=warnings,
            )
            result.trace = trace
            result.input_values = input_values
            result.formula_used = contract.formula
            result.currency = contract.result_currency
            return result
        except ContractExecutionError:
            raise
        except CalculationError as exc:
            raise ContractExecutionError(str(exc)) from exc
        except Exception as exc:
            raise ContractExecutionError(f"Contract calculation failed: {exc}") from exc

    def _try_calculate_nested_contract(
        self,
        contract_input,
        context: CalculationContext,
        observation_provider,
        *,
        unit_normalizer: Optional[Any],
        conversion_engine: Optional[Any],
        contract_resolver: Optional[Any],
        calculation_stack: set[str],
        current_contract_id: str,
    ) -> Optional[CalculationResult]:
        if contract_resolver is None:
            return None

        try:
            nested_contract = contract_resolver.resolve(contract_input.concept)
        except ContractResolutionError:
            return None

        if nested_contract.contract_id in calculation_stack:
            if nested_contract.contract_id == current_contract_id:
                return None
            raise ContractExecutionError(
                "Cycle detected in calculation contract dependencies: "
                + " -> ".join([*calculation_stack, nested_contract.contract_id])
            )

        return self.calculate_contract(
            nested_contract,
            context,
            observation_provider,
            unit_normalizer=unit_normalizer,
            conversion_engine=conversion_engine,
            contract_resolver=contract_resolver,
            _calculation_stack=calculation_stack,
        )

    def _normalize_contract_observations(
        self,
        contract_input: Any,
        observations: List[Any],
        *,
        conversion_engine: Any,
        target_currency: Optional[str],
    ) -> tuple[List[Any], List[Dict[str, Any]], List[str]]:
        normalized: List[Any] = []
        conversions: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for observation in observations:
            normalized_observation, observation_conversions, observation_warnings = (
                self._normalize_contract_observation_with_engine(
                    contract_input,
                    observation,
                    conversion_engine=conversion_engine,
                    target_currency=target_currency,
                )
            )
            normalized.append(normalized_observation)
            conversions.extend(observation_conversions)
            warnings.extend(observation_warnings)

        return normalized, conversions, warnings

    def _normalize_contract_observation_with_engine(
        self,
        contract_input: Any,
        observation: Any,
        *,
        conversion_engine: Any,
        target_currency: Optional[str],
    ) -> tuple[Any, List[Dict[str, Any]], List[str]]:
        self._validate_conversion_policy(contract_input)
        warnings: List[str] = []
        local_variable = str(contract_input.local_variable)
        value_id = str(getattr(observation, "value_id", ""))
        source_currency = getattr(observation, "currency", None)
        component_currency = getattr(contract_input, "currency", None)
        expected_currency = target_currency

        if expected_currency and not source_currency:
            raise ContractExecutionError(
                f"{local_variable}/{value_id}: source currency is required for FX conversion"
            )
        if (
            source_currency
            and component_currency
            and source_currency != component_currency
        ):
            warnings.append(
                f"{local_variable}/{value_id}: observation currency {source_currency} "
                f"overrides component currency hint {component_currency}"
            )

        try:
            conversion_result = conversion_engine.normalize(
                ConversionRequest(
                    value=Decimal(str(getattr(observation, "value"))),
                    unit=getattr(observation, "unit", None),
                    expected_unit=getattr(contract_input, "unit", None),
                    currency=source_currency,
                    expected_currency=expected_currency,
                    value_date=getattr(observation, "value_date", None),
                    period_start=getattr(observation, "period_start", None),
                    period_end=getattr(observation, "period_end", None),
                    fx_policy_id=getattr(contract_input, "fx_policy_id", None),
                )
            )
        except (ConversionDependencyError, ValueError) as exc:
            raise ContractExecutionError(f"{local_variable}/{value_id}: {exc}") from exc

        conversion_trace = self._conversion_trace_entries(
            local_variable=local_variable,
            value_id=value_id,
            existing_trace=getattr(observation, "conversion_trace", None),
            new_trace=getattr(conversion_result, "trace", None),
        )
        normalized_observation = replace(
            observation,
            value=Decimal(str(conversion_result.value)),
            unit=getattr(conversion_result, "unit", None)
            or getattr(observation, "unit", None),
            currency=getattr(conversion_result, "currency", None) or source_currency,
            conversion_trace=[dict(step) for step in conversion_trace]
            or getattr(observation, "conversion_trace", None),
        )
        return normalized_observation, conversion_trace, warnings

    @staticmethod
    def _resolve_input_target_currency(
        contract: CalculationContract,
        contract_input: Any,
        observations: List[Any],
    ) -> Optional[str]:
        explicit_target = getattr(contract_input, "expected_currency", None)
        if explicit_target:
            missing_source = [
                str(getattr(observation, "value_id", ""))
                for observation in observations
                if not getattr(observation, "currency", None)
            ]
            if missing_source:
                value_ids = ", ".join(missing_source)
                raise ContractExecutionError(
                    f"{contract_input.local_variable}: source currency is required "
                    f"for FX conversion ({value_ids})"
                )
            return explicit_target

        row_currencies = {
            str(getattr(observation, "currency"))
            for observation in observations
            if getattr(observation, "currency", None)
        }
        if len(row_currencies) > 1:
            currencies = ", ".join(sorted(row_currencies))
            raise ContractExecutionError(
                f"{contract_input.local_variable}: expected_currency "
                f"is required for mixed-currency observations ({currencies})"
            )

        result_currency = getattr(contract, "result_currency", None)
        if result_currency and row_currencies and result_currency not in row_currencies:
            source_currency = next(iter(row_currencies))
            raise ContractExecutionError(
                f"{contract_input.local_variable}: expected_currency and fx_policy_id "
                f"are required to convert {source_currency} observations to "
                f"result_currency {result_currency}"
            )
        return None

    @staticmethod
    def _requires_currency_conversion(
        target_currency: Optional[str],
        observations: List[Any],
    ) -> bool:
        if not target_currency:
            return False
        return any(
            getattr(observation, "currency", None) != target_currency
            for observation in observations
        )

    @staticmethod
    def _conversion_trace_entries(
        *,
        local_variable: str,
        value_id: str,
        existing_trace: Optional[List[Dict[str, Any]]],
        new_trace: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for step in list(existing_trace or []) + list(new_trace or []):
            entry = {
                "variable": local_variable,
                "value_id": value_id,
            }
            entry.update(dict(step))
            entries.append(entry)
        return entries

    @staticmethod
    def _validate_conversion_policy(contract_input: Any) -> None:
        policy = getattr(contract_input, "conversion_policy", None) or "fail_closed"
        if policy != "fail_closed":
            raise ContractExecutionError(
                f"Unsupported conversion policy for {contract_input.local_variable}: {policy}"
            )

    def _normalize_nested_value(
        self,
        nested_result: CalculationResult,
        contract_input,
        *,
        unit_normalizer: Optional[Any],
        conversion_engine: Optional[Any] = None,
        parent_result_currency: Optional[str] = None,
    ) -> tuple[Decimal, Optional[Dict[str, Any]]]:
        value = Decimal(str(nested_result.value))
        result_unit = nested_result.unit
        expected_unit = contract_input.unit
        expected_currency = getattr(contract_input, "expected_currency", None)
        source_currency = getattr(nested_result, "currency", None)
        if expected_currency and not source_currency:
            raise ContractExecutionError(
                "Nested contract source currency is required for FX conversion"
            )
        if (
            not expected_currency
            and parent_result_currency
            and source_currency
            and source_currency != parent_result_currency
        ):
            raise ContractExecutionError(
                f"Nested contract input {contract_input.local_variable}: "
                "expected_currency and fx_policy_id are required to convert "
                f"{source_currency} result to parent result_currency "
                f"{parent_result_currency}"
            )
        if (
            expected_currency
            and source_currency != expected_currency
            and conversion_engine is None
        ):
            raise ContractExecutionError(
                "Nested contract conversion_engine is required for currency conversion"
            )
        if conversion_engine is not None:
            self._validate_conversion_policy(contract_input)
            try:
                converted = conversion_engine.normalize(
                    ConversionRequest(
                        value=value,
                        unit=result_unit,
                        expected_unit=expected_unit,
                        currency=source_currency,
                        expected_currency=expected_currency,
                        fx_policy_id=getattr(contract_input, "fx_policy_id", None),
                    )
                )
            except (ConversionDependencyError, ValueError) as exc:
                raise ContractExecutionError(
                    f"Nested contract conversion failed: {exc}"
                ) from exc
            detail = {
                "nested_contract": getattr(nested_result.trace, "contract_id", None)
            }
            trace = list(getattr(converted, "trace", []) or [])
            if trace:
                detail["trace"] = trace
            return Decimal(str(converted.value)), detail if trace else None

        if not expected_unit or result_unit == expected_unit:
            return value, None
        if unit_normalizer is None:
            raise ContractExecutionError(
                f"Nested contract unit mismatch requires a unit normalizer: "
                f"{result_unit} -> {expected_unit}"
            )
        if hasattr(unit_normalizer, "normalize"):
            converted, detail = unit_normalizer.normalize(
                value, result_unit, expected_unit
            )
            return Decimal(str(converted)), dict(detail or {})
        if hasattr(unit_normalizer, "convert"):
            result = unit_normalizer.convert(value, result_unit, expected_unit)
            return Decimal(str(result.converted_value)), {
                "from_unit": result_unit,
                "to_unit": expected_unit,
                "factor": str(result.conversion_factor),
            }
        raise ContractExecutionError("Invalid unit normalizer hook")

    def _try_execute_weighted_average_formula(
        self,
        contract: CalculationContract,
        context: CalculationContext,
        *,
        inputs_by_variable: Dict[str, Any],
        raw_observations_by_variable: Dict[str, List[Any]],
        unit_normalizer: Optional[Any],
        conversion_engine: Optional[Any] = None,
    ) -> Optional[CalculationResult]:
        match = _WEIGHTED_AVERAGE_FORMULA.fullmatch(contract.formula or "")
        if match is None:
            return None

        value_variable, weight_variable = match.group(1), match.group(2)
        value_input = inputs_by_variable.get(value_variable)
        weight_input = inputs_by_variable.get(weight_variable)
        if value_input is None or weight_input is None:
            raise ContractExecutionError(
                "weighted_average requires formula variables backed by contract inputs"
            )

        value_observations = raw_observations_by_variable.get(value_variable)
        weight_observations = raw_observations_by_variable.get(weight_variable)
        if not value_observations or not weight_observations:
            raise ContractExecutionError(
                "weighted_average requires raw observations for both value and weight inputs"
            )

        values_by_key = self._normalized_observations_by_entity_period(
            value_variable,
            value_input,
            value_observations,
            unit_normalizer=None if conversion_engine is not None else unit_normalizer,
        )
        weights_by_key = self._normalized_observations_by_entity_period(
            weight_variable,
            weight_input,
            weight_observations,
            unit_normalizer=None if conversion_engine is not None else unit_normalizer,
        )

        value_keys = set(values_by_key)
        weight_keys = set(weights_by_key)
        if value_keys != weight_keys:
            raise ContractExecutionError(
                "weighted_average requires paired observations by entity and period; "
                f"missing weights for {self._format_weighted_keys(value_keys - weight_keys)}; "
                f"missing values for {self._format_weighted_keys(weight_keys - value_keys)}"
            )

        denominator = sum(weights_by_key.values(), Decimal("0"))
        if denominator == 0:
            raise ContractExecutionError("weighted_average denominator is zero")

        numerator = sum(
            values_by_key[key] * weights_by_key[key] for key in sorted(value_keys)
        )
        value = numerator / denominator
        trace = CalculationTrace(
            formula_steps=[
                f"Weighted average: {value_variable} weighted by {weight_variable}",
                f"Weighted numerator: {numerator}",
                f"Weighted denominator: {denominator}",
                f"Formula evaluated to: {value}",
            ]
        )
        return CalculationResult(
            value=value,
            unit=contract.result_unit,
            context=context,
            formula_used=contract.formula,
            input_values={},
            calculation_timestamp=datetime.now(),
            trace=trace,
        )

    def _normalized_observations_by_entity_period(
        self,
        local_variable: str,
        contract_input: Any,
        observations: List[Any],
        *,
        unit_normalizer: Optional[Any],
    ) -> Dict[Tuple[str, date], Decimal]:
        values: Dict[Tuple[str, date], Decimal] = {}
        for observation in observations:
            key = (str(observation.entity), observation.period)
            if key in values:
                raise ContractExecutionError(
                    "weighted_average requires one observation per entity-period pair "
                    f"for {local_variable}; duplicate {key[0]}/{key[1].isoformat()}"
                )
            values[key] = self._normalize_contract_observation_value(
                observation,
                contract_input,
                unit_normalizer=unit_normalizer,
            )
        return values

    def _normalize_contract_observation_value(
        self,
        observation: Any,
        contract_input: Any,
        *,
        unit_normalizer: Optional[Any],
    ) -> Decimal:
        value = Decimal(str(observation.value))
        expected_unit = contract_input.unit
        if not expected_unit or observation.unit == expected_unit:
            return value
        if unit_normalizer is None:
            raise ContractExecutionError(
                f"Mixed units require a unit normalizer: {observation.unit} -> {expected_unit}"
            )
        if hasattr(unit_normalizer, "normalize"):
            converted, _detail = unit_normalizer.normalize(
                value, observation.unit, expected_unit
            )
            return Decimal(str(converted))
        if hasattr(unit_normalizer, "convert"):
            result = unit_normalizer.convert(value, observation.unit, expected_unit)
            return Decimal(str(result.converted_value))
        raise ContractExecutionError("Invalid unit normalizer hook")

    @staticmethod
    def _format_weighted_keys(keys: set[Tuple[str, date]]) -> str:
        if not keys:
            return "none"
        return ", ".join(
            f"{entity}/{period.isoformat()}" for entity, period in sorted(keys)
        )

    def _evaluate_derived_nodes(
        self, contract: CalculationContract, values: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not contract.derived_nodes:
            return {}

        nodes_by_name = {node.local_variable: node for node in contract.derived_nodes}
        visiting: set[str] = set()
        visited: set[str] = set()
        order: List[DerivedCalculationNode] = []

        def visit(name: str) -> None:
            if name in values or name in visited:
                return
            if name in visiting:
                raise ContractExecutionError(
                    f"Cycle detected in calculation contract DAG at {name}"
                )
            node = nodes_by_name.get(name)
            if node is None:
                raise ContractExecutionError(
                    f"Unknown variable in calculation contract DAG: {name}"
                )
            visiting.add(name)
            dependencies = node.dependencies or tuple(
                self._expression_variable_names(node.expression)
            )
            for dependency in dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(node)

        for node_name in nodes_by_name:
            visit(node_name)

        derived_values: Dict[str, Any] = {}
        runtime_values = values.copy()
        for node in order:
            try:
                self._validate_formula(node.expression)
                derived_values[node.local_variable] = self._validate_formula_result(
                    self._evaluate_formula(
                        node.expression,
                        runtime_values,
                    )
                )
            except CalculationError as exc:
                raise ContractExecutionError(
                    f"Failed to evaluate derived node {node.local_variable}: {exc}"
                ) from exc
            runtime_values[node.local_variable] = derived_values[node.local_variable]
        return derived_values

    def _expression_variable_names(self, expression: str) -> List[str]:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ContractExecutionError(f"Invalid formula syntax: {exc}") from exc
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in self.safe_functions:
                names.append(node.id)
        return names

    def _get_base_values(
        self,
        dependencies: List[VariableDependency],
        value_provider: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Get base values for variable dependencies."""
        values = {}

        for dep in dependencies:
            if not value_provider:
                raise CalculationError(
                    "value_provider is required (no mock values in runtime)"
                )

            value = value_provider(dep.variable_uri, dep.entity_id, dep.period)

            # Use variable URI as key for formula
            variable_name = (
                dep.variable_uri.split(":")[-1]
                if ":" in dep.variable_uri
                else dep.variable_uri
            )
            values[variable_name] = value

        return values

    def _apply_aggregations(
        self,
        base_values: Dict[str, Any],
        dependencies: List[VariableDependency],
        context: CalculationContext,
    ) -> Dict[str, Any]:
        """Apply temporal and organizational aggregations to base values."""
        aggregated_values = base_values.copy()

        # For now, return values as-is
        # In real implementation, this would:
        # 1. Group values by temporal periods
        # 2. Apply temporal aggregation (monthly -> annual)
        # 3. Group values by organizational hierarchy
        # 4. Apply organizational aggregation (plant -> country)

        return aggregated_values

    def _get_indicator_formula(
        self,
        indicator_uri: str,
        ontology_graph: Optional[Any] = None,
        dependencies: Optional[List[VariableDependency]] = None,
    ) -> str:
        """Get calculation formula for an indicator from ontology (local graph)."""
        graph = self._get_ontology_graph(
            ontology_graph if isinstance(ontology_graph, Graph) else None
        )

        expression = None
        for indicator_iri in self._indicator_iri_candidates(indicator_uri):
            for formula_iri in graph.objects(indicator_iri, SDS.hasFormula):
                for expr in graph.objects(
                    URIRef(str(formula_iri)), SDS.calculationExpression
                ):
                    expression = str(expr).strip()
                    break
                if expression:
                    break
            if expression:
                break

        if expression:
            parsed = self._parse_formula_expression(expression)
            if parsed:
                return parsed

        # Fallback: derive a sum formula from dependencies.
        if dependencies:
            derived = build_infix_sum_expression([d.variable_uri for d in dependencies])
            if derived:
                return derived

        raise CalculationError(f"No formula found for indicator: {indicator_uri}")

    @staticmethod
    def _parse_formula_expression(expression: str) -> Optional[str]:
        return normalize_formula_expression(expression)

    def _get_indicator_unit(
        self, indicator_uri: str, ontology_graph: Optional[Graph]
    ) -> Optional[str]:
        graph = self._get_ontology_graph(ontology_graph)
        for indicator_iri in self._indicator_iri_candidates(indicator_uri):
            for unit_iri in graph.objects(indicator_iri, SDS.hasUnit):
                return display_unit_from_reference(str(unit_iri))
        return None

    def _validate_formula(self, formula: str):
        """Validate formula syntax and safety."""
        if not formula or not isinstance(formula, str):
            raise CalculationError("Formula must be a non-empty string")

        try:
            # Parse formula to AST
            tree = ast.parse(formula, mode="eval")

            # Check for unsafe operations
            self._check_ast_safety(tree)

        except SyntaxError as e:
            raise CalculationError(f"Invalid formula syntax: {e}")

    def _check_ast_safety(self, node):
        """Check AST node for unsafe operations."""
        if isinstance(node, ast.Expression):
            self._check_ast_safety(node.body)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in self.safe_operators:
                raise CalculationError(f"Unsafe operator: {type(node.op).__name__}")
            if isinstance(node.op, ast.Pow):
                self._validate_exponent_node(node.right)
            self._check_ast_safety(node.left)
            self._check_ast_safety(node.right)
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in self.safe_operators:
                raise CalculationError(
                    f"Unsafe unary operator: {type(node.op).__name__}"
                )
            self._check_ast_safety(node.operand)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in self.safe_functions:
                    raise CalculationError(f"Unsafe function: {node.func.id}")
                if node.func.id == "pow":
                    if len(node.args) != 2:
                        raise CalculationError(
                            "Unsafe exponent: pow() requires two arguments"
                        )
                    self._validate_exponent_node(node.args[1])
            else:
                raise CalculationError("Only simple function calls allowed")
            if node.keywords:
                raise CalculationError("Keyword arguments are not allowed in formulas")
            for arg in node.args:
                self._check_ast_safety(arg)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CalculationError(
                    f"Unsafe non-numeric constant: {type(node.value).__name__}"
                )
            self._validate_numeric_literal(node.value)
        elif isinstance(node, ast.Name):
            pass  # Variable names are safe
        elif isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
            raise CalculationError(f"Unsafe AST node: {type(node).__name__}")
        else:
            raise CalculationError(f"Unsafe AST node: {type(node).__name__}")

    def _validate_numeric_literal(self, value: int | float) -> Decimal:
        if isinstance(value, float) and not math.isfinite(value):
            raise CalculationError("Unsafe numeric literal: non-finite value")
        decimal_value = Decimal(str(value))
        if abs(decimal_value) > MAX_FORMULA_ABS_RESULT:
            raise CalculationError("Unsafe numeric literal exceeds supported bounds")
        return decimal_value

    def _constant_numeric_value(self, node) -> Optional[Decimal]:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                return None
            return self._validate_numeric_literal(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = self._constant_numeric_value(node.operand)
            if value is None:
                return None
            return -value if isinstance(node.op, ast.USub) else value
        return None

    def _validate_exponent_node(self, node):
        value = self._constant_numeric_value(node)
        if value is not None:
            self._validate_exponent_value(value)

    def _validate_exponent_value(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise CalculationError("Unsafe exponent: non-numeric value")
        if isinstance(value, float) and not math.isfinite(value):
            raise CalculationError("Unsafe exponent: non-finite value")
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if abs(decimal_value) > MAX_FORMULA_EXPONENT:
            raise CalculationError("Unsafe exponent exceeds supported bounds")
        return decimal_value

    def _validate_variables(self, variables: Dict[str, Any]):
        """Validate variable values."""
        if not isinstance(variables, dict):
            raise CalculationError("Variables must be a dictionary")

        for name, value in variables.items():
            if not isinstance(name, str):
                raise CalculationError(f"Variable name must be string: {name}")

            if value is None:
                raise CalculationError(f"Variable '{name}' has None value")

            if isinstance(value, (list, tuple)):
                if not all(
                    isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
                    for v in value
                ):
                    raise CalculationError(
                        f"Variable '{name}' contains non-numeric values"
                    )
            elif isinstance(value, bool) or not isinstance(
                value, (int, float, Decimal)
            ):
                raise CalculationError(f"Variable '{name}' is non-numeric")
            elif isinstance(value, float) and not math.isfinite(value):
                raise CalculationError(f"Variable '{name}' is non-finite")
            elif isinstance(value, Decimal) and not value.is_finite():
                raise CalculationError(f"Variable '{name}' is non-finite")

    def _validate_formula_result(self, result_value):
        if isinstance(result_value, bool) or not isinstance(
            result_value, (int, float, Decimal)
        ):
            raise CalculationError("Formula result must be a numeric scalar")
        if isinstance(result_value, float) and not math.isfinite(result_value):
            raise CalculationError("Formula result must be finite")
        if isinstance(result_value, Decimal) and not result_value.is_finite():
            raise CalculationError("Formula result must be finite")
        decimal_value = (
            result_value
            if isinstance(result_value, Decimal)
            else Decimal(str(result_value))
        )
        if abs(decimal_value) > MAX_FORMULA_ABS_RESULT:
            raise CalculationError("Formula result exceeds supported numeric bounds")
        return result_value

    def _evaluate_formula(self, formula: str, variables: Dict[str, Any]) -> Any:
        """Safely evaluate formula with variables."""
        try:
            # Create safe evaluation context
            safe_dict = self._formula_variable_context(variables)
            safe_dict.update(self.safe_functions)

            # Parse and evaluate
            tree = ast.parse(formula, mode="eval")
            result = self._eval_ast_node(tree.body, safe_dict)

            return result

        except (DivisionByZero, ZeroDivisionError) as e:
            raise CalculationError("Formula evaluation failed: division by zero") from e
        except Exception as e:
            raise CalculationError(f"Formula evaluation failed: {e}")

    @staticmethod
    def _formula_variable_context(variables: Dict[str, Any]) -> Dict[str, Any]:
        """Expose stored variable references under the local names formulas use."""
        context = variables.copy()
        for name, value in variables.items():
            if not isinstance(name, str):
                continue
            for alias in (compact_reference(name), local_name(name)):
                if alias and alias not in context:
                    context[alias] = value
        return context

    def _eval_ast_node(self, node, context: Dict[str, Any]) -> Any:
        """Evaluate AST node safely."""
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Name):
            if node.id in context:
                return context[node.id]
            else:
                raise CalculationError(f"Undefined variable: {node.id}")
        elif isinstance(node, ast.BinOp):
            left = self._eval_ast_node(node.left, context)
            right = self._eval_ast_node(node.right, context)
            if isinstance(node.op, ast.Pow):
                self._validate_exponent_value(right)
            op = self.safe_operators[type(node.op)]
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = self._eval_ast_node(node.operand, context)
            op = self.safe_operators[type(node.op)]
            return op(operand)
        elif isinstance(node, ast.Call):
            func_name = node.func.id
            args = [self._eval_ast_node(arg, context) for arg in node.args]
            if func_name == "pow":
                self._validate_exponent_value(args[1])
            func = context[func_name]
            return func(*args)
        elif isinstance(node, ast.List):
            return [self._eval_ast_node(item, context) for item in node.elts]
        else:
            raise CalculationError(f"Unsupported AST node: {type(node).__name__}")

    def aggregate_values(
        self,
        values: List[Union[int, float, Decimal]],
        method: AggregationMethod,
        weights: Optional[List[Union[int, float, Decimal]]] = None,
        context: Optional[CalculationContext] = None,
    ) -> CalculationResult:
        """Aggregate a list of values using specified method."""
        try:
            self.logger.info(
                "Starting value aggregation",
                method=method.value,
                values_count=len(values),
            )

            if not values:
                raise CalculationError("Cannot aggregate empty list of values")

            # Convert values to Decimal for precision
            decimal_values = [Decimal(str(v)) for v in values]

            # Apply aggregation method
            aggregation_func = self.aggregation_methods[method]
            result_value = aggregation_func(decimal_values, weights)

            # Apply precision
            if isinstance(result_value, Decimal):
                result_value = result_value.quantize(
                    Decimal("0." + "0" * self.precision), rounding=ROUND_HALF_UP
                )

            # Create result
            result = CalculationResult(
                value=result_value,
                unit=None,
                context=context
                or CalculationContext(
                    entity_id="aggregation",
                    period_start=date.today(),
                    period_end=date.today(),
                    temporal_granularity=TemporalGranularity.DAILY,
                    organizational_level=0,
                ),
                formula_used=f"{method.value}({len(values)} values)",
                input_values={"values": values, "weights": weights},
                calculation_timestamp=datetime.now(),
            )

            self.logger.info(
                "Value aggregation completed",
                method=method.value,
                result_value=str(result_value),
            )

            return result

        except Exception as e:
            self.logger.error(
                "Value aggregation failed", error=str(e), method=method.value
            )
            raise CalculationError(f"Aggregation failed: {e}")

    def _aggregate_sum(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Sum aggregation."""
        return sum(values)

    def _aggregate_average(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Average aggregation."""
        return sum(values) / len(values)

    def _aggregate_weighted_average(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Weighted average aggregation."""
        if not weights:
            raise CalculationError("Weights required for weighted average")

        if len(values) != len(weights):
            raise CalculationError("Values and weights must have same length")

        decimal_weights = [Decimal(str(w)) for w in weights]
        weighted_sum = sum(v * w for v, w in zip(values, decimal_weights))
        total_weight = sum(decimal_weights)

        if total_weight == 0:
            raise CalculationError("Total weight cannot be zero")

        return weighted_sum / total_weight

    def _aggregate_min(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Minimum aggregation."""
        return min(values)

    def _aggregate_max(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Maximum aggregation."""
        return max(values)

    def _aggregate_count(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Count aggregation."""
        return Decimal(len(values))

    def _aggregate_first(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """First value aggregation."""
        return values[0]

    def _aggregate_last(
        self, values: List[Decimal], weights: Optional[List] = None
    ) -> Decimal:
        """Last value aggregation."""
        return values[-1]

    def clear_cache(self):
        """Clear calculation and dependency caches."""
        self._dependency_cache.clear()
        self._calculation_cache.clear()
        self.logger.info("Calculation caches cleared")

    def get_calculation_stats(self) -> Dict[str, Any]:
        """Get calculation engine statistics."""
        return {
            "precision": self.precision,
            "timeout": self.timeout,
            "supported_operators": [op.__name__ for op in self.safe_operators.keys()],
            "supported_functions": list(self.safe_functions.keys()),
            "aggregation_methods": [method.value for method in AggregationMethod],
            "temporal_granularities": [gran.value for gran in TemporalGranularity],
            "cached_dependencies": len(self._dependency_cache),
            "cached_calculations": len(self._calculation_cache),
        }
