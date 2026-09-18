"""
API router for calculation endpoints.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Request as FastAPIRequest
from rdflib import Graph, Namespace, URIRef
from sqlalchemy.orm import Session

import structlog
from src.api.models import (
    BatchCalculationItem,
    BatchCalculationStatus,
    CalculationRequest,
    CalculationResponse,
    CalculationTrace,
    TemporalGranularity,
    ValueResolutionMethod,
    ValueResolutionStatus,
    ValueResolveRequest,
    ValueResolveResponse,
)
from src.auth.dependencies import get_current_active_user, require_permission
from src.auth.models import Permission, User, UserRole
from src.calculation.contracts import (
    ContractExecutionError,
    ContractResolutionError,
    RuntimeCalculationContractResolver,
    is_public_calculation_target,
)
from src.calculation.value_provider import StoreObservationProvider
from src.concept_runtime_aliases import CSRD_E3_5_DISCLOSURE_URI, concept_uri_candidates
from src.config.settings import settings
from src.database.session import get_db_optional
from src.ontology.curie import DEFAULT_NAMESPACES
from src.ontology.local_graph import get_ontology_graph
from src.services.hierarchy_store import get_hierarchy_store
from src.services.mapped_observation_provider import MappingAwareObservationProvider
from src.services.runtime_execution import build_conversion_engine
from src.services.standard_mapping_store import StandardMappingStore
from src.services.value_resolution import resolve_value_request
from src.services.value_store import (
    get_value_store,
    resolve_value_store_tenant_id,
    revision_value_store_needs_tenant,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

SDS = Namespace("https://sustainabilitydataspace.com/ontology#")
_EXACT_MAPPING_RELATIONSHIPS = {
    "equivalent",
    "exact",
    "exactmatch",
    "sameas",
    "identity",
}


def _build_value_provider(store, context, *, mapping_store: Optional[Any] = None):
    provider = StoreObservationProvider(store)
    if mapping_store is None:
        return provider
    return MappingAwareObservationProvider(provider, mapping_store)


def _is_admin_user(current_user: User) -> bool:
    role = getattr(current_user, "role", None)
    role_value = getattr(role, "value", role)
    return role_value == UserRole.ADMIN.value


def _value_store_tenant_id(current_user: User) -> Optional[str]:
    if revision_value_store_needs_tenant():
        return resolve_value_store_tenant_id(current_user)
    if not settings.require_database or _is_admin_user(current_user):
        return None
    return resolve_value_store_tenant_id(current_user)


def _hierarchy_scope_company_id(current_user: User) -> Optional[str]:
    if _is_admin_user(current_user):
        return None
    return getattr(current_user, "company_id", None)


def get_authenticated_value_store(
    request: FastAPIRequest,
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
):
    tenant_id = _value_store_tenant_id(current_user)
    return get_value_store(request=request, db=db, tenant_id=tenant_id)


def _batch_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc)


def _public_calculation_denial() -> HTTPException:
    return HTTPException(status_code=403, detail="Calculation contract is not public.")


def _assert_public_calculation_target(contract: Any) -> None:
    if not is_public_calculation_target(contract):
        raise _public_calculation_denial()


def _dependencies_from_contract(concept: str, contract) -> Dict[str, Any]:
    inputs = list(getattr(contract, "inputs", ()) or ())
    variables = [item.concept for item in inputs]
    input_bindings = [_dependency_input_binding(item) for item in inputs]
    return {
        "concept": concept,
        "formula": getattr(contract, "formula", None),
        "required_variables": variables,
        "variable_count": len(variables),
        "unit": getattr(contract, "result_unit", None),
        "calculation_type": "contract",
        "dependency_source": "canonical_contract",
        "input_bindings": input_bindings,
        "supports_certified_bridge_inputs": _has_certified_bridge_input_policy(
            input_bindings
        ),
    }


def _has_certified_bridge_input_policy(input_bindings: List[Dict[str, Any]]) -> bool:
    return any(
        _normalize_mapping_relationship(relationship)
        not in _EXACT_MAPPING_RELATIONSHIPS
        for binding in input_bindings
        for relationship in binding["allowed_mapping_relationships"]
    )


def _dependency_input_binding(contract_input: Any) -> Dict[str, Any]:
    return {
        "local_variable": getattr(contract_input, "local_variable", None),
        "concept": getattr(contract_input, "concept", None),
        "unit": getattr(contract_input, "unit", None),
        "required": bool(getattr(contract_input, "required", True)),
        "entity_scope": getattr(contract_input, "entity_scope", None),
        "temporal_aggregation": getattr(contract_input, "temporal_aggregation", None),
        "perimeter_aggregation": getattr(contract_input, "perimeter_aggregation", None),
        "allowed_mapping_relationships": list(
            getattr(contract_input, "allowed_mapping_relationships", ()) or ()
        ),
    }


def _build_bootstrap_scalar_value_provider(store, context):
    def provider(variable_uri: str, entity_id: str, _period: str) -> float:
        offset = 0
        total = 0
        acc = Decimal("0")
        while True:
            page, total = store.list(
                concept=variable_uri,
                entity=entity_id,
                period_start=context.period_start,
                period_end=context.period_end,
                unit=None,
                limit=1000,
                offset=offset,
            )
            for item in page:
                if getattr(item, "value_type", "numeric") not in {
                    "numeric",
                    "number",
                } or isinstance(item.value, (bool, str)):
                    raise ValueError(
                        f"Non-numeric value for {variable_uri} cannot be used in a calculation"
                    )
                acc += Decimal(str(item.value))
            offset += len(page)
            if not page or offset >= total:
                break
        if total == 0:
            raise ValueError(
                f"Missing values for {variable_uri} entity={entity_id} period={context.period_start}..{context.period_end}"
            )
        return float(acc)

    return provider


def _indicator_unit_from_graph(graph: Graph, concept_curie: str) -> Optional[str]:
    for indicator_iri in _indicator_graph_subjects(concept_curie):
        for unit_iri in graph.objects(indicator_iri, SDS.hasUnit):
            unit_curie = DEFAULT_NAMESPACES.compact(str(unit_iri))
            if unit_curie.endswith("CubicMeter"):
                return "m³"
            if unit_curie.endswith("Tonne"):
                return "t"
            if unit_curie.endswith("Kilogram"):
                return "kg"
            if unit_curie.endswith("Liter"):
                return "L"
            return unit_curie
    return None


def _indicator_formula_expression_from_graph(
    graph: Graph, concept_curie: str
) -> Optional[str]:
    for indicator_iri in _indicator_graph_subjects(concept_curie):
        for formula_iri in graph.objects(indicator_iri, SDS.hasFormula):
            for expr in graph.objects(
                URIRef(str(formula_iri)), SDS.calculationExpression
            ):
                return str(expr).strip()
    return None


def _indicator_variables_from_graph(graph: Graph, concept_curie: str) -> List[str]:
    variables: List[str] = []
    for indicator_iri in _indicator_graph_subjects(concept_curie):
        for var_iri in graph.objects(indicator_iri, SDS.hasVariable):
            variables.append(DEFAULT_NAMESPACES.compact(str(var_iri)))
        if variables:
            break
    return variables


def _indicator_graph_subjects(concept_curie: str) -> List[URIRef]:
    return [
        URIRef(DEFAULT_NAMESPACES.expand(candidate))
        for candidate in concept_uri_candidates(concept_curie)
    ]


def _trace_safe_value(value: Any) -> float | int | str | bool | None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _trace_variable_values(
    input_values: Any,
) -> Dict[str, float | int | str | bool | None]:
    if not isinstance(input_values, dict):
        return {}
    return {str(name): _trace_safe_value(value) for name, value in input_values.items()}


def _trace_dependencies(engine_trace: Any) -> List[str]:
    resolved = list(getattr(engine_trace, "dependencies_resolved", []) or [])
    if resolved:
        return resolved
    source_variables = getattr(engine_trace, "source_variables", []) or []
    return [str(getattr(dep, "variable_uri", dep)) for dep in source_variables]


async def _run_calculation(
    request: CalculationRequest,
    *,
    graph: Graph,
    store,
    db: Optional[Session] = None,
    contract_resolver: Optional[Any] = None,
    allow_ontology_fallback: Optional[bool] = None,
    unit_normalizer: Optional[Any] = None,
    conversion_engine: Optional[Any] = None,
    hierarchy_store: Optional[Any] = None,
    hierarchy_company_id: Optional[str] = None,
    mapping_store: Optional[Any] = None,
    allow_cross_standard_resolution: bool = True,
) -> CalculationResponse:
    from src.calculation.engine import (
        CalculationContext,
        CalculationEngine,
        CalculationError,
    )
    from src.calculation.engine import TemporalGranularity as EngineTemporalGranularity

    engine = CalculationEngine()

    period_start, period_end = _parse_period(request.period, request.granularity)
    context = CalculationContext(
        entity_id=request.entity,
        period_start=period_start,
        period_end=period_end,
        temporal_granularity=EngineTemporalGranularity(request.granularity.value),
        organizational_level=_get_entity_level(
            request.entity,
            hierarchy_store=hierarchy_store,
            company_id=hierarchy_company_id,
        ),
        metadata={"request_id": str(uuid.uuid4())},
    )

    if allow_ontology_fallback is None:
        allow_ontology_fallback = not settings.require_database

    use_contract_runtime = contract_resolver is not None or settings.require_database
    if use_contract_runtime:
        resolver = contract_resolver or RuntimeCalculationContractResolver(db=db)
        observation_provider = _build_value_provider(
            store, context, mapping_store=mapping_store
        )

        try:
            contract = resolver.resolve(request.concept)
            _assert_public_calculation_target(contract)
            result = engine.calculate_contract(
                contract,
                context,
                observation_provider,
                unit_normalizer=unit_normalizer,
                conversion_engine=conversion_engine,
                contract_resolver=resolver,
            )
        except ContractResolutionError as e:
            if allow_ontology_fallback and contract_resolver is None:
                result = None
            else:
                cross_standard = await _try_cross_standard_calculation(
                    request,
                    graph=graph,
                    store=store,
                    db=db,
                    resolver=resolver,
                    mapping_store=mapping_store,
                    unit_normalizer=unit_normalizer,
                    conversion_engine=conversion_engine,
                    hierarchy_store=hierarchy_store,
                    hierarchy_company_id=hierarchy_company_id,
                    allow_cross_standard_resolution=allow_cross_standard_resolution,
                )
                if cross_standard is not None:
                    return cross_standard
                raise HTTPException(status_code=404, detail=str(e))
        except ContractExecutionError as e:
            if _is_non_executable_contract_error(e):
                cross_standard = await _try_cross_standard_calculation(
                    request,
                    graph=graph,
                    store=store,
                    db=db,
                    resolver=resolver,
                    mapping_store=mapping_store,
                    unit_normalizer=unit_normalizer,
                    conversion_engine=conversion_engine,
                    hierarchy_store=hierarchy_store,
                    hierarchy_company_id=hierarchy_company_id,
                    allow_cross_standard_resolution=allow_cross_standard_resolution,
                )
                if cross_standard is not None:
                    return cross_standard
            raise HTTPException(status_code=422, detail=str(e))
        else:
            return _calculation_response_from_result(
                request,
                result,
                fallback_unit=None,
            )

        if result is not None:
            return _calculation_response_from_result(
                request,
                result,
                fallback_unit=None,
            )

    if not allow_ontology_fallback:
        raise HTTPException(
            status_code=404,
            detail=f"No executable calculation contract for concept: {request.concept}",
        )

    value_provider = _build_bootstrap_scalar_value_provider(store, context)

    try:
        result = engine.calculate_indicator(
            indicator_uri=request.concept,
            context=context,
            ontology_graph=graph,
            value_provider=value_provider,
        )
    except CalculationError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return _calculation_response_from_result(
        request,
        result,
        fallback_unit=_indicator_unit_from_graph(graph, request.concept),
    )


def _is_non_executable_contract_error(exc: ContractExecutionError) -> bool:
    return "not executable" in str(exc).lower()


async def _try_cross_standard_calculation(
    request: CalculationRequest,
    *,
    graph: Graph,
    store,
    db: Optional[Session],
    resolver: Any,
    mapping_store: Optional[Any],
    unit_normalizer: Optional[Any],
    conversion_engine: Optional[Any],
    hierarchy_store: Optional[Any],
    hierarchy_company_id: Optional[str],
    allow_cross_standard_resolution: bool,
) -> Optional[CalculationResponse]:
    if not allow_cross_standard_resolution or mapping_store is None:
        return None

    async def calculate_native_only(calc_request: CalculationRequest):
        if calc_request.concept != request.concept:
            _assert_cross_standard_source_visible(calc_request.concept, resolver)
        return await _run_calculation(
            calc_request,
            graph=graph,
            store=store,
            db=db,
            contract_resolver=resolver,
            allow_ontology_fallback=False,
            unit_normalizer=unit_normalizer,
            conversion_engine=conversion_engine,
            hierarchy_store=hierarchy_store,
            hierarchy_company_id=hierarchy_company_id,
            mapping_store=None,
            allow_cross_standard_resolution=False,
        )

    resolution = await resolve_value_request(
        ValueResolveRequest(
            target_concept=request.concept,
            entity=request.entity,
            period=request.period,
            granularity=request.granularity or TemporalGranularity.ANNUAL,
            target_unit=None,
            include_trace=bool(request.include_trace),
        ),
        value_store=store,
        unit_converter=unit_normalizer,
        mapping_store=mapping_store,
        calculate=calculate_native_only,
    )

    if (
        resolution.status == ValueResolutionStatus.RESOLVED
        and _is_cross_standard_route(resolution)
    ):
        return _calculation_response_from_resolution(request, resolution)
    if resolution.status == ValueResolutionStatus.NOT_FOUND:
        return None
    if resolution.status == ValueResolutionStatus.RESOLVED:
        return None
    raise _calculation_refusal_exception(request, resolution)


def _is_cross_standard_route(resolution: ValueResolveResponse) -> bool:
    return bool(resolution.contract_id) and resolution.method in {
        ValueResolutionMethod.CALCULATION_THEN_MAPPING,
        ValueResolutionMethod.MAPPING_THEN_CONVERSION,
    }


def _assert_cross_standard_source_visible(concept: str, resolver: Any) -> None:
    try:
        contract = resolver.resolve(concept)
    except ContractResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    exposure = getattr(contract, "exposure", None)
    if exposure not in {None, "public_register"}:
        raise _public_calculation_denial()


def _calculation_response_from_resolution(
    request: CalculationRequest,
    resolution: ValueResolveResponse,
) -> CalculationResponse:
    source_value_ids = _source_value_ids_from_resolution(resolution)
    trace = _trace_from_value_resolution(request, resolution, source_value_ids)
    executed_concept = resolution.source_concept or resolution.target_concept
    return CalculationResponse(
        concept=request.concept,
        requested_concept=request.concept,
        returned_framework_object={
            "concept": request.concept,
            "unit": resolution.unit,
            "period": request.period,
            "entity": request.entity,
        },
        executed_concept=executed_concept,
        execution_authority="equivalent_mapping",
        mapping_relationship_type=resolution.relationship_type,
        coverage_status="complete",
        bridge_id=None,
        source_value_ids=source_value_ids,
        unit_policy={"target_unit": resolution.unit},
        unit_conversions=list(resolution.conversion_steps or []),
        aggregation_policy=_aggregation_policy_from_resolution(resolution),
        framework_metadata={
            "requested_concept": request.concept,
            "executed_concept": executed_concept,
        },
        warnings=[],
        entity=request.entity,
        period=request.period,
        value=resolution.value,
        unit=resolution.unit,
        formula_used=_formula_from_resolution(resolution),
        confidence=1.0,
        trace=trace,
        calculated_at=datetime.utcnow(),
    )


def _trace_from_value_resolution(
    request: CalculationRequest,
    resolution: ValueResolveResponse,
    source_value_ids: List[str],
) -> Optional[CalculationTrace]:
    if not request.include_trace:
        return None

    calc_trace = _resolution_calculation_trace(resolution)
    steps = [
        "Resolved through cross-standard equivalent mapping",
        f"requested_concept={request.concept}",
        f"executed_concept={resolution.source_concept or resolution.target_concept}",
        "execution_authority=equivalent_mapping",
        f"mapping_relationship_type={resolution.relationship_type}",
        "source_value_ids=" + ",".join(source_value_ids),
    ]
    if calc_trace:
        steps.extend(str(step) for step in calc_trace.get("steps", []) or [])

    return CalculationTrace(
        calculation_id=str(uuid.uuid4()),
        steps=steps,
        variables_used=list((calc_trace or {}).get("variables_used", []) or []),
        variable_values=dict((calc_trace or {}).get("variable_values", {}) or {}),
        aggregations_applied=list(
            (calc_trace or {}).get("aggregations_applied", []) or []
        ),
        conversions_applied=list(resolution.conversion_steps or []),
        dependencies_resolved=list(
            (calc_trace or {}).get("dependencies_resolved", []) or []
        ),
        warnings=list((calc_trace or {}).get("warnings", []) or []),
        execution_time_ms=(calc_trace or {}).get("execution_time_ms"),
    )


def _resolution_calculation_trace(
    resolution: ValueResolveResponse,
) -> Optional[Dict[str, Any]]:
    if not isinstance(resolution.trace, dict):
        return None
    calculation = resolution.trace.get("calculation")
    return calculation if isinstance(calculation, dict) else None


def _source_value_ids_from_resolution(resolution: ValueResolveResponse) -> List[str]:
    calc_trace = _resolution_calculation_trace(resolution)
    if not calc_trace:
        return [str(resolution.value_id)] if resolution.value_id else []
    for step in calc_trace.get("steps", []) or []:
        text = str(step)
        if text.startswith("source_value_ids="):
            return [
                item.strip()
                for item in text.split("=", 1)[1].split(",")
                if item.strip()
            ]
    return []


def _formula_from_resolution(resolution: ValueResolveResponse) -> Optional[str]:
    calc_trace = _resolution_calculation_trace(resolution)
    if not calc_trace:
        return None
    for step in calc_trace.get("steps", []) or []:
        text = str(step)
        if text.startswith("formula="):
            return text.split("=", 1)[1].strip() or None
    return None


def _aggregation_policy_from_resolution(
    resolution: ValueResolveResponse,
) -> Optional[Dict[str, Any]]:
    calc_trace = _resolution_calculation_trace(resolution)
    aggregations = list((calc_trace or {}).get("aggregations_applied", []) or [])
    return {"applied": aggregations} if aggregations else None


_REFUSAL_MACHINE_CODES = {
    ValueResolutionStatus.NOT_TRANSFORMABLE: "NOT_TRANSFORMABLE",
    ValueResolutionStatus.NOT_ENOUGH_EVIDENCE: "INCOMPLETE_COVERAGE",
    ValueResolutionStatus.INCOMPLETE_COVERAGE: "INCOMPLETE_COVERAGE",
    ValueResolutionStatus.MULTIPLE_TARGETS: "AMBIGUOUS_MAPPING",
    ValueResolutionStatus.AMBIGUOUS_MAPPING: "AMBIGUOUS_MAPPING",
    ValueResolutionStatus.BRIDGE_REQUIRED: "BRIDGE_REQUIRED",
    ValueResolutionStatus.NON_EXECUTABLE_CONTRACT: "NON_EXECUTABLE_CONTRACT",
    ValueResolutionStatus.FRAMEWORK_METADATA_INCOMPLETE: "FRAMEWORK_METADATA_INCOMPLETE",
    ValueResolutionStatus.AGGREGATION_POLICY_UNPROVEN: "AGGREGATION_POLICY_UNPROVEN",
    ValueResolutionStatus.ENTITY_SCOPE_UNPROVEN: "ENTITY_SCOPE_UNPROVEN",
    ValueResolutionStatus.PERIOD_SEMANTICS_UNPROVEN: "PERIOD_SEMANTICS_UNPROVEN",
    ValueResolutionStatus.UNSUPPORTED_CONVERSION: "NOT_TRANSFORMABLE",
    ValueResolutionStatus.INVALID_REQUEST: "NOT_TRANSFORMABLE",
}


def _calculation_refusal_exception(
    request: CalculationRequest,
    resolution: ValueResolveResponse,
) -> HTTPException:
    detail = _calculation_refusal_detail(request, resolution)
    return HTTPException(status_code=422, detail=detail)


def _calculation_refusal_detail(
    request: CalculationRequest,
    resolution: ValueResolveResponse,
) -> Dict[str, Any]:
    relationship_types = sorted(
        {
            str(candidate.get("relationship_type"))
            for candidate in resolution.candidates
            if candidate.get("relationship_type")
        }
    )
    failed_preconditions = _failed_preconditions(resolution, relationship_types)
    return {
        "code": _REFUSAL_MACHINE_CODES.get(
            resolution.status, str(resolution.status).upper()
        ),
        "status": resolution.status.value,
        "message": _calculation_refusal_message(
            request, resolution, relationship_types
        ),
        "requested_concept": request.concept,
        "returned_framework_object": {
            "concept": request.concept,
            "entity": request.entity,
            "period": request.period,
        },
        "execution_authority": "mapping_refusal",
        "coverage_status": _coverage_status(resolution),
        "mapping_relationship_types": relationship_types,
        "candidate_source_concepts": [
            candidate.get("source_concept")
            for candidate in resolution.candidates
            if candidate.get("source_concept")
        ],
        "candidate_target_concepts": [
            candidate.get("target_concept")
            for candidate in resolution.candidates
            if candidate.get("target_concept")
        ],
        "mapping_row_ids": list(resolution.mapping_row_ids),
        "missing_required_inputs": list(resolution.missing_inputs),
        "failed_preconditions": failed_preconditions,
        "required_bridge_status": (
            "missing"
            if "equivalent_relationship_required" in failed_preconditions
            else None
        ),
        "candidates": list(resolution.candidates),
    }


def _failed_preconditions(
    resolution: ValueResolveResponse, relationship_types: List[str]
) -> List[str]:
    if resolution.status == ValueResolutionStatus.NOT_TRANSFORMABLE:
        if any(item != "equivalent" for item in relationship_types):
            return ["equivalent_relationship_required"]
        return ["not_transformable"]
    if resolution.status in {
        ValueResolutionStatus.MULTIPLE_TARGETS,
        ValueResolutionStatus.AMBIGUOUS_MAPPING,
    }:
        return ["single_eligible_route_required"]
    if resolution.status in {
        ValueResolutionStatus.NOT_ENOUGH_EVIDENCE,
        ValueResolutionStatus.INCOMPLETE_COVERAGE,
    }:
        return ["source_value_completeness"]
    if resolution.status == ValueResolutionStatus.UNSUPPORTED_CONVERSION:
        return ["unit_conversion_policy"]
    return [resolution.status.value]


def _coverage_status(resolution: ValueResolveResponse) -> str:
    if resolution.status == ValueResolutionStatus.NOT_TRANSFORMABLE:
        return "not_transformable"
    if resolution.status in {
        ValueResolutionStatus.MULTIPLE_TARGETS,
        ValueResolutionStatus.AMBIGUOUS_MAPPING,
    }:
        return "ambiguous"
    return "incomplete"


def _calculation_refusal_message(
    request: CalculationRequest,
    resolution: ValueResolveResponse,
    relationship_types: List[str],
) -> str:
    candidates = list(resolution.candidates)
    if candidates and relationship_types:
        source_labels = sorted(
            {
                _framework_code(
                    candidate.get("source_standard"), candidate.get("source_code")
                )
                for candidate in candidates
                if candidate.get("source_standard") or candidate.get("source_code")
            }
        )
        target_labels = sorted(
            {
                _framework_code(
                    candidate.get("target_standard"), candidate.get("target_code")
                )
                for candidate in candidates
                if candidate.get("target_standard") or candidate.get("target_code")
            }
        )
        source_text = ", ".join(source_labels) or "the candidate source"
        target_text = ", ".join(target_labels) or request.concept
        return (
            f"{target_text} cannot be calculated from {source_text}: "
            f"mapping relationship {', '.join(relationship_types)} is not "
            "equivalent. A certified bridge contract is required before numeric "
            "transfer."
        )
    reason = ""
    if isinstance(resolution.trace, dict):
        reason = str(resolution.trace.get("reason") or "")
    return (
        reason or f"{request.concept} cannot be calculated from available SDS evidence."
    )


def _framework_code(standard: Any, code: Any) -> str:
    standard_text = str(standard or "").strip().upper()
    code_text = str(code or "").strip()
    if not standard_text:
        return code_text
    if code_text.upper().startswith(standard_text):
        return code_text
    return f"{standard_text} {code_text}".strip()


def _native_coverage_status(engine_trace: Any) -> str:
    completeness = getattr(engine_trace, "completeness", {}) or {}
    return str(completeness.get("status") or "complete")


def _public_aggregation_policy_map(engine_trace: Any) -> Dict[str, str]:
    policies = dict(getattr(engine_trace, "aggregation_policy", {}) or {})
    public_policies: Dict[str, str] = {}
    for name, policy in policies.items():
        policy_text = str(policy)
        if policy_text.startswith("nested_contract="):
            parts = [
                part
                for part in policy_text.split(";")
                if part and not part.startswith("nested_contract=")
            ]
            public_policies[str(name)] = ";".join(parts)
        else:
            public_policies[str(name)] = policy_text
    return public_policies


def _internal_nested_input_concepts(engine_trace: Any) -> set[str]:
    policies = dict(getattr(engine_trace, "aggregation_policy", {}) or {})
    source_variables = list(getattr(engine_trace, "source_variables", []) or [])
    internal: set[str] = set()
    for item in source_variables:
        local_variable = getattr(item, "local_variable", None)
        concept = getattr(item, "concept", None)
        policy = str(policies.get(local_variable, ""))
        if concept and policy.startswith("nested_contract="):
            internal.add(str(concept))
    return internal


def _native_aggregation_policy(engine_trace: Any) -> Optional[Dict[str, Any]]:
    aggregation_policy = _public_aggregation_policy_map(engine_trace)
    return {"applied": aggregation_policy} if aggregation_policy else None


def _native_input_concepts(engine_trace: Any) -> List[str]:
    internal = _internal_nested_input_concepts(engine_trace)
    return [
        str(getattr(item, "concept"))
        for item in list(getattr(engine_trace, "source_variables", []) or [])
        if getattr(item, "concept", None)
        and str(getattr(item, "concept")) not in internal
    ]


def _public_trace_dependencies(engine_trace: Any, dependencies: List[str]) -> List[str]:
    internal = _internal_nested_input_concepts(engine_trace)
    return [dependency for dependency in dependencies if dependency not in internal]


def _input_bridge_used(input_mappings: List[Dict[str, Any]]) -> bool:
    return any(
        _normalize_mapping_relationship(mapping.get("relationship_type"))
        not in _EXACT_MAPPING_RELATIONSHIPS
        for mapping in input_mappings
    )


def _normalize_mapping_relationship(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _calculation_response_from_result(
    request: CalculationRequest,
    result: Any,
    *,
    fallback_unit: Optional[str],
) -> CalculationResponse:
    trace = None
    engine_trace = getattr(result, "trace", None)
    source_value_ids = list(getattr(engine_trace, "source_value_ids", []) or [])
    conversions_applied = list(getattr(engine_trace, "conversions_applied", []) or [])
    warnings = list(getattr(engine_trace, "warnings", []) or [])
    trace_metadata = getattr(engine_trace, "metadata", {}) or {}
    input_mappings = list(trace_metadata.get("input_mappings", []) or [])
    contract_id = getattr(engine_trace, "contract_id", None)
    contract_version = getattr(engine_trace, "contract_version", None)
    contract_hash = getattr(engine_trace, "contract_hash", None)
    resolver_source = getattr(engine_trace, "resolver_source", None)
    input_bridge_used = _input_bridge_used(input_mappings)
    if request.include_trace:
        variable_values = _trace_variable_values(
            getattr(result, "input_values", {}) or {}
        )
        variables_used = list(variable_values.keys()) or list(
            getattr(result, "variables_used", []) or []
        )
        dependencies_resolved = _trace_dependencies(engine_trace) or list(
            getattr(result, "dependencies_resolved", []) or []
        )
        dependencies_resolved = _public_trace_dependencies(
            engine_trace, dependencies_resolved
        )
        formula_steps = list(getattr(engine_trace, "formula_steps", []) or [])
        aggregations_applied = list(
            getattr(engine_trace, "aggregations_applied", []) or []
        )
        if getattr(engine_trace, "contract_hash", None):
            aggregations_applied = [
                f"{name}: {policy}"
                for name, policy in _public_aggregation_policy_map(engine_trace).items()
            ] or aggregations_applied
        elif "calculation_engine" not in aggregations_applied:
            aggregations_applied.append("calculation_engine")
        execution_time_ms = getattr(engine_trace, "execution_time_ms", None)
        steps = [
            f"Resolved indicator: {request.concept}",
            "Applied calculation engine",
            *formula_steps,
            f"Result: {result.value} {result.unit or ''}",
        ]
        contract_hash = getattr(engine_trace, "contract_hash", None)
        if contract_hash:
            steps.extend(
                [
                    f"contract_id={getattr(engine_trace, 'contract_id', '')}",
                    f"contract_hash={contract_hash}",
                    f"contract_version={getattr(engine_trace, 'contract_version', '')}",
                    f"resolver_source={getattr(engine_trace, 'resolver_source', '')}",
                    "source_value_ids="
                    + ",".join(getattr(engine_trace, "source_value_ids", []) or []),
                    "completeness="
                    + str(getattr(engine_trace, "completeness", {}) or {}),
                ]
            )
            for mapping in input_mappings:
                steps.append(
                    "input_mapping="
                    f"{mapping.get('local_variable')}:"
                    f"{mapping.get('requested_concept')}<-"
                    f"{mapping.get('source_concept')}"
                )
            hierarchy_config_id = getattr(engine_trace, "hierarchy_config_id", None)
            if hierarchy_config_id:
                steps.append(f"hierarchy_config_id={hierarchy_config_id}")

        trace = CalculationTrace(
            calculation_id=str(uuid.uuid4()),
            steps=steps,
            variables_used=variables_used,
            variable_values=variable_values,
            aggregations_applied=aggregations_applied,
            conversions_applied=conversions_applied,
            dependencies_resolved=dependencies_resolved,
            warnings=warnings,
            execution_time_ms=(
                execution_time_ms
                if execution_time_ms is not None
                else getattr(result, "execution_time_ms", 50.0)
            ),
        )

    return CalculationResponse(
        concept=request.concept,
        entity=request.entity,
        period=request.period,
        value=result.value,
        unit=result.unit or fallback_unit,
        formula_used=result.formula_used,
        confidence=result.confidence,
        trace=trace,
        calculated_at=result.calculation_timestamp,
        requested_concept=request.concept,
        returned_framework_object={
            "concept": request.concept,
            "unit": result.unit or fallback_unit,
            "period": request.period,
            "entity": request.entity,
        },
        executed_concept=request.concept,
        execution_authority=(
            "certified_bridge"
            if input_bridge_used
            else ("native_contract" if contract_id else "native")
        ),
        coverage_status=_native_coverage_status(engine_trace),
        bridge_id=contract_id if input_bridge_used else None,
        source_value_ids=source_value_ids,
        unit_policy={"target_unit": result.unit or fallback_unit},
        unit_conversions=conversions_applied,
        aggregation_policy=_native_aggregation_policy(engine_trace),
        framework_metadata={
            "requested_concept": request.concept,
            "executed_concept": request.concept,
            "contract_id": contract_id,
            "contract_version": contract_version,
            "contract_hash": contract_hash,
            "resolver_source": resolver_source,
            "formula": result.formula_used,
            "result_unit": result.unit or fallback_unit,
            "input_concepts": _native_input_concepts(engine_trace),
            "input_mappings": input_mappings,
        },
        warnings=warnings,
    )


@router.post(
    "/calculate",
    response_model=CalculationResponse,
    summary="Calculate indicator",
    description="Calculate an ESG indicator with full traceability",
    dependencies=[Depends(require_permission(Permission.EXECUTE_CALCULATIONS))],
)
async def calculate_indicator(
    request: CalculationRequest,
    http_request: FastAPIRequest,
    graph: Graph = Depends(get_ontology_graph),
    store=Depends(get_authenticated_value_store),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> CalculationResponse:
    """
    Calculate an ESG indicator with full traceability.

    - **concept**: Indicator concept URI (e.g., 'urn:sds:disclosure:csrd:e3-5')
    - **entity**: Entity ID for calculation (e.g., 'nh_group', 'nh_es_valencia_plant')
    - **period**: Period identifier (e.g., '2024', '2024-Q1', '2024-01')
    - **granularity**: Temporal granularity (daily, monthly, quarterly, annual)
    - **include_trace**: Whether to include calculation trace information
    """
    try:
        logger.info(
            "Starting indicator calculation",
            concept=request.concept,
            entity=request.entity,
            period=request.period,
            granularity=request.granularity.value,
        )

        # TODO: Validate concept exists in ontology
        # TODO: Validate entity exists in hierarchy

        unit_converter = getattr(http_request.app.state, "unit_converter", None)
        conversion_engine = (
            build_conversion_engine(db, unit_converter)
            if db is not None and unit_converter is not None
            else None
        )
        response = await _run_calculation(
            request,
            graph=graph,
            store=store,
            db=db,
            unit_normalizer=unit_converter,
            conversion_engine=conversion_engine,
            hierarchy_store=hierarchy_store,
            hierarchy_company_id=_hierarchy_scope_company_id(current_user),
            mapping_store=StandardMappingStore(db=db) if db is not None else None,
        )

        logger.info(
            "Indicator calculation completed",
            concept=request.concept,
            entity=request.entity,
            result_value=float(response.value),
            unit=response.unit,
        )

        return response

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(
            "Indicator calculation validation error",
            error=str(e),
            concept=request.concept,
            entity=request.entity,
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Indicator calculation failed",
            error=str(e),
            concept=request.concept,
            entity=request.entity,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/calculate/batch",
    summary="Batch calculate indicators",
    description="Calculate multiple indicators for an entity and period",
    dependencies=[Depends(require_permission(Permission.EXECUTE_CALCULATIONS))],
    response_model=List[BatchCalculationItem],
)
async def batch_calculate_indicators(
    http_request: FastAPIRequest,
    entity: str = Query(..., description="Entity ID"),
    period: str = Query(..., description="Period identifier"),
    concepts: str = Query(..., description="Comma-separated list of concept URIs"),
    granularity: TemporalGranularity = Query(
        TemporalGranularity.ANNUAL, description="Temporal granularity"
    ),
    graph: Graph = Depends(get_ontology_graph),
    store=Depends(get_authenticated_value_store),
    hierarchy_store=Depends(get_hierarchy_store),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> List[BatchCalculationItem]:
    """
    Calculate multiple indicators in batch.

    - **entity**: Entity ID for calculations
    - **period**: Period identifier
    - **concepts**: Comma-separated concept URIs (e.g., 'urn:sds:disclosure:csrd:e3-5,gri:303-5.c')
    - **granularity**: Temporal granularity
    """
    try:
        logger.info(
            "Starting batch calculation",
            entity=entity,
            period=period,
            concepts=concepts,
            granularity=granularity.value,
        )

        concept_list = [c.strip() for c in concepts.split(",")]
        results: List[BatchCalculationItem] = []
        unit_converter = getattr(http_request.app.state, "unit_converter", None)
        conversion_engine = (
            build_conversion_engine(db, unit_converter)
            if db is not None and unit_converter is not None
            else None
        )

        for concept in concept_list:
            try:
                request = CalculationRequest(
                    concept=concept,
                    entity=entity,
                    period=period,
                    granularity=granularity,
                    include_trace=False,  # Skip trace for batch to improve performance
                )

                result = await _run_calculation(
                    request,
                    graph=graph,
                    store=store,
                    db=db,
                    unit_normalizer=unit_converter,
                    conversion_engine=conversion_engine,
                    hierarchy_store=hierarchy_store,
                    hierarchy_company_id=_hierarchy_scope_company_id(current_user),
                    mapping_store=(
                        StandardMappingStore(db=db) if db is not None else None
                    ),
                )
                results.append(
                    BatchCalculationItem(
                        concept=concept,
                        status=BatchCalculationStatus.SUCCESS,
                        result=result,
                        error=None,
                    )
                )

            except Exception as e:
                logger.warning(
                    "Failed to calculate concept in batch",
                    concept=concept,
                    error=str(e),
                )
                results.append(
                    BatchCalculationItem(
                        concept=concept,
                        status=BatchCalculationStatus.FAILED,
                        result=None,
                        error=_batch_error_message(e),
                    )
                )

        logger.info(
            "Batch calculation completed",
            entity=entity,
            requested_concepts=len(concept_list),
            successful_calculations=sum(
                1 for item in results if item.status == BatchCalculationStatus.SUCCESS
            ),
        )

        return results

    except Exception as e:
        logger.error("Batch calculation failed", error=str(e), entity=entity)
        raise HTTPException(
            status_code=500, detail=f"Batch calculation failed: {str(e)}"
        )


@router.get(
    "/calculate/dependencies/{concept}",
    summary="Get calculation dependencies",
    description="Get the dependencies (variables) required to calculate an indicator",
    dependencies=[Depends(require_permission(Permission.QUERY_ONTOLOGY))],
)
async def get_calculation_dependencies(
    concept: str,
    graph: Graph = Depends(get_ontology_graph),
    db: Optional[Session] = Depends(get_db_optional),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """
    Get calculation dependencies for a concept.

    - **concept**: Concept URI to analyze
    """
    try:
        logger.info("Getting calculation dependencies", concept=concept)

        if settings.require_database:
            try:
                contract = RuntimeCalculationContractResolver(db=db).resolve(concept)
            except ContractResolutionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            # Public read (QUERY_ONTOLOGY): only a publicly-registered calculation
            # contract may expose its formula/variables here. A non-public canonical
            # contract (runtime_support/container/audit_only) is hidden — 404 — to match
            # the concept-detail public_register gate (codex G-AUDIT M2). Projected
            # semantic-formula contracts carry exposure=None and remain public.
            exposure = getattr(contract, "exposure", None)
            if exposure is not None and exposure != "public_register":
                raise HTTPException(
                    status_code=404,
                    detail=f"No public calculation dependencies for concept: {concept}",
                )

            dependencies = _dependencies_from_contract(concept, contract)
            logger.info(
                "Dependencies retrieved from calculation contract",
                concept=concept,
                variable_count=dependencies["variable_count"],
            )
            return dependencies

        formula = _indicator_formula_expression_from_graph(graph, concept)
        variables = _indicator_variables_from_graph(graph, concept)
        if not variables and not formula:
            raise HTTPException(
                status_code=404, detail=f"No dependencies found for concept: {concept}"
            )

        dependencies = {
            "concept": concept,
            "formula": formula,
            "required_variables": variables,
            "variable_count": len(variables),
            "unit": _indicator_unit_from_graph(graph, concept),
            "calculation_type": _get_concept_calculation_type(concept),
            "dependency_source": "ontology_graph",
        }

        logger.info(
            "Dependencies retrieved", concept=concept, variable_count=len(variables)
        )

        return dependencies

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get dependencies", error=str(e), concept=concept)
        raise HTTPException(
            status_code=500, detail=f"Failed to get dependencies: {str(e)}"
        )


def _parse_period(
    period: str, granularity: TemporalGranularity = TemporalGranularity.ANNUAL
) -> tuple[date, date]:
    """Parse period string to date range based on granularity.

    Raises:
        ValueError: If the period string cannot be parsed or contains invalid values.
    """
    try:
        if granularity == TemporalGranularity.ANNUAL:
            year = int(period)
            return date(year, 1, 1), date(year, 12, 31)
        elif granularity == TemporalGranularity.QUARTERLY:
            # Format: 2024-Q1
            year, quarter = period.split("-Q")
            year = int(year)
            quarter = int(quarter)

            if quarter < 1 or quarter > 4:
                raise ValueError(f"Invalid quarter: {quarter} (must be 1-4)")

            if quarter == 1:
                return date(year, 1, 1), date(year, 3, 31)
            elif quarter == 2:
                return date(year, 4, 1), date(year, 6, 30)
            elif quarter == 3:
                return date(year, 7, 1), date(year, 9, 30)
            else:
                return date(year, 10, 1), date(year, 12, 31)
        elif granularity == TemporalGranularity.MONTHLY:
            # Format: 2024-01
            year, month = period.split("-")
            year, month = int(year), int(month)

            if month < 1 or month > 12:
                raise ValueError(f"Invalid month: {month} (must be 1-12)")

            import calendar

            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, 1), date(year, month, last_day)
        else:
            # Default to annual
            year = int(period.split("-")[0])
            return date(year, 1, 1), date(year, 12, 31)

    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid period format: {period}") from e


def _get_entity_level(
    entity: str,
    *,
    hierarchy_store: Optional[Any] = None,
    company_id: Optional[str] = None,
) -> int:
    """Get organizational level for entity."""
    if hierarchy_store is None:
        return 0

    configs, _ = hierarchy_store.list(
        company_id=company_id,
        hierarchy_type="organizational",
        active=True,
        limit=1000,
        offset=0,
    )
    if not configs:
        return 0

    for config in configs:
        for level in config.levels:
            if level.id == entity:
                return int(level.level)

    raise ValueError(f"Unknown entity: {entity}")


def _get_concept_calculation_type(concept: str) -> str:
    """Get calculation type for a concept."""
    if (
        "water" in concept.lower()
        or concept == CSRD_E3_5_DISCLOSURE_URI
        or "E3_5" in concept
        or "303_5" in concept
    ):
        return "consumption"
    elif "emission" in concept.lower() or "E1_1" in concept or "305_1" in concept:
        return "emission"
    else:
        return "general"
