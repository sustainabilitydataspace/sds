"""Aggregation policies for contract-first calculation runtime."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.calculation.contracts import CalculationContractInput, ContractExecutionError
from src.calculation.value_provider import ObservationRecord


@dataclass(frozen=True)
class AggregationOutcome:
    value: Decimal
    source_value_ids: list[str]
    conversions: list[dict[str, Any]] = field(default_factory=list)
    policy_label: str = ""
    warnings: list[str] = field(default_factory=list)


def resolve_entities(
    contract_input: CalculationContractInput,
    context,
    hierarchy: dict[str, list[str]],
) -> list[str]:
    scope = contract_input.entity_scope.lower()
    entity_id = context.entity_id
    if scope == "self":
        return [entity_id]
    children = list(hierarchy.get(entity_id, []))
    if scope == "children":
        if not children:
            raise ContractExecutionError(
                f"Hierarchy has no children for entity {entity_id}"
            )
        return children
    if scope == "self_and_children":
        return [entity_id, *children]
    raise ContractExecutionError(f"Unsupported entity scope: {scope}")


def aggregate_observations(
    contract_input: CalculationContractInput,
    observations: list[ObservationRecord],
    *,
    unit_normalizer: Any = None,
) -> AggregationOutcome:
    if not observations:
        raise ContractExecutionError(
            f"Cannot aggregate empty observations for {contract_input.local_variable}"
        )

    normalized: list[tuple[ObservationRecord, Decimal]] = []
    conversions: list[dict[str, Any]] = []
    for observation in observations:
        value, detail = _normalize_value(
            observation.value,
            from_unit=observation.unit,
            to_unit=contract_input.unit,
            unit_normalizer=unit_normalizer,
        )
        normalized.append((observation, value))
        if detail:
            conversions.append(
                {
                    "variable": contract_input.local_variable,
                    "value_id": observation.value_id,
                    **detail,
                }
            )

    temporal_rule = contract_input.temporal_aggregation.lower()
    perimeter_rule = contract_input.perimeter_aggregation.lower()
    entity_groups: dict[str, list[tuple[ObservationRecord, Decimal]]] = defaultdict(
        list
    )
    for item in normalized:
        entity_groups[item[0].entity].append(item)

    temporal_values = [
        (
            entity,
            _aggregate_axis(entity_items, temporal_rule, axis="temporal"),
        )
        for entity, entity_items in sorted(entity_groups.items())
    ]
    value = _aggregate_perimeter(temporal_values, perimeter_rule)

    if perimeter_rule not in {
        "sum",
        "average",
        "last",
        "first",
        "none",
        "min",
        "max",
        "count",
    }:
        raise ContractExecutionError(
            f"Unsupported perimeter aggregation: {perimeter_rule}"
        )

    return AggregationOutcome(
        value=value,
        source_value_ids=[observation.value_id for observation in observations],
        conversions=conversions,
        policy_label=(
            f"temporal={temporal_rule};"
            f"perimeter={perimeter_rule};"
            f"entities={contract_input.entity_scope.lower()}"
        ),
    )


def _aggregate_axis(
    items: list[tuple[ObservationRecord, Decimal]], rule: str, *, axis: str
) -> Decimal:
    if rule == "sum":
        return sum(value for _observation, value in items)
    if rule == "average":
        return sum(value for _observation, value in items) / Decimal(len(items))
    if rule == "last":
        _record, value = max(items, key=lambda item: (item[0].period, item[0].value_id))
        return value
    if rule == "first":
        _record, value = min(items, key=lambda item: (item[0].period, item[0].value_id))
        return value
    if rule == "min":
        return min(value for _observation, value in items)
    if rule == "max":
        return max(value for _observation, value in items)
    if rule == "count":
        return Decimal(len(items))
    raise ContractExecutionError(f"Unsupported {axis} aggregation: {rule}")


def _aggregate_perimeter(
    temporal_values: list[tuple[str, Decimal]], perimeter_rule: str
) -> Decimal:
    values = [value for _entity, value in temporal_values]
    if perimeter_rule == "none":
        if len(values) != 1:
            raise ContractExecutionError(
                "Perimeter aggregation 'none' requires exactly one entity"
            )
        return values[0]
    if perimeter_rule == "sum":
        return sum(values)
    if perimeter_rule == "average":
        return sum(values) / Decimal(len(values))
    if perimeter_rule == "min":
        return min(values)
    if perimeter_rule == "max":
        return max(values)
    if perimeter_rule == "count":
        return Decimal(len(values))
    if perimeter_rule == "first":
        return sorted(temporal_values, key=lambda item: item[0])[0][1]
    if perimeter_rule == "last":
        return sorted(temporal_values, key=lambda item: item[0])[-1][1]
    raise ContractExecutionError(f"Unsupported perimeter aggregation: {perimeter_rule}")


def _normalize_value(
    value: Decimal,
    *,
    from_unit: str,
    to_unit: str | None,
    unit_normalizer: Any,
) -> tuple[Decimal, dict[str, Any] | None]:
    if not to_unit or from_unit == to_unit:
        return value, None
    if unit_normalizer is None:
        raise ContractExecutionError(
            f"Mixed units require a unit normalizer: {from_unit} -> {to_unit}"
        )

    if hasattr(unit_normalizer, "normalize"):
        converted, detail = unit_normalizer.normalize(value, from_unit, to_unit)
        return Decimal(str(converted)), dict(detail or {})

    if hasattr(unit_normalizer, "convert"):
        result = unit_normalizer.convert(value, from_unit, to_unit)
        return Decimal(str(result.converted_value)), {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "factor": str(result.conversion_factor),
        }

    raise ContractExecutionError("Invalid unit normalizer hook")
