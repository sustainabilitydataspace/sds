"""Runtime calculation contracts and resolver seam."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Protocol

from src.calculation.semantic_formula import local_name, normalize_formula_expression
from src.concept_runtime_aliases import concept_uri_candidates


class ContractResolutionError(Exception):
    """Raised when a runtime calculation contract cannot be resolved safely."""


class ContractExecutionError(Exception):
    """Raised when an imported contract cannot be executed safely."""


HIDDEN_PUBLIC_TARGET_EXPOSURES = frozenset(
    {"runtime_support", "audit_only", "container"}
)


def contract_exposure(contract: Any) -> Optional[str]:
    """Return normalized calculation contract exposure."""

    exposure = getattr(contract, "exposure", None)
    if exposure is None:
        return None
    normalized = str(exposure).strip().lower()
    return normalized or None


def is_public_calculation_target(contract: Any) -> bool:
    """Whether a contract may be targeted by public calculation/value surfaces."""

    return contract_exposure(contract) not in HIDDEN_PUBLIC_TARGET_EXPOSURES


class ContractRepository(Protocol):
    """Minimal repository seam used by the runtime resolver."""

    def get_contract_for_concept(self, concept: str) -> Any:
        """Return the best contract row for a concept, or None."""


@dataclass(frozen=True)
class CalculationContractInput:
    """Single imported input binding for a runtime calculation contract."""

    local_variable: str
    concept: str
    unit: Optional[str] = None
    required: bool = True
    value_type: str = "numeric"
    temporal_aggregation: str = "sum"
    perimeter_aggregation: str = "sum"
    entity_scope: str = "self"
    default_value: Optional[Decimal] = None
    currency: Optional[str] = None
    expected_currency: Optional[str] = None
    conversion_policy: str = "fail_closed"
    fx_policy_id: Optional[str] = None
    allowed_mapping_relationships: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        object.__setattr__(
            self, "temporal_aggregation", self.temporal_aggregation.lower()
        )
        object.__setattr__(
            self, "perimeter_aggregation", self.perimeter_aggregation.lower()
        )
        object.__setattr__(self, "entity_scope", self.entity_scope.lower())
        object.__setattr__(
            self, "currency", _normalize_currency(self.currency, "currency")
        )
        object.__setattr__(
            self,
            "expected_currency",
            _normalize_currency(self.expected_currency, "expected_currency"),
        )
        object.__setattr__(
            self,
            "conversion_policy",
            _normalize_conversion_policy(self.conversion_policy),
        )
        fx_policy_id = _optional_clean_string(self.fx_policy_id)
        object.__setattr__(self, "fx_policy_id", fx_policy_id)
        relationships = tuple(
            _normalize_relationship_token(item)
            for item in _as_sequence(self.allowed_mapping_relationships)
            if _normalize_relationship_token(item)
        )
        object.__setattr__(self, "allowed_mapping_relationships", relationships)
        if self.expected_currency and not fx_policy_id:
            raise ValueError("expected_currency requires fx_policy_id")
        if fx_policy_id and not self.expected_currency:
            raise ValueError("fx_policy_id requires expected_currency")
        if self.default_value is not None and not isinstance(
            self.default_value, Decimal
        ):
            object.__setattr__(self, "default_value", Decimal(str(self.default_value)))


@dataclass(frozen=True)
class DerivedCalculationNode:
    """Derived local variable inside a typed calculation DAG."""

    local_variable: str
    expression: str
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        object.__setattr__(self, "dependencies", tuple(self.dependencies or ()))


@dataclass(frozen=True)
class ContractResolutionMetadata:
    """How a contract was resolved for execution."""

    source: str = "canonical_db"
    row_id: Optional[str] = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CalculationContract:
    """Imported, runtime-ready calculation contract."""

    contract_id: str
    concept: str
    contract_version: str
    contract_hash: str
    runtime_status: str
    formula: str
    inputs: tuple[CalculationContractInput, ...]
    result_unit: Optional[str] = None
    result_currency: Optional[str] = None
    conversion_policy: str = "fail_closed"
    #: Contract-level missing-data policy (from the node's aggregation policy).
    #: "BLOCK" means ANY missing input fails the calculation closed, even an
    #: input marked required=false (codex F07 M1).
    missing_data: Optional[str] = None
    #: Canonical contract exposure (public_register / runtime_support / container /
    #: audit_only); None for projected-formula contracts. Public read surfaces must
    #: only expose public_register contracts (codex G-AUDIT M2).
    exposure: Optional[str] = None
    derived_nodes: tuple[DerivedCalculationNode, ...] = field(default_factory=tuple)
    hierarchy_config_id: Optional[str] = None
    hierarchy: dict[str, list[str]] = field(default_factory=dict)
    resolution: ContractResolutionMetadata = field(
        default_factory=ContractResolutionMetadata
    )

    def __post_init__(self):
        object.__setattr__(self, "inputs", tuple(self.inputs or ()))
        object.__setattr__(self, "derived_nodes", tuple(self.derived_nodes or ()))
        object.__setattr__(
            self, "runtime_status", (self.runtime_status or "").strip().lower()
        )
        object.__setattr__(
            self,
            "result_currency",
            _normalize_currency(self.result_currency, "result_currency"),
        )
        object.__setattr__(
            self,
            "conversion_policy",
            _normalize_conversion_policy(self.conversion_policy),
        )
        object.__setattr__(
            self,
            "missing_data",
            (self.missing_data or "").strip().upper() or None,
        )
        validate_contract_shape(self)

    @property
    def is_executable(self) -> bool:
        return self.runtime_status == "executable"

    @property
    def resolver_source(self) -> str:
        return self.resolution.source


class RuntimeCalculationContractResolver:
    """Resolve canonical runtime calculation contracts from a DB/repository seam."""

    def __init__(
        self,
        *,
        repository: Optional[ContractRepository] = None,
        db: Any = None,
    ):
        self.repository = repository or _DBCalculationContractRepository(db)

    def resolve(self, concept: str) -> CalculationContract:
        row = self.repository.get_contract_for_concept(concept)
        if row is None:
            raise ContractResolutionError(
                f"No calculation contract for concept: {concept}"
            )
        try:
            contract = contract_from_row(row, requested_concept=concept)
        except ContractResolutionError:
            raise
        except Exception as exc:
            raise ContractResolutionError(
                f"Invalid calculation contract for concept {concept}: {exc}"
            ) from exc
        return contract


def validate_contract_shape(contract: CalculationContract) -> None:
    """Fail closed on malformed contract records before runtime execution."""

    required_fields = {
        "contract_id": contract.contract_id,
        "concept": contract.concept,
        "contract_version": contract.contract_version,
        "contract_hash": contract.contract_hash,
        "runtime_status": contract.runtime_status,
        "formula": contract.formula,
    }
    missing = [name for name, value in required_fields.items() if not value]
    if missing:
        raise ContractResolutionError(
            "Invalid calculation contract missing fields: " + ", ".join(missing)
        )
    if not contract.inputs:
        raise ContractResolutionError("Invalid calculation contract has no inputs")

    seen: set[str] = set()
    for local_variable in [
        *(item.local_variable for item in contract.inputs),
        *(node.local_variable for node in contract.derived_nodes),
    ]:
        if not local_variable or not local_variable.isidentifier():
            raise ContractResolutionError(
                f"Invalid local variable name: {local_variable!r}"
            )
        if local_variable in seen:
            raise ContractResolutionError(
                f"Duplicate local variable in calculation contract: {local_variable}"
            )
        seen.add(local_variable)


def contract_from_row(row: Any, *, requested_concept: str) -> CalculationContract:
    """Build a runtime contract from a SQLAlchemy row, dataclass, or mapping."""

    if _is_canonical_calculation_contract(row):
        return _contract_from_canonical_row(row, requested_concept=requested_concept)

    inputs = tuple(
        _input_from_row(item) for item in _as_sequence(_field(row, "inputs"))
    )
    derived_nodes = tuple(
        _node_from_row(item)
        for item in _as_sequence(_field(row, "derived_nodes", "nodes", default=[]))
    )

    hierarchy = _field(row, "hierarchy", "hierarchy_config", default={}) or {}
    if isinstance(hierarchy, str):
        hierarchy = _json_loads(hierarchy, default={})

    return CalculationContract(
        contract_id=str(_field(row, "contract_id", "id")),
        concept=str(_field(row, "concept", "concept_uri", default=requested_concept)),
        contract_version=str(_field(row, "contract_version", "version")),
        contract_hash=str(_field(row, "contract_hash", "hash", "source_hash")),
        runtime_status=str(_field(row, "runtime_status", "status")),
        formula=str(_field(row, "formula", "formula_expression", "expression")),
        result_unit=_field(row, "result_unit", "output_unit", "unit", default=None),
        result_currency=_field(row, "result_currency", "currency", default=None),
        conversion_policy=_field(row, "conversion_policy", default="fail_closed"),
        missing_data=_field(row, "missing_data", default=None),
        inputs=inputs,
        derived_nodes=derived_nodes,
        hierarchy_config_id=_field(row, "hierarchy_config_id", default=None),
        hierarchy={str(k): list(v) for k, v in dict(hierarchy).items()},
        resolution=ContractResolutionMetadata(
            source=_field(row, "resolver_source", default="canonical_db"),
            row_id=str(_field(row, "id", "contract_id", default="")),
        ),
    )


def _is_canonical_calculation_contract(row: Any) -> bool:
    return all(
        hasattr(row, attr)
        for attr in (
            "node_id",
            "runtime_expression",
            "aggregation_policy",
            "components",
        )
    )


def _contract_from_canonical_row(
    row: Any, *, requested_concept: str
) -> CalculationContract:
    formula = _runtime_formula_from_canonical_row(row)
    components = sorted(
        _as_sequence(_field(row, "components", default=[])),
        key=lambda item: int(_field(item, "component_order", default=0) or 0),
    )

    if components:
        derived_nodes = _derived_nodes_from_canonical_row(row)
        derived_node_names = {node.local_variable for node in derived_nodes}
        local_variables = [
            name
            for name in _ordered_formula_variables(formula)
            if name not in derived_node_names
        ]
        inputs = tuple(
            _input_from_canonical_component(
                component,
                index=index,
                local_variables=local_variables,
            )
            for index, component in enumerate(components)
        )
    else:
        derived_nodes = _derived_nodes_from_canonical_row(row)
        inputs = (_self_input_from_canonical_row(row, formula=formula),)

    hierarchy = _field(row, "hierarchy", "hierarchy_config", default={}) or {}
    if isinstance(hierarchy, str):
        hierarchy = _json_loads(hierarchy, default={})

    node_aggregation_policy = _field(row, "aggregation_policy", default={}) or {}
    if isinstance(node_aggregation_policy, str):
        node_aggregation_policy = _json_loads(node_aggregation_policy, default={})
    contract_missing_data = (
        node_aggregation_policy.get("missing_data")
        if isinstance(node_aggregation_policy, Mapping)
        else None
    )

    return CalculationContract(
        contract_id=str(_field(row, "node_id", "canonical_datapoint_id", "id")),
        concept=str(
            _field(
                row,
                "indicator_identifier",
                "canonical_datapoint_id",
                "node_id",
                default=requested_concept,
            )
        ),
        contract_version=str(_field(row, "contract_version")),
        contract_hash=str(_field(row, "contract_hash")),
        runtime_status=str(_field(row, "runtime_status")),
        formula=formula,
        result_unit=_field(row, "unit_name", "unit", "result_unit", default=None),
        result_currency=_field(row, "result_currency", default=None),
        conversion_policy=_field(row, "conversion_policy", default="fail_closed"),
        missing_data=contract_missing_data,
        exposure=_field(row, "exposure", default=None),
        inputs=inputs,
        derived_nodes=derived_nodes,
        hierarchy_config_id=_field(row, "hierarchy_config_id", default=None),
        hierarchy={str(k): list(v) for k, v in dict(hierarchy).items()},
        resolution=ContractResolutionMetadata(
            source="canonical_calculation_contracts",
            row_id=str(_field(row, "id", "node_id", default="")),
        ),
    )


def _derived_nodes_from_canonical_row(row: Any) -> tuple[DerivedCalculationNode, ...]:
    source_payload = _field(row, "source_payload", default={}) or {}
    if not isinstance(source_payload, Mapping):
        return ()

    formula_payload = source_payload.get("formula") or {}
    if not isinstance(formula_payload, Mapping):
        formula_payload = {}

    candidate_sets = [
        formula_payload.get("derived_nodes"),
        formula_payload.get("derived"),
        formula_payload.get("intermediate_nodes"),
        source_payload.get("derived_nodes"),
        source_payload.get("derived"),
        source_payload.get("intermediate_nodes"),
    ]
    derived_nodes: list[DerivedCalculationNode] = []
    for candidate_set in candidate_sets:
        for item in _as_sequence(candidate_set):
            if isinstance(item, Mapping):
                derived_nodes.append(_node_from_row(item))
    return tuple(derived_nodes)


def _runtime_formula_from_canonical_row(row: Any) -> str:
    raw = _field(
        row,
        "runtime_expression",
        "semantic_expression",
        "formula",
        "formula_expression",
        default=None,
    )
    normalized = normalize_formula_expression(raw)
    if normalized:
        return normalized

    formula_kind = str(_field(row, "formula_kind", default="input") or "input").lower()
    if formula_kind in {"input", "constant", "source"}:
        return _canonical_self_local_variable(row)

    raise ContractResolutionError(
        f"Canonical calculation contract {getattr(row, 'node_id', '')} has no runtime expression"
    )


def _input_from_canonical_component(
    component: Any,
    *,
    index: int,
    local_variables: list[str],
) -> CalculationContractInput:
    source_payload = _field(component, "source_payload", default={}) or {}
    if not isinstance(source_payload, Mapping):
        source_payload = {}

    local_variable = (
        source_payload.get("local_variable")
        or source_payload.get("runtime_variable")
        or (local_variables[index] if index < len(local_variables) else None)
        or local_name(_field(component, "variable_uri", default=None))
        or local_name(_field(component, "component_id", default=None))
        or local_name(_field(component, "component_node_id", default=None))
    )
    concept = (
        _field(component, "indicator_identifier", default=None)
        or _field(component, "variable_uri", default=None)
        or _field(component, "component_id", default=None)
        or _field(component, "component_node_id", default=None)
    )

    temporal_rule = _supported_aggregation_rule(
        _field(component, "aggregation_method", default=None),
        default="sum",
    )
    perimeter_rule = _supported_aggregation_rule(
        source_payload.get("perimeter_aggregation")
        or source_payload.get("perimeter")
        or source_payload.get("aggregation_perimeter"),
        default="sum",
    )

    return CalculationContractInput(
        local_variable=str(local_variable),
        concept=str(concept),
        unit=_field(component, "unit_name", "unit", default=None),
        required=bool(_field(component, "required", default=True)),
        value_type=str(_field(component, "value_type", default="numeric")),
        temporal_aggregation=temporal_rule,
        perimeter_aggregation=perimeter_rule,
        entity_scope=str(source_payload.get("entity_scope") or "self"),
        default_value=source_payload.get("default_value"),
        currency=_field(component, "currency", default=source_payload.get("currency")),
        expected_currency=_field(
            component,
            "expected_currency",
            default=source_payload.get("expected_currency"),
        ),
        conversion_policy=_field(
            component,
            "conversion_policy",
            default=source_payload.get("conversion_policy") or "fail_closed",
        ),
        fx_policy_id=_field(
            component, "fx_policy_id", default=source_payload.get("fx_policy_id")
        ),
        allowed_mapping_relationships=_field(
            component,
            "allowed_mapping_relationships",
            "mapping_relationships",
            default=source_payload.get("allowed_mapping_relationships")
            or source_payload.get("mapping_relationships")
            or source_payload.get("allowed_relationships")
            or (),
        ),
    )


def _self_input_from_canonical_row(
    row: Any, *, formula: str
) -> CalculationContractInput:
    aggregation_policy = _field(row, "aggregation_policy", default={}) or {}
    if isinstance(aggregation_policy, str):
        aggregation_policy = _json_loads(aggregation_policy, default={})
    if not isinstance(aggregation_policy, Mapping):
        aggregation_policy = {}

    source_payload = _field(row, "source_payload", default={}) or {}
    if not isinstance(source_payload, Mapping):
        source_payload = {}

    local_variable = _ordered_formula_variables(formula)
    concept = (
        _field(row, "canonical_datapoint_id", default=None)
        or _field(row, "indicator_identifier", default=None)
        or _field(row, "node_id")
    )

    return CalculationContractInput(
        local_variable=(
            local_variable[0] if local_variable else _canonical_self_local_variable(row)
        ),
        concept=str(concept),
        unit=_field(row, "unit_name", "unit", default=None),
        required=True,
        value_type=str(_field(row, "value_kind", default="numeric") or "numeric"),
        temporal_aggregation=_supported_aggregation_rule(
            aggregation_policy.get("temporal"), default="sum"
        ),
        perimeter_aggregation=_supported_aggregation_rule(
            aggregation_policy.get("perimeter"), default="sum"
        ),
        entity_scope=str(
            aggregation_policy.get("entity_scope")
            or source_payload.get("entity_scope")
            or "self"
        ),
        default_value=source_payload.get("default_value"),
        currency=source_payload.get("currency"),
        expected_currency=source_payload.get("expected_currency"),
        conversion_policy=source_payload.get("conversion_policy") or "fail_closed",
        fx_policy_id=source_payload.get("fx_policy_id"),
        allowed_mapping_relationships=source_payload.get(
            "allowed_mapping_relationships"
        )
        or source_payload.get("mapping_relationships")
        or source_payload.get("allowed_relationships")
        or (),
    )


def _canonical_self_local_variable(row: Any) -> str:
    return (
        local_name(_field(row, "canonical_datapoint_id", default=None))
        or local_name(_field(row, "indicator_identifier", default=None))
        or local_name(_field(row, "node_id", default=None))
        or "value"
    )


def _ordered_formula_variables(expression: str) -> list[str]:
    names = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression or "")
    safe_names = {
        "abs",
        "round",
        "min",
        "max",
        "sum",
        "len",
        "sqrt",
        "pow",
        "log",
        "log10",
        "exp",
        "sin",
        "cos",
        "tan",
        "weighted_average",
    }
    ordered: list[str] = []
    for name in names:
        if name in safe_names or name in ordered:
            continue
        ordered.append(name)
    return ordered


def _supported_aggregation_rule(value: Any, *, default: str) -> str:
    if value is None or str(value).strip() == "":
        return default
    token = str(value).strip().lower()
    mapping = {
        "sum": "sum",
        "add": "sum",
        "total": "sum",
        "average": "average",
        "avg": "average",
        "average_over_period": "average",
        "last": "last",
        "last_value": "last",
        "first": "first",
        "first_value": "first",
        "min": "min",
        "max": "max",
        "count": "count",
        "weighted_average": "weighted_average",
        "weighted_avg": "weighted_average",
        "none": "none",
    }
    try:
        return mapping[token]
    except KeyError as exc:
        raise ContractResolutionError(f"unsupported aggregation rule: {token}") from exc


def _input_from_row(row: Any) -> CalculationContractInput:
    return CalculationContractInput(
        local_variable=str(
            _field(row, "local_variable", "local_name", "variable", "name")
        ),
        concept=str(
            _field(row, "concept", "source_concept", "variable_concept", "concept_uri")
        ),
        unit=_field(row, "unit", "target_unit", default=None),
        required=bool(_field(row, "required", default=True)),
        value_type=str(_field(row, "value_type", default="numeric")),
        temporal_aggregation=str(
            _field(row, "temporal_aggregation", "temporal_rule", default="sum")
        ),
        perimeter_aggregation=str(
            _field(row, "perimeter_aggregation", "perimeter_rule", default="sum")
        ),
        entity_scope=str(_field(row, "entity_scope", default="self")),
        default_value=_field(row, "default_value", default=None),
        currency=_field(row, "currency", default=None),
        expected_currency=_field(row, "expected_currency", default=None),
        conversion_policy=_field(row, "conversion_policy", default="fail_closed"),
        fx_policy_id=_field(row, "fx_policy_id", default=None),
        allowed_mapping_relationships=_field(
            row,
            "allowed_mapping_relationships",
            "mapping_relationships",
            "allowed_relationships",
            default=(),
        ),
    )


def _node_from_row(row: Any) -> DerivedCalculationNode:
    return DerivedCalculationNode(
        local_variable=str(
            _field(row, "local_variable", "local_name", "variable", "name")
        ),
        expression=str(
            _field(
                row, "expression", "runtime_expression", "formula", "formula_expression"
            )
        ),
        dependencies=tuple(
            str(item) for item in _as_sequence(_field(row, "dependencies", default=[]))
        ),
    )


def _as_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        value = _json_loads(value, default=[])
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _field(row: Any, *names: str, default: Any = ...):
    for name in names:
        if isinstance(row, Mapping) and name in row:
            return row[name]
        if hasattr(row, name):
            value = getattr(row, name)
            if value is not None:
                return value
    if default is not ...:
        return default
    raise ContractResolutionError(f"Contract row missing field: {names[0]}")


def _json_loads(value: str, *, default: Any):
    try:
        return json.loads(value)
    except Exception:
        return default


def _optional_clean_string(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _normalize_currency(value: Any, field_name: str) -> str | None:
    stripped = _optional_clean_string(value)
    if stripped is None:
        return None
    normalized = stripped.upper()
    if not re.fullmatch(r"[A-Z]{3}", normalized):
        raise ValueError(f"invalid currency code for {field_name}: {value!r}")
    return normalized


def _normalize_conversion_policy(value: Any) -> str:
    policy = (_optional_clean_string(value) or "fail_closed").lower()
    if policy != "fail_closed":
        raise ValueError(f"unsupported conversion_policy: {policy}")
    return policy


def _normalize_relationship_token(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "same_as": "equivalent",
        "sameas": "equivalent",
        "exact": "equivalent",
        "exact_match": "equivalent",
        "exactmatch": "equivalent",
        "close_match": "partial",
        "closematch": "partial",
        "overlap": "partial",
        "overlaps": "partial",
        "broad_match": "broader",
        "broadmatch": "broader",
        "narrow_match": "narrower",
        "narrowmatch": "narrower",
    }
    normalized = aliases.get(normalized, normalized)
    return re.sub(r"[^a-z0-9]+", "", normalized)


class _DBCalculationContractRepository:
    """Best-effort adapter; activates only when schema models exist."""

    def __init__(self, db: Any):
        self.db = db

    def get_contract_for_concept(self, concept: str) -> Any:
        if self.db is None:
            return None

        candidates = _concept_uri_candidates(concept)
        model = self._model()
        if model is None:
            return self._semantic_formula_contract_for_concept(concept)

        query = self.db.query(model)
        if getattr(model, "__name__", "") == "CanonicalCalculationContract":
            if hasattr(model, "is_active"):
                query = query.filter(getattr(model, "is_active").is_(True))
            lookup_attrs = (
                "indicator_identifier",
                "canonical_datapoint_id",
                "node_id",
                "indicator_id",
            )
            conditions = []
            for attr in lookup_attrs:
                if hasattr(model, attr):
                    conditions.extend(
                        _column_candidate_conditions(getattr(model, attr), candidates)
                    )
            if conditions:
                try:
                    from sqlalchemy import or_

                    query = query.filter(or_(*conditions))
                except Exception:
                    query = query.filter(*conditions)
        else:
            for attr in ("concept", "concept_uri", "target_concept"):
                if hasattr(model, attr):
                    conditions = _column_candidate_conditions(
                        getattr(model, attr), candidates
                    )
                    try:
                        from sqlalchemy import or_

                        query = query.filter(or_(*conditions))
                    except Exception:
                        query = query.filter(*conditions)
                    break

        for attr in ("effective_from", "created_at", "updated_at"):
            if hasattr(model, attr):
                query = query.order_by(getattr(model, attr).desc())
        if getattr(model, "__name__", "") == "CanonicalCalculationContract":
            rows = _query_rows(query)
            for row in rows:
                if (
                    str(_field(row, "runtime_status", default="")).lower()
                    == "executable"
                ):
                    return row
            semantic_contract = self._semantic_formula_contract_for_concept(concept)
            if semantic_contract is not None:
                return semantic_contract
            if rows:
                return rows[0]
            return None

        row = query.first()
        if row is not None:
            return row
        return self._semantic_formula_contract_for_concept(concept)

    def _semantic_formula_contract_for_concept(self, concept: str) -> Any:
        try:
            from sqlalchemy import or_
            from sqlalchemy.orm import joinedload

            from src.database import models
        except Exception:
            return None

        candidates = _concept_uri_candidates(concept)
        try:
            query = (
                self.db.query(models.Concept)
                .options(
                    joinedload(models.Concept.formulas),
                    joinedload(models.Concept.variables),
                )
                .filter(or_(*(models.Concept.uri == item for item in candidates)))
            )
            concept_rows = _semantic_rows_in_candidate_order(query, candidates)
        except Exception:
            return None

        for concept_row in concept_rows:
            contract = _semantic_formula_contract_from_concept_row(
                self.db, concept_row, concept
            )
            if contract is not None:
                return contract
        return None

    @staticmethod
    def _model():
        try:
            from src.database import models
        except Exception:
            return None
        for name in (
            "CanonicalCalculationContract",
            "CalculationContract",
            "CalculationContractRecord",
            "IndicatorCalculationContract",
        ):
            model = getattr(models, name, None)
            if model is not None:
                return model
        return None


def _concept_uri_candidates(concept: str) -> list[str]:
    return concept_uri_candidates(concept)


def _column_candidate_conditions(column: Any, candidates: list[str]) -> list[Any]:
    if not candidates:
        return []
    if hasattr(column, "in_"):
        try:
            return [column.in_(candidates)]
        except Exception:
            pass
    return [column == candidate for candidate in candidates]


def _semantic_rows_in_candidate_order(query: Any, candidates: list[str]) -> list[Any]:
    if hasattr(query, "all"):
        rows = [row for row in query.all() if row is not None]
    else:
        row = query.first()
        rows = [row] if row is not None else []
    rows_by_uri = {getattr(row, "uri", None): row for row in rows}
    ordered = [rows_by_uri[uri] for uri in candidates if uri in rows_by_uri]
    ordered.extend(row for row in rows if row not in ordered)
    return ordered


def _query_rows(query: Any) -> list[Any]:
    if hasattr(query, "all"):
        return [row for row in query.all() if row is not None]
    row = query.first()
    return [row] if row is not None else []


def _semantic_formula_contract_from_concept_row(
    db: Any, concept_row: Any, requested_concept: str
) -> Any:
    formulas = [
        item
        for item in _as_sequence(getattr(concept_row, "formulas", []))
        if bool(getattr(item, "is_active", True))
    ]
    formulas.sort(
        key=lambda item: (
            int(getattr(item, "version", 0) or 0),
            int(getattr(item, "id", 0) or 0),
        ),
        reverse=True,
    )
    formula = normalize_formula_expression(
        getattr(formulas[0], "expression", None) if formulas else None
    )
    variables = sorted(
        _as_sequence(getattr(concept_row, "variables", [])),
        key=lambda item: int(getattr(item, "ordering", 0) or 0),
    )
    if not formula or not variables:
        return None

    result_unit = _semantic_unit_to_runtime_unit(getattr(concept_row, "unit", None))
    inputs = [
        {
            "local_variable": local_name(getattr(variable, "variable_uri", None)),
            "concept": getattr(variable, "variable_uri", None),
            "unit": _semantic_input_unit(db, variable, result_unit),
            "temporal_aggregation": getattr(variable, "aggregation_method", None)
            or "SUM",
        }
        for variable in variables
    ]
    if result_unit is None and len(inputs) == 1:
        result_unit = inputs[0].get("unit")
    contract_material = {
        "uri": getattr(concept_row, "uri", requested_concept),
        "formula": formula,
        "inputs": inputs,
    }
    contract_hash = hashlib.sha256(
        json.dumps(contract_material, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "contract_id": f"semantic-db:{getattr(concept_row, 'id', requested_concept)}",
        "concept": requested_concept,
        "contract_version": "semantic-db",
        "contract_hash": contract_hash,
        "runtime_status": "executable",
        "formula": formula,
        "result_unit": result_unit,
        "inputs": inputs,
        "resolver_source": "semantic_db",
    }


def _semantic_unit_to_runtime_unit(unit: Any) -> str | None:
    if unit is None:
        return None
    token = str(unit).strip()
    mapping = {
        "sds:CubicMeter": "m3",
        "sds:CubicMetre": "m3",
        "sds:Liter": "L",
        "sds:Litre": "L",
        "sds:Megaliter": "ML",
        "sds:Megalitre": "ML",
        "sds:KilowattHour": "kWh",
        "sds:MegawattHour": "MWh",
        "sds:Gigajoule": "GJ",
        "sds:Megajoule": "MJ",
    }
    return mapping.get(token, token)


def _semantic_input_unit(
    db: Any, variable: Any, fallback_unit: str | None
) -> str | None:
    variable_uri = getattr(variable, "variable_uri", None)
    if not variable_uri:
        return fallback_unit
    try:
        from sqlalchemy import or_

        from src.database import models

        candidates = _concept_uri_candidates(variable_uri)
        variable_concept = (
            db.query(models.Concept)
            .filter(or_(*(models.Concept.uri == item for item in candidates)))
            .first()
        )
    except Exception:
        return fallback_unit
    return (
        _semantic_unit_to_runtime_unit(
            getattr(variable_concept, "unit", None)
            if variable_concept is not None
            else None
        )
        or fallback_unit
    )
