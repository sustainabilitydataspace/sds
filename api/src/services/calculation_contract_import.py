"""Import Atomizer calculation contract JSON into canonical SDS tables."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from src.calculation.engine import CalculationEngine, CalculationError
from src.calculation.semantic_formula import local_name
from src.database.models import (
    AtomizerPackageImport,
    CanonicalCalculationComponent,
    CanonicalCalculationContract,
    CanonicalCalculationDimension,
    CanonicalCalculationGate,
    CanonicalCalculationSupportRule,
    CanonicalConcept,
    Concept,
    ConceptState,
    Indicator,
)
from src.services.canonical_mapping_relationship_policy import (
    OPERATIONAL_RELATIONSHIP_TYPES,
    normalize_relationship_type,
)

DEFAULT_CREATED_BY = "calculation_contract_import"
EXECUTABLE_STATUS = "executable"
NON_EXECUTABLE_STATUSES = {"semantic_only", "audit_only"}
RUNTIME_STATUSES = {EXECUTABLE_STATUS, *NON_EXECUTABLE_STATUSES}
EXPOSURES = {"public_register", "runtime_support", "container", "audit_only"}
INPUT_FORMULA_KINDS = {"input", "constant", "source"}
SUPPORTED_CONVERSION_POLICIES = {"fail_closed"}
RETIREMENT_SCOPES = {"incoming_keys", "incoming_models"}


class CalculationContractImportError(ValueError):
    """Raised when a calculation contract cannot be validated or imported."""


def _validate_runtime_expression(
    runtime_expression: str,
    *,
    context: str,
    component_refs: list[dict[str, Any]],
    formula: dict[str, Any],
    formula_kind: str,
) -> None:
    engine = CalculationEngine()
    try:
        engine._validate_formula(runtime_expression)
    except CalculationError as exc:
        raise CalculationContractImportError(
            f"{context} runtime_expression invalid: {exc}"
        ) from exc

    if formula_kind in INPUT_FORMULA_KINDS and not component_refs:
        return

    ordered_expression_variables = engine._expression_variable_names(runtime_expression)
    declared_derived_variables = _declared_derived_variables(formula)
    positional_variables = [
        variable
        for variable in ordered_expression_variables
        if variable not in declared_derived_variables
    ]
    allowed_variables = _component_ref_variables(
        component_refs,
        positional_variables=positional_variables,
    )
    _validate_derived_runtime_expressions(
        formula,
        engine=engine,
        context=context,
        base_variables=allowed_variables,
    )
    allowed_variables.update(declared_derived_variables)

    expression_variables = set(ordered_expression_variables)
    _reject_unbound_runtime_variables(
        expression_variables,
        allowed_variables=allowed_variables,
        context=f"{context} runtime_expression",
    )


def _component_ref_variables(
    component_refs: list[dict[str, Any]], *, positional_variables: list[str]
) -> set[str]:
    variables: set[str] = set()
    for index, component_ref in enumerate(component_refs):
        variable = (
            _optional_str(component_ref.get("local_variable"))
            or _optional_str(component_ref.get("runtime_variable"))
            or (
                positional_variables[index]
                if index < len(positional_variables)
                else None
            )
            or local_name(_optional_str(component_ref.get("variable_uri")))
            or local_name(_optional_str(component_ref.get("component_id")))
            or local_name(_optional_str(component_ref.get("component_node_id")))
            or local_name(_optional_str(component_ref.get("node_id")))
        )
        if variable:
            variables.add(variable)
    return variables


def _reject_unbound_runtime_variables(
    expression_variables: set[str], *, allowed_variables: set[str], context: str
) -> None:
    unbound = sorted(expression_variables - allowed_variables)
    if unbound:
        raise CalculationContractImportError(
            f"{context} has unbound variables: {', '.join(unbound)}"
        )


def _declared_derived_variables(formula: dict[str, Any]) -> set[str]:
    variables: set[str] = set()
    for key in ("derived_nodes", "derived", "intermediate_nodes"):
        for item in _list_or_empty(formula.get(key)):
            if not isinstance(item, dict):
                continue
            variable = _optional_str(
                item.get("local_variable")
                or item.get("runtime_variable")
                or item.get("name")
                or item.get("variable")
            )
            if variable:
                variables.add(variable)
    return variables


def _declared_derived_nodes(formula: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for key in ("derived_nodes", "derived", "intermediate_nodes"):
        for item in _list_or_empty(formula.get(key)):
            if isinstance(item, dict):
                nodes.append(item)
    return nodes


def _validate_derived_runtime_expressions(
    formula: dict[str, Any],
    *,
    engine: CalculationEngine,
    context: str,
    base_variables: set[str],
) -> None:
    allowed_variables = set(base_variables)
    for index, node in enumerate(_declared_derived_nodes(formula)):
        local_variable = _optional_str(
            node.get("local_variable")
            or node.get("runtime_variable")
            or node.get("name")
            or node.get("variable")
        )
        if not local_variable:
            raise CalculationContractImportError(
                f"{context} derived_nodes[{index}] has no local_variable"
            )
        if not local_variable.isidentifier():
            raise CalculationContractImportError(
                f"{context} derived_nodes[{index}] has invalid local_variable: "
                f"{local_variable}"
            )

        expression = _optional_str(
            node.get("expression")
            or node.get("runtime_expression")
            or node.get("formula")
            or node.get("formula_expression")
        )
        if not expression:
            raise CalculationContractImportError(
                f"{context} derived_nodes[{index}] requires expression"
            )
        try:
            engine._validate_formula(expression)
        except CalculationError as exc:
            raise CalculationContractImportError(
                f"{context} derived_nodes[{index}] expression invalid: {exc}"
            ) from exc

        expression_variables = set(engine._expression_variable_names(expression))
        _reject_unbound_runtime_variables(
            expression_variables,
            allowed_variables=allowed_variables,
            context=f"{context} derived_nodes[{index}] expression",
        )
        explicit_dependencies = set()
        for dependency in _list_or_empty(node.get("dependencies")):
            dependency_name = _optional_str(dependency)
            if not dependency_name:
                raise CalculationContractImportError(
                    f"{context} derived_nodes[{index}] dependencies "
                    "must contain non-empty variables"
                )
            explicit_dependencies.add(dependency_name)
        _reject_unbound_runtime_variables(
            explicit_dependencies,
            allowed_variables=allowed_variables,
            context=f"{context} derived_nodes[{index}] dependencies",
        )
        allowed_variables.add(local_variable)


@dataclass(frozen=True)
class CalculationContractValidation:
    """Validation summary for an Atomizer calculation contract payload."""

    valid: bool
    node_count: int
    executable_count: int
    semantic_only_count: int
    audit_only_count: int
    public_indicator_count: int
    component_count: int


@dataclass
class CalculationContractImportReport:
    """Outcome of a calculation contract import."""

    contract_path: str
    package_hash: str
    contract_sha256: str
    dry_run: bool
    valid: bool
    committed: bool
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_path": self.contract_path,
            "package_hash": self.package_hash,
            "contract_sha256": self.contract_sha256,
            "dry_run": self.dry_run,
            "valid": self.valid,
            "committed": self.committed,
            "status": self.status,
            "counts": dict(self.counts),
            "errors": list(self.errors),
        }


def import_calculation_contract_file(
    *,
    contract_path: Path,
    db: Session,
    dry_run: bool = False,
    created_by: str = DEFAULT_CREATED_BY,
    known_indicator_identifiers: set[str] | None = None,
    retirement_scope: str = "incoming_keys",
) -> CalculationContractImportReport:
    """Validate and import a calculation contract file.

    The function validates the whole payload before adding ORM rows. Dry-runs
    validate against the current catalog and return counts without adding rows.
    Re-importing the same package hash after a completed import is idempotent.
    """

    contract_path = contract_path.resolve()
    if retirement_scope not in RETIREMENT_SCOPES:
        raise CalculationContractImportError(
            "retirement_scope must be one of: " + ", ".join(sorted(RETIREMENT_SCOPES))
        )
    payload, contract_sha256 = load_calculation_contract_payload(contract_path)
    indicator_by_identifier = _active_indicator_lookup(db)
    validation_indicator_identifiers = set(indicator_by_identifier)
    validation_indicator_identifiers.update(known_indicator_identifiers or set())
    validation = validate_calculation_contract_payload(
        payload,
        known_indicator_identifiers=validation_indicator_identifiers,
    )
    package_hash = package_hash_for_payload(payload, contract_sha256)

    base_report = CalculationContractImportReport(
        contract_path=str(contract_path),
        package_hash=package_hash,
        contract_sha256=contract_sha256,
        dry_run=dry_run,
        valid=True,
        committed=False,
        status="validated" if dry_run else "pending",
        counts=_validation_counts(validation),
    )
    if dry_run:
        return base_report

    existing_import = _completed_package_import(db, package_hash)
    if existing_import is not None:
        base_report.status = "already_imported"
        return base_report

    nodes_by_id = {
        node["node_id"]: node
        for node in payload["nodes"]
        if isinstance(node, dict) and node.get("node_id")
    }
    source_package = _object_or_empty(payload.get("source_package"))
    register_info = _object_or_empty(payload.get("register"))
    package_import = AtomizerPackageImport(
        package_id=_optional_str(
            source_package.get("package_id")
            or source_package.get("id")
            or source_package.get("manifest_hash")
        ),
        package_version=_optional_str(source_package.get("version")),
        package_hash=package_hash,
        manifest_hash=_optional_str(source_package.get("manifest_hash")),
        register_sha256=_optional_str(register_info.get("sha256")),
        calculation_contract_sha256=contract_sha256,
        import_mode="apply",
        status="running",
        contract_node_count=validation.node_count,
        executable_contract_count=validation.executable_count,
        semantic_only_count=validation.semantic_only_count,
        audit_only_count=validation.audit_only_count,
        source_package=source_package or None,
        file_hashes={
            "sds_calculation_contract.json": contract_sha256,
            "sds_dataset_register.csv": register_info.get("sha256"),
        },
        created_by=created_by,
    )

    try:
        db.add(package_import)
        db.flush()

        retired_count = _retire_current_contracts(
            db,
            payload["nodes"],
            retirement_scope=retirement_scope,
        )
        if retired_count:
            base_report.counts["contracts_retired"] = retired_count

        contract_by_node_id: dict[str, CanonicalCalculationContract] = {}
        for node in payload["nodes"]:
            contract = _create_contract_row(
                node=node,
                payload=payload,
                package_import=package_import,
                contract_sha256=contract_sha256,
                indicator_by_identifier=indicator_by_identifier,
                created_by=created_by,
            )
            db.add(contract)
            db.flush()
            contract_by_node_id[contract.node_id] = contract
            base_report.counts["contracts_created"] = (
                base_report.counts.get("contracts_created", 0) + 1
            )

            for component_order, component in enumerate(
                _component_refs_for_node(node, nodes_by_id=nodes_by_id), start=1
            ):
                db.add(
                    _create_component_row(
                        component=component,
                        component_order=component_order,
                        contract=contract,
                        package_import=package_import,
                    )
                )
                base_report.counts["components_created"] = (
                    base_report.counts.get("components_created", 0) + 1
                )

            for dimension in _list_or_empty(node.get("dimensions")):
                db.add(
                    _create_dimension_row(
                        dimension=dimension,
                        contract=contract,
                        package_import=package_import,
                    )
                )
                base_report.counts["dimensions_created"] = (
                    base_report.counts.get("dimensions_created", 0) + 1
                )

            _mark_semantic_state_if_calculable(db, contract, indicator_by_identifier)

        _add_global_gate_rows(
            db,
            payload=payload,
            package_import=package_import,
            contract_by_node_id=contract_by_node_id,
            report=base_report,
        )
        _add_global_support_rule_rows(
            db,
            payload=payload,
            package_import=package_import,
            contract_by_node_id=contract_by_node_id,
            report=base_report,
        )

        package_import.status = "completed"
        package_import.completed_at = _utcnow_naive()
        base_report.committed = True
        base_report.status = "completed"
        package_import.result_json = base_report.as_dict()
        db.commit()
        return base_report
    except Exception:
        db.rollback()
        raise


def load_calculation_contract_payload(path: Path) -> tuple[dict[str, Any], str]:
    """Load JSON payload and return it with the file SHA-256."""

    if not path.exists():
        raise CalculationContractImportError(f"calculation contract not found: {path}")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CalculationContractImportError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CalculationContractImportError(
            "calculation contract JSON must be an object"
        )
    return payload, hashlib.sha256(raw).hexdigest()


def validate_calculation_contract_payload(
    payload: dict[str, Any],
    *,
    known_indicator_identifiers: set[str] | None = None,
) -> CalculationContractValidation:
    """Validate the contract shape and catalog references before any writes."""

    if not isinstance(payload, dict):
        raise CalculationContractImportError(
            "calculation contract payload must be an object"
        )
    if not payload.get("contract_version"):
        raise CalculationContractImportError("contract_version is required")

    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise CalculationContractImportError("nodes must be a list")

    known_indicator_identifiers = known_indicator_identifiers or set()
    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise CalculationContractImportError(f"nodes[{index}] must be an object")
        node_id = _required_str(node, "node_id", f"nodes[{index}]")
        if node_id in node_ids:
            raise CalculationContractImportError(f"duplicate node_id: {node_id}")
        node_ids.add(node_id)

    executable_count = 0
    semantic_only_count = 0
    audit_only_count = 0
    public_indicator_count = 0
    component_count = 0

    for index, node in enumerate(nodes):
        context = f"node {node.get('node_id') or index}"
        exposure = node.get("exposure") or "audit_only"
        if exposure not in EXPOSURES:
            raise CalculationContractImportError(
                f"{context} has unsupported exposure: {exposure}"
            )
        runtime_status = node.get("runtime_status") or "audit_only"
        if runtime_status not in RUNTIME_STATUSES:
            raise CalculationContractImportError(
                f"{context} has unsupported runtime_status: {runtime_status}"
            )

        indicator_identifier = _indicator_identifier_for_node(node)
        if exposure == "public_register" and not indicator_identifier:
            raise CalculationContractImportError(
                f"{context} is public_register but has no indicator_identifier"
            )
        if indicator_identifier:
            public_indicator_count += 1
            is_unknown = indicator_identifier not in known_indicator_identifiers
            if exposure == "public_register":
                # Fail closed: a public-register contract MUST resolve to a known
                # active indicator. An empty/absent known set means "unknown", not
                # "permissive", so we never persist a public contract with a
                # NULL indicator_id against an empty/unchecked catalog (codex F04 M3).
                if is_unknown:
                    raise CalculationContractImportError(
                        f"{context} is public_register but references an unknown "
                        f"indicator (not in the active catalog / known register): "
                        f"{indicator_identifier}"
                    )
            elif known_indicator_identifiers and is_unknown:
                raise CalculationContractImportError(
                    f"{context} references unknown indicator: {indicator_identifier}"
                )

        if runtime_status == EXECUTABLE_STATUS:
            executable_count += 1
        elif runtime_status == "semantic_only":
            semantic_only_count += 1
        elif runtime_status == "audit_only":
            audit_only_count += 1

        _normalize_currency(
            node.get("result_currency"),
            context=f"{context} result_currency",
        )
        _normalize_currency(node.get("currency"), context=f"{context} currency")
        _normalize_conversion_policy(
            node.get("conversion_policy"),
            context=f"{context} conversion_policy",
        )

        formula = _object_or_empty(node.get("formula"))
        formula_kind = formula.get("kind") or "input"
        component_refs = formula.get("component_refs")
        if component_refs is None:
            component_refs = []
        if not isinstance(component_refs, list):
            raise CalculationContractImportError(
                f"{context} component_refs must be a list"
            )

        if (
            runtime_status == EXECUTABLE_STATUS
            and formula_kind not in INPUT_FORMULA_KINDS
        ):
            if not component_refs:
                raise CalculationContractImportError(
                    f"{context} executable formula requires component_refs"
                )
            if not formula.get("runtime_expression"):
                raise CalculationContractImportError(
                    f"{context} executable formula requires runtime_expression"
                )

        component_ids = set(_list_or_empty(formula.get("component_ids")))
        ref_component_ids: set[str] = set()
        for component_index, component_ref in enumerate(component_refs):
            if not isinstance(component_ref, dict):
                raise CalculationContractImportError(
                    f"{context} component_refs[{component_index}] must be an object"
                )
            component_id = _required_str(
                component_ref, "component_id", f"{context} component_ref"
            )
            ref_component_ids.add(component_id)
            component_count += 1
            ref_node_id = _optional_str(component_ref.get("node_id"))
            ref_indicator_identifier = _optional_str(
                component_ref.get("indicator_identifier")
            )
            ref_variable_uri = _optional_str(component_ref.get("variable_uri"))
            if (
                not ref_node_id
                and not ref_indicator_identifier
                and not ref_variable_uri
            ):
                raise CalculationContractImportError(
                    f"{context} component_ref {component_id} has no node, "
                    "indicator, or variable_uri"
                )
            if ref_node_id and ref_node_id not in node_ids:
                raise CalculationContractImportError(
                    f"{context} has orphan component_ref node_id: {ref_node_id}"
                )
            if (
                ref_indicator_identifier
                and known_indicator_identifiers
                and ref_indicator_identifier not in known_indicator_identifiers
            ):
                raise CalculationContractImportError(
                    f"{context} component_ref references unknown indicator: "
                    f"{ref_indicator_identifier}"
                )
            _validate_component_conversion_policy(component_ref, context, component_id)
            _validate_component_mapping_relationship_policy(
                component_ref, context, component_id
            )

        missing_refs = sorted(component_ids - ref_component_ids)
        if runtime_status == EXECUTABLE_STATUS and missing_refs:
            raise CalculationContractImportError(
                f"{context} missing component_refs for component_ids: "
                f"{', '.join(missing_refs)}"
            )

        runtime_expression = _optional_str(formula.get("runtime_expression"))
        if runtime_status == EXECUTABLE_STATUS and runtime_expression:
            _validate_runtime_expression(
                runtime_expression,
                context=context,
                component_refs=component_refs,
                formula=formula,
                formula_kind=str(formula_kind),
            )

    return CalculationContractValidation(
        valid=True,
        node_count=len(nodes),
        executable_count=executable_count,
        semantic_only_count=semantic_only_count,
        audit_only_count=audit_only_count,
        public_indicator_count=public_indicator_count,
        component_count=component_count,
    )


def package_hash_for_payload(payload: dict[str, Any], contract_sha256: str) -> str:
    """Return the idempotency hash for a package import."""

    source_package = _object_or_empty(payload.get("source_package"))
    manifest_hash = _optional_str(source_package.get("manifest_hash"))
    return manifest_hash or contract_sha256


def _validation_counts(validation: CalculationContractValidation) -> dict[str, int]:
    return {
        "contract_nodes": validation.node_count,
        "executable_contracts": validation.executable_count,
        "semantic_only_contracts": validation.semantic_only_count,
        "audit_only_contracts": validation.audit_only_count,
        "public_indicator_refs": validation.public_indicator_count,
        "component_refs": validation.component_count,
    }


def _active_indicator_lookup(db: Session) -> dict[str, Indicator]:
    lookup: dict[str, Indicator] = {}
    for indicator in db.query(Indicator).all():
        if getattr(indicator, "is_active", True) is False:
            continue
        identifier = _optional_str(getattr(indicator, "identifier", None))
        if identifier:
            lookup[identifier] = indicator
    return lookup


def _completed_package_import(
    db: Session, package_hash: str
) -> AtomizerPackageImport | None:
    return (
        db.query(AtomizerPackageImport)
        .filter_by(package_hash=package_hash, status="completed")
        .first()
    )


def _retire_current_contracts(
    db: Session,
    incoming_nodes: list[dict[str, Any]],
    *,
    retirement_scope: str = "incoming_keys",
) -> int:
    if retirement_scope not in RETIREMENT_SCOPES:
        raise CalculationContractImportError(
            "retirement_scope must be one of: " + ", ".join(sorted(RETIREMENT_SCOPES))
        )
    node_ids = {_required_str(node, "node_id", "node") for node in incoming_nodes}
    model_ids = {
        _required_str(node, "model_id", _required_str(node, "node_id", "node"))
        for node in incoming_nodes
    }
    model_datapoint_keys = {
        (
            _required_str(node, "model_id", _required_str(node, "node_id", "node")),
            datapoint_id,
        )
        for node in incoming_nodes
        if (datapoint_id := _optional_str(node.get("canonical_datapoint_id")))
    }
    indicator_identifiers = {
        _indicator_identifier_for_node(node) for node in incoming_nodes
    }
    indicator_identifiers.discard(None)
    now = _utcnow_naive()
    retired_count = 0

    for contract in db.query(CanonicalCalculationContract).all():
        if getattr(contract, "is_active", True) is False:
            continue
        if (
            contract.node_id in node_ids
            or (
                contract.model_id,
                contract.canonical_datapoint_id,
            )
            in model_datapoint_keys
            or contract.indicator_identifier in indicator_identifiers
            or (
                retirement_scope == "incoming_models" and contract.model_id in model_ids
            )
        ):
            contract.is_active = False
            contract.effective_to = now
            retired_count += 1
    return retired_count


def _create_contract_row(
    *,
    node: dict[str, Any],
    payload: dict[str, Any],
    package_import: AtomizerPackageImport,
    contract_sha256: str,
    indicator_by_identifier: dict[str, Indicator],
    created_by: str,
) -> CanonicalCalculationContract:
    formula = _object_or_empty(node.get("formula"))
    indicator_identifier = _indicator_identifier_for_node(node)
    indicator = (
        indicator_by_identifier.get(indicator_identifier)
        if indicator_identifier
        else None
    )
    node_id = _required_str(node, "node_id", "node")
    return CanonicalCalculationContract(
        package_import_id=package_import.id,
        package_import=package_import,
        contract_version=str(payload["contract_version"]),
        model_id=_required_str(node, "model_id", node_id),
        node_id=node_id,
        canonical_datapoint_id=_optional_str(node.get("canonical_datapoint_id")),
        indicator_id=getattr(indicator, "id", None) if indicator is not None else None,
        indicator=indicator,
        indicator_identifier=indicator_identifier,
        label=_optional_str(node.get("label")) or node_id,
        exposure=node.get("exposure") or "audit_only",
        runtime_status=node.get("runtime_status") or "audit_only",
        role=_optional_str(node.get("role")),
        source_kind=_optional_str(node.get("source_kind")),
        formula_kind=_optional_str(formula.get("kind")) or "input",
        semantic_expression=_optional_str(formula.get("semantic_expression")),
        runtime_expression=_optional_str(formula.get("runtime_expression")),
        value_kind=_optional_str(node.get("value_kind")),
        unit_name=_optional_str(node.get("unit_name")),
        unit_type=_optional_str(node.get("unit_type")),
        result_currency=_normalize_currency(
            node.get("result_currency") or node.get("currency"),
            context=f"{node_id} result_currency",
        ),
        conversion_policy=_normalize_conversion_policy(
            node.get("conversion_policy"),
            context=f"{node_id} conversion_policy",
        ),
        parent_node_id=_optional_str(node.get("parent_id")),
        relation_to_parent=_optional_str(node.get("relation_to_parent")),
        aggregation_policy=_object_or_none(node.get("aggregation")),
        completeness_policy=_object_or_none(node.get("completeness")),
        gate_ids=_list_or_empty(node.get("gate_ids")),
        support_rule_ids=_list_or_empty(node.get("support_rule_ids")),
        evidence=_list_or_empty(node.get("evidence")),
        provenance=_object_or_none(node.get("provenance")),
        source_payload=node,
        contract_hash=_contract_hash(payload, node, contract_sha256),
        created_by=created_by,
    )


def _create_component_row(
    *,
    component: dict[str, Any],
    component_order: int,
    contract: CanonicalCalculationContract,
    package_import: AtomizerPackageImport,
) -> CanonicalCalculationComponent:
    return CanonicalCalculationComponent(
        contract_id=contract.id,
        contract=contract,
        package_import_id=package_import.id,
        package_import=package_import,
        component_order=component_order,
        component_id=_required_str(component, "component_id", "component_ref"),
        component_node_id=_optional_str(component.get("node_id")),
        indicator_identifier=_optional_str(component.get("indicator_identifier")),
        component_scope=_optional_str(component.get("component_scope")),
        variable_uri=_optional_str(component.get("variable_uri")),
        unit_name=_optional_str(component.get("unit_name")),
        unit_type=_optional_str(component.get("unit_type")),
        currency=_normalize_currency(
            component.get("currency"),
            context=f"{component.get('component_id')} currency",
        ),
        expected_currency=_normalize_currency(
            component.get("expected_currency"),
            context=f"{component.get('component_id')} expected_currency",
        ),
        conversion_policy=_normalize_conversion_policy(
            component.get("conversion_policy"),
            context=f"{component.get('component_id')} conversion_policy",
        ),
        fx_policy_id=_optional_str(component.get("fx_policy_id")),
        aggregation_method=_optional_str(component.get("aggregation_method")),
        role=_optional_str(component.get("role")),
        required=bool(component.get("required", True)),
        numerator_denominator_role=_optional_str(
            component.get("numerator_denominator_role")
        ),
        weight_hint=_optional_str(component.get("weight_hint")),
        source_payload=component,
    )


def _create_dimension_row(
    *,
    dimension: dict[str, Any],
    contract: CanonicalCalculationContract,
    package_import: AtomizerPackageImport,
) -> CanonicalCalculationDimension:
    return CanonicalCalculationDimension(
        contract_id=contract.id,
        contract=contract,
        package_import_id=package_import.id,
        package_import=package_import,
        dimension_id=_required_str(dimension, "dimension_id", "dimension"),
        mode=_optional_str(dimension.get("mode")) or "unspecified",
        fixed_value=_optional_str(dimension.get("fixed_value")),
        member_values=_list_or_empty(dimension.get("member_values")),
        required=bool(dimension.get("required", False)),
        rationale=_optional_str(dimension.get("rationale")),
        source_payload=dimension,
    )


def _add_global_gate_rows(
    db: Session,
    *,
    payload: dict[str, Any],
    package_import: AtomizerPackageImport,
    contract_by_node_id: dict[str, CanonicalCalculationContract],
    report: CalculationContractImportReport,
) -> None:
    del contract_by_node_id
    for gate in _list_or_empty(payload.get("gates")):
        if not isinstance(gate, dict):
            continue
        gate_id = _optional_str(gate.get("gate_id") or gate.get("id"))
        if not gate_id:
            continue
        db.add(
            CanonicalCalculationGate(
                package_import_id=package_import.id,
                package_import=package_import,
                gate_id=gate_id,
                title=_optional_str(gate.get("title")),
                payload=gate,
            )
        )
        report.counts["gates_created"] = report.counts.get("gates_created", 0) + 1


def _add_global_support_rule_rows(
    db: Session,
    *,
    payload: dict[str, Any],
    package_import: AtomizerPackageImport,
    contract_by_node_id: dict[str, CanonicalCalculationContract],
    report: CalculationContractImportReport,
) -> None:
    del contract_by_node_id
    for rule in _list_or_empty(payload.get("support_rules")):
        if not isinstance(rule, dict):
            continue
        support_rule_id = _optional_str(rule.get("support_rule_id") or rule.get("id"))
        if not support_rule_id:
            continue
        db.add(
            CanonicalCalculationSupportRule(
                package_import_id=package_import.id,
                package_import=package_import,
                support_rule_id=support_rule_id,
                title=_optional_str(rule.get("title")),
                payload=rule,
            )
        )
        report.counts["support_rules_created"] = (
            report.counts.get("support_rules_created", 0) + 1
        )


def _mark_semantic_state_if_calculable(
    db: Session,
    contract: CanonicalCalculationContract,
    indicator_by_identifier: dict[str, Indicator],
) -> None:
    if (
        contract.runtime_status != EXECUTABLE_STATUS
        or not contract.indicator_identifier
    ):
        return
    indicator = indicator_by_identifier.get(contract.indicator_identifier)
    if indicator is None:
        return
    indicator.concept_state = ConceptState.CALCULABLE

    for concept_model in (Concept, CanonicalConcept):
        for concept in db.query(concept_model).all():
            if getattr(concept, "indicator_id", None) == indicator.id:
                concept.concept_state = ConceptState.CALCULABLE


def _component_refs_for_node(
    node: dict[str, Any], *, nodes_by_id: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    formula = _object_or_empty(node.get("formula"))
    nodes_by_id = nodes_by_id or {}
    components: list[dict[str, Any]] = []
    for component in _list_or_empty(formula.get("component_refs")):
        if not isinstance(component, dict):
            continue
        enriched = dict(component)
        referenced_node = nodes_by_id.get(_optional_str(component.get("node_id")) or "")
        if referenced_node:
            for key in ("unit_name", "unit_type", "value_kind"):
                if not _optional_str(enriched.get(key)):
                    enriched[key] = referenced_node.get(key)
            referenced_aggregation = _object_or_empty(
                referenced_node.get("aggregation")
            )
            if not _optional_str(enriched.get("aggregation_method")):
                enriched["aggregation_method"] = referenced_aggregation.get("temporal")
            if not _optional_str(enriched.get("perimeter_aggregation")):
                enriched["perimeter_aggregation"] = referenced_aggregation.get(
                    "perimeter"
                )
        components.append(enriched)
    return components


def _contract_hash(
    payload: dict[str, Any], node: dict[str, Any], contract_sha256: str
) -> str:
    stable = {
        "contract_version": payload.get("contract_version"),
        "source_package_hash": package_hash_for_payload(payload, contract_sha256),
        "node": node,
    }
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _indicator_identifier_for_node(node: dict[str, Any]) -> str | None:
    return _optional_str(
        node.get("indicator_identifier") or node.get("register_identifier")
    )


def _required_str(payload: dict[str, Any], key: str, context: str) -> str:
    value = _optional_str(payload.get(key))
    if not value:
        raise CalculationContractImportError(f"{context} requires {key}")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _normalize_currency(value: Any, *, context: str) -> str | None:
    stripped = _optional_str(value)
    if stripped is None:
        return None
    normalized = stripped.upper()
    if not re.fullmatch(r"[A-Z]{3}", normalized):
        raise CalculationContractImportError(f"{context} invalid currency: {value!r}")
    return normalized


def _normalize_conversion_policy(value: Any, *, context: str) -> str:
    policy = (_optional_str(value) or "fail_closed").lower()
    if policy not in SUPPORTED_CONVERSION_POLICIES:
        raise CalculationContractImportError(
            f"{context} unsupported conversion_policy: {policy}"
        )
    return policy


def _validate_component_conversion_policy(
    component_ref: dict[str, Any],
    node_context: str,
    component_id: str,
) -> None:
    context = f"{node_context} component_ref {component_id}"
    _normalize_currency(component_ref.get("currency"), context=f"{context} currency")
    expected_currency = _normalize_currency(
        component_ref.get("expected_currency"),
        context=f"{context} expected_currency",
    )
    fx_policy_id = _optional_str(component_ref.get("fx_policy_id"))
    _normalize_conversion_policy(
        component_ref.get("conversion_policy"),
        context=f"{context} conversion_policy",
    )
    if expected_currency and not fx_policy_id:
        raise CalculationContractImportError(
            f"{context} expected_currency requires fx_policy_id"
        )
    if fx_policy_id and not expected_currency:
        raise CalculationContractImportError(
            f"{context} fx_policy_id requires expected_currency"
        )


def _validate_component_mapping_relationship_policy(
    component_ref: dict[str, Any],
    node_context: str,
    component_id: str,
) -> None:
    context = f"{node_context} component_ref {component_id}"
    for key in (
        "allowed_mapping_relationships",
        "mapping_relationships",
        "allowed_relationships",
    ):
        if key not in component_ref or component_ref[key] is None:
            continue
        relationships = component_ref[key]
        if isinstance(relationships, str) or not isinstance(relationships, list):
            raise CalculationContractImportError(f"{context} {key} must be a list")
        for relationship in relationships:
            relationship_text = _optional_str(relationship)
            normalized = normalize_relationship_type(relationship_text)
            if (
                relationship_text is None
                or normalized not in OPERATIONAL_RELATIONSHIP_TYPES
            ):
                raise CalculationContractImportError(
                    f"{context} unsupported {key}: {relationship!r}"
                )


def _object_or_empty(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CalculationContractImportError("expected object in calculation contract")
    return value


def _object_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _object_or_empty(value)


def _list_or_empty(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CalculationContractImportError("expected list in calculation contract")
    return value


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
