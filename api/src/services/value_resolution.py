"""Runtime value resolution across values, calculations, mappings, and units."""

from __future__ import annotations

import calendar
import inspect
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, Optional

from fastapi import HTTPException

from src.api.models import (
    CalculationRequest,
    CalculationResponse,
    TemporalGranularity,
    ValueResolutionMethod,
    ValueResolutionStatus,
    ValueResolveRequest,
    ValueResolveResponse,
)

CalculateCallable = Callable[
    [CalculationRequest], Awaitable[CalculationResponse] | CalculationResponse
]

_EQUIVALENT_RELATIONSHIPS = {
    "equivalent",
    "exact",
    "exact_match",
    "exactmatch",
    "same_as",
    "sameas",
    "identity",
}
_COMPONENT_AGGREGATION_RELATIONSHIPS = {"narrower"}


@dataclass(frozen=True)
class _MappingRoute:
    row: Any
    direction: str
    source_concept: str
    target_concept: str

    @property
    def relationship_type(self) -> str:
        return str(getattr(self.row, "relationship_type", "") or "").strip().lower()

    @property
    def row_id(self) -> str:
        return str(getattr(self.row, "id", ""))

    @property
    def is_equivalent(self) -> bool:
        return (
            _normalize_relationship(self.relationship_type) in _EQUIVALENT_RELATIONSHIPS
        )

    def as_candidate(self) -> dict[str, Any]:
        return {
            "mapping_row_id": self.row_id,
            "direction": self.direction,
            "source_concept": self.source_concept,
            "target_concept": self.target_concept,
            "source_standard": getattr(self.row, "source_standard", None),
            "source_code": getattr(self.row, "source_code", None),
            "target_standard": getattr(self.row, "target_standard", None),
            "target_code": getattr(self.row, "target_code", None),
            "relationship_type": self.relationship_type,
            "confidence": _optional_float(getattr(self.row, "confidence", None)),
            "dataset": getattr(self.row, "dataset", None),
            "transferable": self.is_equivalent,
        }


@dataclass(frozen=True)
class _RouteValue:
    value: Any
    unit: Optional[str]
    value_id: Optional[str]
    method: ValueResolutionMethod
    source_concept: Optional[str]
    contract_id: Optional[str] = None
    contract_version: Optional[str] = None
    execution_authority: Optional[str] = None
    bridge_id: Optional[str] = None
    calculation_trace: Optional[dict[str, Any]] = None


async def resolve_value_request(
    request: ValueResolveRequest,
    *,
    value_store: Any,
    unit_converter: Any = None,
    mapping_store: Any = None,
    calculate: Optional[CalculateCallable] = None,
) -> ValueResolveResponse:
    """Resolve a requested value or return a structured fail-closed refusal."""

    period_start, period_end = _period_window(request.period, request.granularity)

    direct_target, direct_target_concept = _latest_value_for_concepts(
        value_store,
        concepts=_value_concept_candidates(request.target_concept),
        entity=request.entity,
        period_start=period_start,
        period_end=period_end,
    )
    if direct_target is not None:
        route = _RouteValue(
            value=direct_target.value,
            unit=direct_target.unit,
            value_id=direct_target.id,
            method=ValueResolutionMethod.DIRECT_STORED,
            source_concept=direct_target_concept or request.target_concept,
        )
        alternative = (
            await _calculation_alternative(request, calculate=calculate)
            if request.include_trace and calculate is not None
            else None
        )
        return _response_from_route(
            request,
            route,
            unit_converter=unit_converter,
            relationship_type=None,
            mapping_route=None,
            alternative_route=alternative,
        )

    target_calculation = await _try_calculation(request, calculate=calculate)
    if target_calculation is not None:
        return _response_from_route(
            request,
            target_calculation,
            unit_converter=unit_converter,
            relationship_type=None,
            mapping_route=None,
        )

    if request.source_concept:
        return await _resolve_from_explicit_source(
            request,
            value_store=value_store,
            period_start=period_start,
            period_end=period_end,
            unit_converter=unit_converter,
            mapping_store=mapping_store,
            calculate=calculate,
        )

    return await _resolve_from_mapping_candidates(
        request,
        value_store=value_store,
        period_start=period_start,
        period_end=period_end,
        unit_converter=unit_converter,
        mapping_store=mapping_store,
        calculate=calculate,
    )


async def _resolve_from_explicit_source(
    request: ValueResolveRequest,
    *,
    value_store: Any,
    period_start: date,
    period_end: date,
    unit_converter: Any,
    mapping_store: Any,
    calculate: Optional[CalculateCallable],
) -> ValueResolveResponse:
    assert request.source_concept is not None
    routes = _find_mapping_routes(
        mapping_store,
        source_concept=request.source_concept,
        target_concept=request.target_concept,
    )
    if not routes:
        return _empty_response(
            request,
            status=ValueResolutionStatus.NOT_FOUND,
            trace_reason="No mapping route or direct value found for requested source and target.",
        )

    equivalent = [route for route in routes if route.is_equivalent]
    if len(equivalent) > 1:
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.AMBIGUOUS_MAPPING,
            method=None,
            routes=equivalent,
            reason="More than one exact/equivalent mapping row matches the requested pair.",
        )
    if not equivalent:
        target_routes = _find_mapping_routes(
            mapping_store,
            source_concept=None,
            target_concept=request.target_concept,
        )
        component_result = await _try_component_aggregation(
            request,
            routes=_component_routes(target_routes or routes),
            value_store=value_store,
            period_start=period_start,
            period_end=period_end,
            unit_converter=unit_converter,
            calculate=calculate,
            explicit_source_concept=request.source_concept,
        )
        if component_result is not None:
            return component_result
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.NOT_TRANSFORMABLE,
            method=ValueResolutionMethod.REFUSED_NON_EQUIVALENT_MAPPING,
            routes=routes,
            reason="Only non-equivalent mapping relationships were found; numeric transfer is refused.",
        )

    route = equivalent[0]
    source_value = await _resolve_source_route(
        request,
        source_concept=request.source_concept,
        value_store=value_store,
        period_start=period_start,
        period_end=period_end,
        calculate=calculate,
        mapped=True,
    )
    if source_value is None:
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.NOT_ENOUGH_EVIDENCE,
            method=None,
            routes=[route],
            reason="Exact/equivalent mapping exists, but no source value or executable source calculation resolved.",
        )

    return _response_from_route(
        request,
        source_value,
        unit_converter=unit_converter,
        relationship_type=route.relationship_type,
        mapping_route=route,
    )


async def _resolve_from_mapping_candidates(
    request: ValueResolveRequest,
    *,
    value_store: Any,
    period_start: date,
    period_end: date,
    unit_converter: Any,
    mapping_store: Any,
    calculate: Optional[CalculateCallable],
) -> ValueResolveResponse:
    routes = _find_mapping_routes(
        mapping_store,
        source_concept=None,
        target_concept=request.target_concept,
    )
    if not routes:
        return _empty_response(
            request,
            status=ValueResolutionStatus.NOT_FOUND,
            trace_reason="No direct value, calculation, or mapping candidate resolved the request.",
        )

    equivalent = [route for route in routes if route.is_equivalent]
    if not equivalent:
        component_result = await _try_component_aggregation(
            request,
            routes=_component_routes(routes),
            value_store=value_store,
            period_start=period_start,
            period_end=period_end,
            unit_converter=unit_converter,
            calculate=calculate,
        )
        if component_result is not None:
            return component_result
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.NOT_TRANSFORMABLE,
            method=ValueResolutionMethod.REFUSED_NON_EQUIVALENT_MAPPING,
            routes=routes,
            reason="Mapping candidates exist but none are exact/equivalent, so numeric transfer is refused.",
        )

    resolved: list[tuple[_MappingRoute, _RouteValue]] = []
    for route in equivalent:
        source_route = await _resolve_source_route(
            request,
            source_concept=route.source_concept,
            value_store=value_store,
            period_start=period_start,
            period_end=period_end,
            calculate=calculate,
            mapped=True,
        )
        if source_route is not None:
            resolved.append((route, source_route))

    if len(resolved) > 1:
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.MULTIPLE_TARGETS,
            method=None,
            routes=[route for route, _value in resolved],
            reason="More than one mapped source route can satisfy the target request.",
        )
    if not resolved:
        return _mapping_refusal(
            request,
            status=ValueResolutionStatus.NOT_ENOUGH_EVIDENCE,
            method=None,
            routes=equivalent,
            reason="Exact/equivalent mapping candidates exist, but none had source values or executable source calculations.",
        )

    mapping_route, source_value = resolved[0]
    return _response_from_route(
        request,
        source_value,
        unit_converter=unit_converter,
        relationship_type=mapping_route.relationship_type,
        mapping_route=mapping_route,
    )


async def _resolve_source_route(
    request: ValueResolveRequest,
    *,
    source_concept: str,
    value_store: Any,
    period_start: date,
    period_end: date,
    calculate: Optional[CalculateCallable],
    mapped: bool,
) -> Optional[_RouteValue]:
    source_value, resolved_source_concept = _latest_value_for_concepts(
        value_store,
        concepts=_value_concept_candidates(source_concept),
        entity=request.entity,
        period_start=period_start,
        period_end=period_end,
    )
    if source_value is not None:
        return _RouteValue(
            value=source_value.value,
            unit=source_value.unit,
            value_id=source_value.id,
            method=(
                ValueResolutionMethod.EQUIVALENT_MAPPING
                if mapped
                else ValueResolutionMethod.DIRECT_STORED
            ),
            source_concept=resolved_source_concept or source_concept,
        )

    source_request = ValueResolveRequest(
        source_concept=None,
        target_concept=source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        target_unit=None,
        include_trace=request.include_trace,
    )
    calculation = await _try_calculation(source_request, calculate=calculate)
    if calculation is None:
        return None
    return _RouteValue(
        value=calculation.value,
        unit=calculation.unit,
        value_id=None,
        method=ValueResolutionMethod.CALCULATION_THEN_MAPPING,
        source_concept=source_concept,
        contract_id=calculation.contract_id,
        contract_version=calculation.contract_version,
        execution_authority=calculation.execution_authority,
        bridge_id=calculation.bridge_id,
        calculation_trace=calculation.calculation_trace,
    )


async def _try_component_aggregation(
    request: ValueResolveRequest,
    *,
    routes: list[_MappingRoute],
    value_store: Any,
    period_start: date,
    period_end: date,
    unit_converter: Any,
    calculate: Optional[CalculateCallable],
    explicit_source_concept: Optional[str] = None,
) -> Optional[ValueResolveResponse]:
    component_routes = _dedupe_routes(_component_routes(routes))
    if len(component_routes) < 2:
        return None

    resolved: list[tuple[_MappingRoute, _RouteValue]] = []
    missing: list[str] = []
    for route in component_routes:
        route_value = await _resolve_source_route(
            request,
            source_concept=route.source_concept,
            value_store=value_store,
            period_start=period_start,
            period_end=period_end,
            calculate=calculate,
            mapped=True,
        )
        if route_value is None:
            missing.append(route.source_concept)
            continue
        resolved.append((route, route_value))

    candidates = [route.as_candidate() for route in component_routes]
    mapping_row_ids = [route.row_id for route in component_routes if route.row_id]
    component_concepts = [route.source_concept for route in component_routes]

    if missing:
        trace = None
        if request.include_trace:
            trace = {
                "reason": (
                    "Target can be derived only by aggregating all narrower "
                    "component mappings; one or more component values are missing."
                ),
                "route": ValueResolutionMethod.COMPONENT_AGGREGATION.value,
                "relationship_type": "narrower",
                "required_components": component_concepts,
                "missing_inputs": missing,
                "candidates": candidates,
            }
        return ValueResolveResponse(
            status=ValueResolutionStatus.NOT_ENOUGH_EVIDENCE,
            method=None,
            target_concept=request.target_concept,
            source_concept=explicit_source_concept or request.source_concept,
            entity=request.entity,
            period=request.period,
            granularity=request.granularity,
            value=None,
            unit=request.target_unit,
            relationship_type="narrower",
            mapping_row_ids=mapping_row_ids,
            component_concepts=component_concepts,
            missing_inputs=missing,
            candidates=candidates,
            trace=trace,
        )

    target_unit = request.target_unit or _first_non_empty_unit(
        route_value.unit for _route, route_value in resolved
    )
    if not target_unit:
        return _component_refusal_response(
            request,
            status=ValueResolutionStatus.NOT_TRANSFORMABLE,
            method=ValueResolutionMethod.COMPONENT_AGGREGATION,
            routes=component_routes,
            reason="Narrower component values resolved, but no aggregation unit is available.",
            source_concept=explicit_source_concept,
        )

    total = Decimal("0")
    conversion_steps: list[dict[str, Any]] = []
    trace_components: list[dict[str, Any]] = []
    for route, route_value in resolved:
        decimal_value = _decimal_value(route_value.value)
        if decimal_value is None:
            return _component_refusal_response(
                request,
                status=ValueResolutionStatus.NOT_TRANSFORMABLE,
                method=ValueResolutionMethod.COMPONENT_AGGREGATION,
                routes=component_routes,
                reason=(
                    "Narrower component aggregation requires numeric component "
                    f"values; {route.source_concept} resolved to a non-numeric value."
                ),
                source_concept=explicit_source_concept,
            )

        component_value = decimal_value
        component_unit = route_value.unit
        if component_unit and component_unit != target_unit:
            if unit_converter is None:
                return _component_unsupported_conversion_response(
                    request,
                    routes=component_routes,
                    from_unit=component_unit,
                    to_unit=target_unit,
                    error="Unit converter is not initialized.",
                    source_concept=explicit_source_concept,
                )
            try:
                conversion_result = unit_converter.convert(
                    component_value, component_unit, target_unit
                )
            except Exception as exc:
                return _component_unsupported_conversion_response(
                    request,
                    routes=component_routes,
                    from_unit=component_unit,
                    to_unit=target_unit,
                    error=str(exc),
                    source_concept=explicit_source_concept,
                )
            component_value = Decimal(str(conversion_result.converted_value))
            component_unit = conversion_result.converted_unit
            conversion_steps.append(
                {
                    "step_type": "unit",
                    "source_concept": route.source_concept,
                    "from_unit": conversion_result.original_unit,
                    "to_unit": conversion_result.converted_unit,
                    "original_value": str(conversion_result.original_value),
                    "converted_value": str(conversion_result.converted_value),
                    "formula": conversion_result.formula_used,
                }
            )

        total += component_value
        trace_components.append(
            {
                "mapping_row_id": route.row_id,
                "mapping_source_concept": route.source_concept,
                "resolved_source_concept": route_value.source_concept,
                "source_value_id": route_value.value_id,
                "value": str(component_value),
                "unit": component_unit,
            }
        )

    trace = None
    if request.include_trace:
        trace = {
            "route": ValueResolutionMethod.COMPONENT_AGGREGATION.value,
            "relationship_type": "narrower",
            "target_concept": request.target_concept,
            "component_count": len(resolved),
            "formula": " + ".join(component_concepts),
            "components": trace_components,
        }
        if conversion_steps:
            trace["conversion"] = conversion_steps

    return ValueResolveResponse(
        status=ValueResolutionStatus.RESOLVED,
        method=ValueResolutionMethod.COMPONENT_AGGREGATION,
        target_concept=request.target_concept,
        source_concept=explicit_source_concept or request.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=total,
        unit=target_unit,
        value_id=None,
        relationship_type="narrower",
        mapping_row_ids=mapping_row_ids,
        component_concepts=component_concepts,
        candidates=candidates,
        conversion_steps=conversion_steps,
        trace=trace,
    )


def _response_from_route(
    request: ValueResolveRequest,
    route: _RouteValue,
    *,
    unit_converter: Any,
    relationship_type: Optional[str],
    mapping_route: Optional[_MappingRoute],
    alternative_route: Optional[dict[str, Any]] = None,
) -> ValueResolveResponse:
    value = route.value
    unit = route.unit
    method = route.method
    conversion_steps: list[dict[str, Any]] = []

    if request.target_unit and unit and unit != request.target_unit:
        if unit_converter is None:
            return _unsupported_conversion_response(
                request,
                from_unit=unit,
                to_unit=request.target_unit,
                error="Unit converter is not initialized.",
                method=method,
                route=route,
                mapping_route=mapping_route,
            )
        try:
            conversion_result = unit_converter.convert(value, unit, request.target_unit)
        except Exception as exc:
            return _unsupported_conversion_response(
                request,
                from_unit=unit,
                to_unit=request.target_unit,
                error=str(exc),
                method=ValueResolutionMethod.CONVERTED,
                route=route,
                mapping_route=mapping_route,
            )
        value = conversion_result.converted_value
        unit = conversion_result.converted_unit
        conversion_steps.append(
            {
                "step_type": "unit",
                "from_unit": conversion_result.original_unit,
                "to_unit": conversion_result.converted_unit,
                "original_value": str(conversion_result.original_value),
                "converted_value": str(conversion_result.converted_value),
                "formula": conversion_result.formula_used,
            }
        )
        if method == ValueResolutionMethod.EQUIVALENT_MAPPING:
            method = ValueResolutionMethod.MAPPING_THEN_CONVERSION
        elif method == ValueResolutionMethod.DIRECT_STORED:
            method = ValueResolutionMethod.CONVERTED

    trace = None
    candidates: list[dict[str, Any]] = []
    mapping_row_ids: list[str] = []
    if mapping_route is not None:
        candidate = mapping_route.as_candidate()
        candidates.append(candidate)
        mapping_row_ids.append(mapping_route.row_id)
    if request.include_trace:
        trace = {
            "route": method.value,
            "source_value_id": route.value_id,
            "source_concept": route.source_concept,
            "target_concept": request.target_concept,
        }
        if mapping_route is not None:
            trace["mapping"] = mapping_route.as_candidate()
        if route.calculation_trace is not None:
            trace["calculation"] = route.calculation_trace
        _add_route_execution_metadata(trace, route)
        if conversion_steps:
            trace["conversion"] = conversion_steps[-1]

    return ValueResolveResponse(
        status=ValueResolutionStatus.RESOLVED,
        method=method,
        target_concept=request.target_concept,
        source_concept=route.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=value,
        unit=unit,
        value_id=route.value_id,
        relationship_type=relationship_type,
        mapping_row_ids=mapping_row_ids,
        contract_id=route.contract_id,
        contract_version=route.contract_version,
        execution_authority=route.execution_authority,
        bridge_id=route.bridge_id,
        candidates=candidates,
        conversion_steps=conversion_steps,
        alternative_route=alternative_route,
        trace=trace,
    )


def _unsupported_conversion_response(
    request: ValueResolveRequest,
    *,
    from_unit: str,
    to_unit: str,
    error: str,
    method: ValueResolutionMethod,
    route: _RouteValue,
    mapping_route: Optional[_MappingRoute],
) -> ValueResolveResponse:
    trace = None
    if request.include_trace:
        trace = {
            "route": method.value,
            "source_value_id": route.value_id,
            "source_concept": route.source_concept,
            "target_concept": request.target_concept,
            "conversion": {
                "from_unit": from_unit,
                "to_unit": to_unit,
                "error": error,
            },
        }
        if mapping_route is not None:
            trace["mapping"] = mapping_route.as_candidate()
        _add_route_execution_metadata(trace, route)

    return ValueResolveResponse(
        status=ValueResolutionStatus.UNSUPPORTED_CONVERSION,
        method=method,
        target_concept=request.target_concept,
        source_concept=route.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=None,
        unit=None,
        value_id=route.value_id,
        relationship_type=(mapping_route.relationship_type if mapping_route else None),
        mapping_row_ids=[mapping_route.row_id] if mapping_route else [],
        contract_id=route.contract_id,
        contract_version=route.contract_version,
        execution_authority=route.execution_authority,
        bridge_id=route.bridge_id,
        trace=trace,
    )


def _component_refusal_response(
    request: ValueResolveRequest,
    *,
    status: ValueResolutionStatus,
    method: Optional[ValueResolutionMethod],
    routes: list[_MappingRoute],
    reason: str,
    source_concept: Optional[str],
) -> ValueResolveResponse:
    candidates = [route.as_candidate() for route in routes]
    component_concepts = [route.source_concept for route in routes]
    trace = None
    if request.include_trace:
        trace = {
            "reason": reason,
            "route": (
                method.value
                if isinstance(method, ValueResolutionMethod)
                else ValueResolutionMethod.COMPONENT_AGGREGATION.value
            ),
            "relationship_type": "narrower",
            "required_components": component_concepts,
            "candidates": candidates,
        }
    return ValueResolveResponse(
        status=status,
        method=method,
        target_concept=request.target_concept,
        source_concept=source_concept or request.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=None,
        unit=request.target_unit,
        relationship_type="narrower",
        mapping_row_ids=[route.row_id for route in routes if route.row_id],
        component_concepts=component_concepts,
        candidates=candidates,
        trace=trace,
    )


def _component_unsupported_conversion_response(
    request: ValueResolveRequest,
    *,
    routes: list[_MappingRoute],
    from_unit: str,
    to_unit: str,
    error: str,
    source_concept: Optional[str],
) -> ValueResolveResponse:
    candidates = [route.as_candidate() for route in routes]
    component_concepts = [route.source_concept for route in routes]
    trace = None
    if request.include_trace:
        trace = {
            "route": ValueResolutionMethod.COMPONENT_AGGREGATION.value,
            "relationship_type": "narrower",
            "required_components": component_concepts,
            "conversion": {
                "from_unit": from_unit,
                "to_unit": to_unit,
                "error": error,
            },
            "candidates": candidates,
        }
    return ValueResolveResponse(
        status=ValueResolutionStatus.UNSUPPORTED_CONVERSION,
        method=ValueResolutionMethod.COMPONENT_AGGREGATION,
        target_concept=request.target_concept,
        source_concept=source_concept or request.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=None,
        unit=None,
        relationship_type="narrower",
        mapping_row_ids=[route.row_id for route in routes if route.row_id],
        component_concepts=component_concepts,
        candidates=candidates,
        trace=trace,
    )


def _mapping_refusal(
    request: ValueResolveRequest,
    *,
    status: ValueResolutionStatus,
    method: Optional[ValueResolutionMethod],
    routes: list[_MappingRoute],
    reason: str,
) -> ValueResolveResponse:
    candidates = [route.as_candidate() for route in routes]
    trace = (
        {"reason": reason, "candidates": candidates} if request.include_trace else None
    )
    return ValueResolveResponse(
        status=status,
        method=method,
        target_concept=request.target_concept,
        source_concept=request.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=None,
        unit=None,
        relationship_type=(routes[0].relationship_type if routes else None),
        mapping_row_ids=[route.row_id for route in routes if route.row_id],
        candidates=candidates,
        trace=trace,
    )


def _empty_response(
    request: ValueResolveRequest,
    *,
    status: ValueResolutionStatus,
    trace_reason: str,
) -> ValueResolveResponse:
    return ValueResolveResponse(
        status=status,
        method=None,
        target_concept=request.target_concept,
        source_concept=request.source_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        value=None,
        unit=request.target_unit,
        trace={"reason": trace_reason} if request.include_trace else None,
    )


async def _try_calculation(
    request: ValueResolveRequest,
    *,
    calculate: Optional[CalculateCallable],
) -> Optional[_RouteValue]:
    if calculate is None:
        return None
    calc_request = CalculationRequest(
        concept=request.target_concept,
        entity=request.entity,
        period=request.period,
        granularity=request.granularity,
        include_trace=request.include_trace,
    )
    try:
        maybe_response = calculate(calc_request)
        response = (
            await maybe_response
            if inspect.isawaitable(maybe_response)
            else maybe_response
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise
        return None
    except Exception:
        return None

    contract_id = _trace_field(response, "contract_id")
    execution_authority = response.execution_authority
    bridge_id = response.bridge_id or (
        contract_id if execution_authority == "certified_bridge" else None
    )
    return _RouteValue(
        value=response.value,
        unit=response.unit,
        value_id=None,
        method=ValueResolutionMethod.CALCULATION,
        source_concept=response.concept,
        contract_id=contract_id,
        contract_version=_trace_field(response, "contract_version"),
        execution_authority=execution_authority,
        bridge_id=bridge_id,
        calculation_trace=_calculation_trace_dict(response),
    )


async def _calculation_alternative(
    request: ValueResolveRequest,
    *,
    calculate: CalculateCallable,
) -> Optional[dict[str, Any]]:
    route = await _try_calculation(request, calculate=calculate)
    if route is None:
        return None
    return {
        "method": ValueResolutionMethod.CALCULATION.value,
        "value": str(route.value),
        "unit": route.unit,
        "contract_id": route.contract_id,
        "contract_version": route.contract_version,
        "execution_authority": route.execution_authority,
        "bridge_id": route.bridge_id,
    }


def _latest_value_for_concepts(
    store: Any,
    *,
    concepts: Iterable[str],
    entity: str,
    period_start: date,
    period_end: date,
) -> tuple[Any | None, Optional[str]]:
    for concept in concepts:
        value = _latest_value(
            store,
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
        )
        if value is not None:
            return value, concept
    return None, None


def _latest_value(
    store: Any,
    *,
    concept: str,
    entity: str,
    period_start: date,
    period_end: date,
) -> Any | None:
    if hasattr(store, "latest"):
        return store.latest(
            concept=concept,
            entity=entity,
            period_start=period_start,
            period_end=period_end,
        )

    values = _iter_values(
        store,
        concept=concept,
        entity=entity,
        period_start=period_start,
        period_end=period_end,
    )
    if not values:
        return None
    values.sort(
        key=lambda value: (
            getattr(value, "period", date.min),
            getattr(value, "updated_at", None) or getattr(value, "created_at", None),
            getattr(value, "id", ""),
        ),
        reverse=True,
    )
    return values[0]


def _iter_values(
    store: Any,
    *,
    concept: str,
    entity: str,
    period_start: date,
    period_end: date,
) -> list[Any]:
    if hasattr(store, "iterate"):
        return list(
            store.iterate(
                concept=concept,
                entity=entity,
                period_start=period_start,
                period_end=period_end,
                unit=None,
                changed_since=None,
            )
        )
    items, _total = store.list(
        concept=concept,
        entity=entity,
        period_start=period_start,
        period_end=period_end,
        unit=None,
        changed_since=None,
        limit=100,
        offset=0,
    )
    return list(items)


def find_mapping_routes(
    mapping_store: Any,
    *,
    source_concept: Optional[str],
    target_concept: str,
) -> list[_MappingRoute]:
    """Find canonical mapping routes that can connect two concepts."""

    return _find_mapping_routes(
        mapping_store,
        source_concept=source_concept,
        target_concept=target_concept,
    )


def _find_mapping_routes(
    mapping_store: Any,
    *,
    source_concept: Optional[str],
    target_concept: str,
) -> list[_MappingRoute]:
    if mapping_store is None:
        return []

    rows = _load_mapping_rows(mapping_store, source_concept, target_concept)
    routes: list[_MappingRoute] = []
    for row in rows:
        forward_source = (
            _side_matches(
                source_concept,
                getattr(row, "source_standard", None),
                getattr(row, "source_code", None),
            )
            if source_concept
            else True
        )
        forward_target = _side_matches(
            target_concept,
            getattr(row, "target_standard", None),
            getattr(row, "target_code", None),
        )
        if forward_source and forward_target:
            inferred_source = source_concept or _concept_from_mapping_side(
                getattr(row, "source_standard", None), getattr(row, "source_code", None)
            )
            routes.append(
                _MappingRoute(row, "forward", inferred_source, target_concept)
            )
            continue

        inverse_target = _side_matches(
            target_concept,
            getattr(row, "source_standard", None),
            getattr(row, "source_code", None),
        )
        if not inverse_target:
            continue
        if source_concept:
            inverse_source = _side_matches(
                source_concept,
                getattr(row, "target_standard", None),
                getattr(row, "target_code", None),
            )
            if inverse_source:
                routes.append(
                    _MappingRoute(row, "inverse", source_concept, target_concept)
                )
        else:
            inferred_source = _concept_from_mapping_side(
                getattr(row, "target_standard", None), getattr(row, "target_code", None)
            )
            routes.append(
                _MappingRoute(row, "inverse", inferred_source, target_concept)
            )
    return _dedupe_routes(routes)


def _load_mapping_rows(
    mapping_store: Any,
    source_concept: Optional[str],
    target_concept: str,
) -> list[Any]:
    rows_by_id: dict[str, Any] = {}
    used_unfiltered_fallback = False

    def add(rows: Iterable[Any]) -> None:
        for row in rows or []:
            key = str(getattr(row, "id", id(row)))
            rows_by_id[key] = row

    def search_once(**kwargs: Any) -> None:
        nonlocal used_unfiltered_fallback
        try:
            add(mapping_store.search(**kwargs))
        except TypeError:
            if not used_unfiltered_fallback:
                add(mapping_store.search())
                used_unfiltered_fallback = True

    source_standards = (
        _standard_candidates(source_concept) if source_concept else [None]
    )
    target_standards = _standard_candidates(target_concept) or [None]
    source_codes = _raw_code_candidates(source_concept) if source_concept else [None]
    target_codes = _raw_code_candidates(target_concept) or [None]

    if hasattr(mapping_store, "find_candidate_routes"):
        try:
            add(
                mapping_store.find_candidate_routes(
                    source_standards=[item for item in source_standards if item],
                    source_codes=[item for item in source_codes if item],
                    target_standards=[item for item in target_standards if item],
                    target_codes=[item for item in target_codes if item],
                    limit=500,
                )
            )
            return list(rows_by_id.values())
        except TypeError:
            rows_by_id.clear()

    if hasattr(mapping_store, "search"):
        for source_standard in source_standards:
            for source_code in source_codes:
                for target_standard in target_standards:
                    for target_code in target_codes:
                        kwargs = {"limit": 500}
                        if source_standard:
                            kwargs["source_standard"] = source_standard
                        if source_code:
                            kwargs["source_code"] = source_code
                        if target_standard:
                            kwargs["target_standard"] = target_standard
                        if target_code:
                            kwargs["target_code"] = target_code
                        search_once(**kwargs)

        for target_standard in target_standards:
            for target_code in target_codes:
                for source_standard in source_standards:
                    for source_code in source_codes:
                        kwargs = {"limit": 500}
                        if target_standard:
                            kwargs["source_standard"] = target_standard
                        if target_code:
                            kwargs["source_code"] = target_code
                        if source_standard:
                            kwargs["target_standard"] = source_standard
                        if source_code:
                            kwargs["target_code"] = source_code
                        search_once(**kwargs)
    if not rows_by_id and hasattr(mapping_store, "get_all"):
        try:
            add(mapping_store.get_all(limit=500, offset=0))
        except TypeError:
            add(mapping_store.get_all())

    return list(rows_by_id.values())


def _dedupe_routes(routes: list[_MappingRoute]) -> list[_MappingRoute]:
    seen: set[tuple[str, str]] = set()
    deduped: list[_MappingRoute] = []
    for route in routes:
        key = (route.row_id, route.direction)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(route)
    return deduped


def _component_routes(routes: list[_MappingRoute]) -> list[_MappingRoute]:
    return [
        route
        for route in routes
        if _normalize_relationship(route.relationship_type)
        in _COMPONENT_AGGREGATION_RELATIONSHIPS
    ]


def _value_concept_candidates(concept: str) -> list[str]:
    raw = str(concept or "").strip()
    candidates: list[str] = []

    def add(value: str) -> None:
        token = str(value or "").strip()
        if token and token not in candidates:
            candidates.append(token)

    add(raw)
    urn_match = re.match(r"^urn:sds:reg:([^:]+):(.+)$", raw, re.IGNORECASE)
    if urn_match:
        namespace, segment = urn_match.groups()
        namespace = namespace.lower()
        segment = segment.strip()
        if namespace in {"esrs", "csrd"}:
            code = _esrs_code_from_sds_segment(segment)
            add(f"csrd:{code}")
            add(f"esrs:{code}")
        elif namespace == "gri":
            code = _gri_code_from_sds_segment(segment)
            add(f"gri:{code}")
            if code.upper().startswith("GRI "):
                add(f"gri:{code[4:]}")
        return candidates

    prefix, separator, local = raw.partition(":")
    if not separator:
        return candidates

    prefix = prefix.strip().lower()
    local = local.strip()
    if prefix in {"csrd", "esrs"}:
        segment = _sds_identifier_segment(local)
        add(f"urn:sds:reg:esrs:{segment}")
        add(f"urn:sds:reg:csrd:{segment}")
        add(f"csrd:{local.replace('_', '-')}")
        add(f"esrs:{local.replace('_', '-')}")
    elif prefix == "gri":
        add(f"urn:sds:reg:gri:{_sds_identifier_segment(local)}")
        if not local.upper().startswith("GRI "):
            add(f"urn:sds:reg:gri:{_sds_identifier_segment('GRI ' + local)}")
            add(f"gri:GRI {local}")
    return candidates


def _sds_identifier_segment(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return re.sub(r"_+", "_", token).strip("_")


def _esrs_code_from_sds_segment(segment: str) -> str:
    token = str(segment or "").strip().lower()
    match = re.match(r"^([esg]\d+)_(\d+)_(.+)$", token)
    if match:
        return f"{match.group(1)}-{match.group(2)}_{match.group(3)}".upper()
    return token.replace("_", "-").upper()


def _gri_code_from_sds_segment(segment: str) -> str:
    token = str(segment or "").strip().lower()
    if token.startswith("gri_"):
        token = token[4:]
    parts = [part for part in token.split("_") if part]
    if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
        suffix = ".".join(parts[2:])
        return f"GRI {parts[0]}-{parts[1]}.{suffix}"
    return token.replace("_", "-").upper()


def _first_non_empty_unit(units: Iterable[Optional[str]]) -> Optional[str]:
    for unit in units:
        if unit:
            return unit
    return None


def _decimal_value(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _side_matches(
    concept: Optional[str],
    standard: Any,
    code: Any,
) -> bool:
    if concept is None:
        return True
    standards = {_normalize_token(item) for item in _standard_candidates(concept)}
    if standards:
        row_standard = _normalize_token(standard)
        if row_standard and row_standard not in standards:
            return False

    row_code = _normalize_code(code)
    return any(row_code == candidate for candidate in _code_candidates(concept))


def _standard_candidates(concept: Optional[str]) -> list[str]:
    if not concept:
        return []
    prefix = concept.split(":", 1)[0].lower() if ":" in concept else ""
    mapping = {
        "csrd": ["CSRD", "ESRS"],
        "esrs": ["ESRS", "CSRD"],
        "gri": ["GRI"],
        "syg": ["SYGRIS", "SYG", "SDS"],
        "sygris": ["SYGRIS", "SYG", "SDS"],
    }
    return mapping.get(prefix, [prefix.upper()] if prefix else [])


def _code_candidates(concept: str) -> set[str]:
    prefix = concept.split(":", 1)[0] if ":" in concept else ""
    local = concept.split(":", 1)[1] if ":" in concept else concept
    raw = {concept, local, local.replace("_", "-"), local.replace("-", "_")}
    prefix_label = prefix.upper()
    if prefix_label in {"GRI", "ESRS", "CSRD"}:
        raw.add(f"{prefix_label} {local}")
        raw.add(f"{prefix_label} {local.replace('_', '-')}")
        if local.upper().startswith(prefix_label + " "):
            stripped = local[len(prefix_label) + 1 :]
            raw.add(stripped)
            raw.add(stripped.replace("_", "-"))
    return {_normalize_code(item) for item in raw if item}


def _raw_code_candidates(concept: Optional[str]) -> list[str]:
    if not concept:
        return []
    prefix = concept.split(":", 1)[0] if ":" in concept else ""
    local = concept.split(":", 1)[1] if ":" in concept else concept
    raw = {concept, local, local.replace("_", "-"), local.replace("-", "_")}
    prefix_label = prefix.upper()
    if prefix_label in {"GRI", "ESRS", "CSRD"}:
        raw.add(f"{prefix_label} {local}")
        raw.add(f"{prefix_label} {local.replace('_', '-')}")
        raw.add(f"{prefix_label} {local.replace('-', '_')}")
        if local.upper().startswith(prefix_label + " "):
            stripped = local[len(prefix_label) + 1 :]
            raw.add(stripped)
            raw.add(stripped.replace("_", "-"))
            raw.add(stripped.replace("-", "_"))
    return sorted({item for item in raw if item}, key=lambda item: (len(item), item))


def _concept_from_mapping_side(standard: Any, code: Any) -> str:
    standard_token = str(standard or "").strip().upper()
    code_token = str(code or "").strip()
    prefix = {
        "CSRD": "csrd",
        "ESRS": "csrd",
        "GRI": "gri",
        "SYGRIS": "syg",
        "SYG": "syg",
        "SDS": "syg",
    }.get(standard_token, standard_token.lower() or "mapped")
    return f"{prefix}:{code_token}"


def _normalize_relationship(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _period_window(period: str, granularity: TemporalGranularity) -> tuple[date, date]:
    token = str(period).strip()
    if granularity == TemporalGranularity.ANNUAL and re.fullmatch(r"\d{4}", token):
        year = int(token)
        return date(year, 1, 1), date(year, 12, 31)
    if granularity == TemporalGranularity.MONTHLY and re.fullmatch(
        r"\d{4}-\d{2}", token
    ):
        year, month = [int(part) for part in token.split("-")]
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    if granularity == TemporalGranularity.QUARTERLY:
        match = re.fullmatch(r"(\d{4})-?Q([1-4])", token, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
            month = 1 + (quarter - 1) * 3
            last_month = month + 2
            return date(year, month, 1), date(
                year, last_month, calendar.monthrange(year, last_month)[1]
            )
    if granularity == TemporalGranularity.SEMESTRAL:
        match = re.fullmatch(r"(\d{4})-?[HS]([12])", token, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            semester = int(match.group(2))
            return (
                (date(year, 1, 1), date(year, 6, 30))
                if semester == 1
                else (date(year, 7, 1), date(year, 12, 31))
            )
    if granularity == TemporalGranularity.WEEKLY:
        match = re.fullmatch(r"(\d{4})-?W(\d{1,2})", token, re.IGNORECASE)
        if match:
            start = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
            return start, date.fromordinal(start.toordinal() + 6)
    parsed = date.fromisoformat(token)
    return parsed, parsed


def _trace_field(response: CalculationResponse, field_name: str) -> Optional[str]:
    trace = response.trace
    if trace is None:
        return None
    for step in trace.steps:
        prefix = f"{field_name}="
        if step.startswith(prefix):
            value = step[len(prefix) :].strip()
            return value or None
    return None


def _add_route_execution_metadata(trace: dict[str, Any], route: _RouteValue) -> None:
    if route.execution_authority is not None:
        trace["execution_authority"] = route.execution_authority
    if route.bridge_id is not None:
        trace["bridge_id"] = route.bridge_id


def _calculation_trace_dict(response: CalculationResponse) -> dict[str, Any] | None:
    if response.trace is None:
        return None
    return response.trace.model_dump(mode="json")
