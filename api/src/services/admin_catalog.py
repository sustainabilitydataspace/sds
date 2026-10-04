"""Admin calculation-contract import and unit-catalog repair services.

Every mutation records an ``admin_catalog_operations`` row in the same
transaction. The highest ``ok`` row in scope ``unit_catalog`` is the catalog
revision that API processes compare before serving unit lookups.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.calculation.conversion.physical import (
    PhysicalUnit,
    UnitConflict,
    analyze_expression_registry,
)
from src.database.models import (
    AdminCatalogOperation,
    AtomizerPackageImport,
    ConversionRule,
    ESGValue,
    Unit,
    UnitCategory,
    ValueRevision,
)
from src.services.calculation_contract_import import (
    CalculationContractImportError,
    import_calculation_contract_bytes,
    load_calculation_contract_bytes,
    package_hash_for_payload,
)

REPAIR_PLAN_TTL = timedelta(minutes=10)
MAX_CONFLICTS_PAGE = 100
IMPACT_PAGE_LIMIT = 50
IMPACT_STATEMENT_TIMEOUT = "5s"
REFERENCE_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "units_database.json"
)
DUPLICATE_PLAN_DOMAIN = b"sds-unit-repair-plan-v1"
FACTOR_PLAN_DOMAIN = b"sds-unit-factor-correction-v1"
_SNAPSHOT_FIELDS = (
    "id",
    "category_id",
    "symbol",
    "name",
    "conversion_factor",
    "conversion_offset",
    "aliases",
    "unit_metadata",
    "dimension_vector",
    "valid_from",
    "valid_to",
    "is_active",
)


class AdminCatalogError(Exception):
    """Operation refused; ``status_code`` is the HTTP status to return."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class Actor:
    user_id: str
    auth_method: str
    request_id: Optional[str] = None


@dataclass
class ContractOutcome:
    status: str
    package_hash: str
    contract_sha256: str
    committed: bool
    counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Revision and audit
# --------------------------------------------------------------------------


def unit_catalog_revision(db: Session) -> int:
    value = (
        db.query(func.coalesce(func.max(AdminCatalogOperation.id), 0))
        .filter(
            AdminCatalogOperation.scope == "unit_catalog",
            AdminCatalogOperation.result == "ok",
        )
        .scalar()
    )
    return int(value or 0)


def _record(db: Session, actor: Actor, **fields: Any) -> AdminCatalogOperation:
    row = AdminCatalogOperation(
        actor_user_id=actor.user_id,
        auth_method=actor.auth_method,
        request_id=actor.request_id,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def record_failure(db: Session, actor: Actor, **fields: Any) -> None:
    """Persist a failed/rejected audit row in its own short transaction."""
    try:
        _record(db, actor, **fields)
        db.commit()
    except Exception:
        db.rollback()


# --------------------------------------------------------------------------
# Calculation contracts
# --------------------------------------------------------------------------


CONTRACT_INVALID = (
    "calculation contract is invalid; run "
    "scripts/import_calculation_contracts.py --dry-run on the package for details"
)


def _bounded(message: str, limit: int = 300) -> str:
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _lock_key(label: bytes) -> int:
    digest = hashlib.sha256(label).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


UNIT_CATALOG_LOCK_KEY = _lock_key(b"sds-unit-catalog-mutation")


def lock_unit_catalog(db: Session) -> None:
    """Serialize unit-catalog mutations for the rest of this transaction."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": UNIT_CATALOG_LOCK_KEY}
        )


def advisory_lock_key(package_hash: str) -> int:
    """Deterministic signed 64-bit key for pg_advisory_xact_lock."""
    digest = hashlib.sha256(b"sds-contract-import:" + package_hash.encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _completed_import(db: Session, package_hash: str):
    return (
        db.query(AtomizerPackageImport)
        .filter(
            AtomizerPackageImport.package_hash == package_hash,
            AtomizerPackageImport.status == "completed",
        )
        .first()
    )


def validate_contract(db: Session, raw: bytes, actor: Actor) -> ContractOutcome:
    """Dry-run validation against the live indicator catalog; no domain rows."""
    try:
        report = import_calculation_contract_bytes(
            raw, db=db, dry_run=True, created_by=f"admin:{actor.user_id}"
        )
    except CalculationContractImportError:
        db.rollback()
        record_failure(
            db,
            actor,
            action="contract_validate",
            scope="calculation_contracts",
            result="rejected",
            detail="validation_error",
        )
        raise AdminCatalogError(422, CONTRACT_INVALID) from None
    _record(
        db,
        actor,
        action="contract_validate",
        scope="calculation_contracts",
        result="ok",
        package_hash=report.package_hash,
        counts=dict(report.counts),
    )
    db.commit()
    return ContractOutcome(
        status="validated",
        package_hash=report.package_hash,
        contract_sha256=report.contract_sha256,
        committed=False,
        counts=dict(report.counts),
    )


def import_contract(
    db: Session, raw: bytes, actor: Actor, *, retirement_scope: str
) -> ContractOutcome:
    """Import inside ONE route-owned transaction guarded by an advisory lock."""
    try:
        payload, contract_sha256 = load_calculation_contract_bytes(raw)
        package_hash = package_hash_for_payload(payload, contract_sha256)
    except CalculationContractImportError:
        record_failure(
            db,
            actor,
            action="contract_import",
            scope="calculation_contracts",
            result="rejected",
            detail="validation_error",
        )
        raise AdminCatalogError(422, CONTRACT_INVALID) from None

    base = {
        "action": "contract_import",
        "scope": "calculation_contracts",
        "package_hash": package_hash,
    }
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": advisory_lock_key(package_hash)},
            )
        if _completed_import(db, package_hash) is not None:
            _record(db, actor, result="already_imported", **base)
            db.commit()
            return ContractOutcome(
                "already_imported", package_hash, contract_sha256, False
            )
        report = import_calculation_contract_bytes(
            raw,
            db=db,
            dry_run=False,
            created_by=f"admin:{actor.user_id}",
            retirement_scope=retirement_scope,
            source_label="admin-api",
            commit=False,
        )
        _record(db, actor, result="ok", counts=dict(report.counts), **base)
        db.commit()
        return ContractOutcome(
            report.status, package_hash, contract_sha256, True, dict(report.counts)
        )
    except CalculationContractImportError:
        db.rollback()
        record_failure(db, actor, result="rejected", detail="validation_error", **base)
        raise AdminCatalogError(422, CONTRACT_INVALID) from None
    except IntegrityError:
        db.rollback()
        if _completed_import(db, package_hash) is not None:
            _record(db, actor, result="already_imported", **base)
            db.commit()
            return ContractOutcome(
                "already_imported", package_hash, contract_sha256, False
            )
        record_failure(db, actor, result="failed", detail="integrity_error", **base)
        raise
    except AdminCatalogError:
        raise
    except Exception as exc:
        db.rollback()
        record_failure(db, actor, result="failed", detail=type(exc).__name__, **base)
        raise


# --------------------------------------------------------------------------
# Unit-catalog analysis
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def unit_snapshot(unit: Unit) -> dict[str, Any]:
    """Allow-listed exact before-image of a unit row."""
    return {name: _jsonable(getattr(unit, name)) for name in _SNAPSHOT_FIELDS}


def _identifiers(symbol: str, aliases) -> set[str]:
    return {symbol, *(aliases or [])}


def _unit_dict(unit: Unit) -> dict[str, Any]:
    """Same unit dictionary UnitService.get_all_units feeds the converter."""
    return {
        "name": unit.name,
        "symbol": unit.symbol,
        "category": unit.category.name,
        "base_unit": unit.category.base_unit or unit.symbol,
        "aliases": unit.aliases or [],
        "conversion_factor": (
            float(unit.conversion_factor) if unit.conversion_factor else 1.0
        ),
        "conversion_offset": (
            float(unit.conversion_offset) if unit.conversion_offset else 0.0
        ),
        "metadata": unit.unit_metadata or {},
    }


def _physical_units_for(db: Session, units: list[Unit]) -> dict[str, PhysicalUnit]:
    """Build physical units with the UnitConverter's own conversion helpers.

    Works for any rows (including an inactive unit being restored) so the
    analysis matches what a DB-backed converter would build from them.
    """
    from src.calculation.unit_converter import UnitConverter

    to_definition = UnitConverter._dict_to_unit_definition
    physical: dict[str, PhysicalUnit] = {}
    for row in units:
        definition = to_definition(None, _unit_dict(row))
        physical[row.symbol] = PhysicalUnit(
            symbol=definition.symbol,
            name=definition.name,
            dimension=UnitConverter._dimension_for_unit(definition),
            factor_to_base=definition.conversion_factor,
            offset_to_base=definition.conversion_offset,
            aliases=tuple(definition.aliases),
        )
    return physical


def _active_units(db: Session) -> list[Unit]:
    return db.query(Unit).filter(Unit.is_active.is_(True)).order_by(Unit.id).all()


def conflict_id_for(conflict: UnitConflict, ids_by_symbol: dict[str, int]) -> str:
    ids = sorted(ids_by_symbol.get(symbol, -1) for symbol in conflict.symbols)
    raw = f"{conflict.kind}|{conflict.token}|{','.join(str(i) for i in ids)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _analysis_for(db: Session, units: list[Unit]):
    physical = _physical_units_for(db, units)
    ordered = {
        unit.symbol: physical[unit.symbol] for unit in units if unit.symbol in physical
    }
    return physical, analyze_expression_registry(ordered)


def list_conflicts(db: Session, *, limit: int, offset: int) -> dict[str, Any]:
    units = _active_units(db)
    ids_by_symbol = {unit.symbol: unit.id for unit in units}
    by_symbol = {unit.symbol: unit for unit in units}
    physical, analysis = _analysis_for(db, units)
    items = []
    for conflict in analysis.conflicts:
        involved = []
        for symbol in conflict.symbols:
            row = by_symbol.get(symbol)
            phys = physical.get(symbol)
            if row is None:
                continue
            involved.append(
                {
                    "id": row.id,
                    "symbol": row.symbol,
                    "aliases": list(row.aliases or []),
                    "category_id": row.category_id,
                    "conversion_factor": _jsonable(row.conversion_factor),
                    "conversion_offset": _jsonable(row.conversion_offset),
                    "dimension": (dict(phys.dimension.exponents) if phys else None),
                    "is_active": bool(row.is_active),
                }
            )
        items.append(
            {
                "conflict_id": conflict_id_for(conflict, ids_by_symbol),
                "kind": conflict.kind,
                "token": conflict.token,
                "message": conflict.message,
                "units": involved,
            }
        )
    limit = max(1, min(limit, MAX_CONFLICTS_PAGE))
    return {
        "catalog_revision": unit_catalog_revision(db),
        "total": len(items),
        "items": items[offset : offset + limit],
    }


# --------------------------------------------------------------------------
# Unit-catalog repair: preview -> commit -> reverse
# --------------------------------------------------------------------------


def _plan_key(domain: bytes) -> bytes:
    """Server-held key: plans cannot be forged or altered by clients."""
    from src.config.settings import settings

    secret = settings.export_signing_secret.get_secret_value().encode()
    return hmac.new(secret, domain, hashlib.sha256).digest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def plan_digest(plan: dict[str, Any], domain: bytes = DUPLICATE_PLAN_DOMAIN) -> str:
    return hmac.new(_plan_key(domain), _canonical(plan), hashlib.sha256).hexdigest()


def factor_plan_digest(plan: dict[str, Any]) -> str:
    """Factor corrections sign under their own domain: plans never cross over."""
    return plan_digest(plan, FACTOR_PLAN_DOMAIN)


def _validate_plan_fields(plan: dict[str, Any]) -> datetime:
    """Re-check every client-carried plan field the preview produced."""
    try:
        expires_at = datetime.fromisoformat(plan["expires_at"])
        valid = (
            isinstance(plan.get("reason"), str)
            and 1 <= len(plan["reason"]) <= 500
            and isinstance(plan.get("repair_id"), str)
            and len(plan["repair_id"]) == 32
            and all(char in "0123456789abcdef" for char in plan["repair_id"])
            and isinstance(plan.get("deactivate_unit_id"), int)
            and isinstance(plan.get("retain_unit_id"), int)
            and isinstance(plan.get("catalog_revision"), int)
            and expires_at.tzinfo is not None
            and expires_at <= datetime.now(timezone.utc) + REPAIR_PLAN_TTL
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise AdminCatalogError(422, "invalid plan")
    return expires_at


@dataclass
class RepairCheck:
    target: Unit
    retained: Unit
    pre_ids: list[str]
    post_ids: list[str]
    factor: Optional[dict[str, Any]] = None


def _factor_text(value: Any) -> str:
    return format(Decimal(value).normalize(), "f")


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def reference_catalog() -> tuple[dict[str, Any], str]:
    """The bundled canonical unit catalog and the SHA-256 of its exact bytes."""
    raw = REFERENCE_CATALOG_PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def factor_acknowledgement(target: Unit, retained: Unit, base_symbol: str) -> str:
    return (
        f"I confirm unit {target.symbol} "
        f"(factor {_factor_text(target.conversion_factor)}) is incorrect and unit "
        f"{retained.symbol} (factor {_factor_text(retained.conversion_factor)}) "
        f"is correct relative to base {base_symbol}"
    )


def _check_factor_correction(
    db: Session, target: Unit, retained: Unit, *, lock: bool
) -> dict[str, Any]:
    """Prove the retained factor against the base unit and the reference catalog."""
    if Decimal(target.conversion_offset or 0) != 0 or Decimal(
        retained.conversion_offset or 0
    ):
        raise AdminCatalogError(422, "factor corrections require zero offsets")
    if Decimal(target.conversion_factor) == Decimal(retained.conversion_factor):
        raise AdminCatalogError(
            422, "units have equal factors; use a duplicate repair instead"
        )
    category = db.get(UnitCategory, retained.category_id)
    base_symbol = category.base_unit if category is not None else None
    base = None
    if base_symbol:
        query = db.query(Unit).filter(
            Unit.category_id == retained.category_id,
            Unit.symbol == base_symbol,
            Unit.is_active.is_(True),
        )
        if lock:
            query = query.with_for_update()
        base = query.first()
    if (
        base is None
        or Decimal(base.conversion_factor) != 1
        or Decimal(base.conversion_offset or 0) != 0
    ):
        raise AdminCatalogError(
            422,
            "the category base unit must be an active unit with factor 1 and offset 0",
        )

    reference, reference_sha256 = reference_catalog()
    categories = reference.get("categories") or {}
    ref_category = categories.get(category.name) or {}
    ref_unit = (ref_category.get("units") or {}).get(retained.symbol) or {}
    defined = {
        symbol for ref in categories.values() for symbol in (ref.get("units") or {})
    }
    proven = (
        ref_category.get("base_unit") == base_symbol
        and _decimal(ref_unit.get("conversion_factor"))
        == Decimal(retained.conversion_factor)
        and _decimal(ref_unit.get("conversion_offset", 0)) == 0
        and target.symbol in (ref_unit.get("aliases") or [])
        and target.symbol not in defined
    )
    if not proven:
        raise AdminCatalogError(
            422, "the reference unit catalog does not confirm this correction"
        )
    return {
        "base_symbol": base_symbol,
        "base_unit_id": base.id,
        "old_factor": _factor_text(target.conversion_factor),
        "new_factor": _factor_text(retained.conversion_factor),
        "old_offset": _factor_text(target.conversion_offset or 0),
        "new_offset": _factor_text(retained.conversion_offset or 0),
        "reference_sha256": reference_sha256,
        "acknowledgement": factor_acknowledgement(target, retained, base_symbol),
    }


def _check_repair(
    db: Session,
    *,
    conflict_id: str,
    deactivate_unit_id: int,
    retain_unit_id: int,
    lock: bool = False,
    factor_correction: bool = False,
) -> RepairCheck:
    """Validate every repair precondition; return units and conflict ids.

    A duplicate repair needs identical factors and offsets. A factor correction
    instead needs zero offsets, different factors and a proof that the retained
    factor is the canonical one (``_check_factor_correction``).
    """
    if deactivate_unit_id == retain_unit_id:
        raise AdminCatalogError(422, "deactivate and retain units must differ")
    query = db.query(Unit).filter(Unit.id.in_([deactivate_unit_id, retain_unit_id]))
    if lock:
        query = query.with_for_update()
    rows = {row.id: row for row in query.all()}
    target = rows.get(deactivate_unit_id)
    retained = rows.get(retain_unit_id)
    if target is None or retained is None:
        raise AdminCatalogError(404, "unit not found")
    if not (target.is_active and retained.is_active):
        raise AdminCatalogError(409, "both units must be active")

    units = _active_units(db)
    ids_by_symbol = {unit.symbol: unit.id for unit in units}
    physical, before = _analysis_for(db, units)
    pre_ids = [conflict_id_for(c, ids_by_symbol) for c in before.conflicts]
    conflict = next(
        (
            c
            for c in before.conflicts
            if conflict_id_for(c, ids_by_symbol) == conflict_id
        ),
        None,
    )
    if conflict is None:
        raise AdminCatalogError(409, "conflict not found in the current catalog")
    if set(conflict.symbols) != {target.symbol, retained.symbol}:
        raise AdminCatalogError(422, "conflict does not involve exactly these units")

    if target.category_id != retained.category_id:
        raise AdminCatalogError(422, "units belong to different categories")
    if physical[target.symbol].dimension != physical[retained.symbol].dimension:
        raise AdminCatalogError(422, "units have different dimensions")
    factor = None
    if factor_correction:
        factor = _check_factor_correction(db, target, retained, lock=lock)
    else:
        if Decimal(target.conversion_factor) != Decimal(retained.conversion_factor):
            raise AdminCatalogError(422, "units have different conversion factors")
        if Decimal(target.conversion_offset or 0) != Decimal(
            retained.conversion_offset or 0
        ):
            raise AdminCatalogError(422, "units have different conversion offsets")

    # Every identifier normalize_unit_symbol resolves (symbol, aliases and,
    # case-insensitively, the unit name) must keep resolving to an equivalent
    # unit after the repair.
    covered = {
        identifier.lower()
        for identifier in (
            *_identifiers(retained.symbol, retained.aliases),
            retained.name or "",
        )
    }
    uncovered = sorted(
        identifier
        for identifier in _identifiers(target.symbol, target.aliases)
        if identifier.lower() not in covered
    )
    if uncovered:
        raise AdminCatalogError(
            422,
            "retained unit does not cover identifiers: " + ", ".join(uncovered[:20]),
        )
    if target.name and target.name.lower() not in covered:
        # The name would stop resolving: allow only when nothing persisted uses it.
        if _unit_text_in_use(db, target.name):
            raise AdminCatalogError(
                422,
                "stored values or active conversion rules use the unit name "
                f"{target.name!r}, which the retained unit does not cover",
            )

    base_unit = (
        db.query(UnitCategory.id)
        .filter(
            UnitCategory.id == target.category_id,
            UnitCategory.base_unit == target.symbol,
        )
        .first()
    )
    if base_unit is not None:
        raise AdminCatalogError(422, "cannot deactivate a category base unit")

    names = sorted(_identifiers(target.symbol, target.aliases))
    referenced = (
        db.query(ConversionRule.id)
        .filter(
            ConversionRule.is_active.is_(True),
            (ConversionRule.from_unit.in_(names)) | (ConversionRule.to_unit.in_(names)),
        )
        .first()
    )
    if referenced is not None:
        raise AdminCatalogError(422, "active conversion rules reference this unit")

    remaining = [unit for unit in units if unit.id != target.id]
    _, after = _analysis_for(db, remaining)
    remaining_ids = {unit.symbol: unit.id for unit in remaining}
    post_ids = [conflict_id_for(c, remaining_ids) for c in after.conflicts]
    if conflict_id in post_ids or not set(post_ids) <= set(pre_ids):
        raise AdminCatalogError(422, "repair would not resolve the conflict cleanly")
    return RepairCheck(target, retained, pre_ids, post_ids, factor)


def _unit_text_in_use(db: Session, text_value: str) -> bool:
    lowered = text_value.lower()
    value = (
        db.query(ESGValue.id)
        .filter(
            (func.lower(ESGValue.unit) == lowered)
            | (func.lower(ESGValue.original_unit) == lowered)
        )
        .first()
    )
    if value is not None:
        return True
    rule = (
        db.query(ConversionRule.id)
        .filter(
            ConversionRule.is_active.is_(True),
            (func.lower(ConversionRule.from_unit) == lowered)
            | (func.lower(ConversionRule.to_unit) == lowered),
        )
        .first()
    )
    return rule is not None


def verify_catalog_in_transaction(db: Session, expected_ids: list[str]) -> None:
    """Fail the open transaction unless the post-mutation conflicts are expected.

    Runs on the session's own (uncommitted) view while the catalog lock is held,
    so no other catalog operation can interleave and nothing needs compensating.
    """
    units = _active_units(db)
    ids_by_symbol = {unit.symbol: unit.id for unit in units}
    physical, analysis = _analysis_for(db, units)
    actual = sorted(conflict_id_for(c, ids_by_symbol) for c in analysis.conflicts)
    if actual != sorted(expected_ids):
        raise AdminCatalogError(
            409, "catalog verification failed: unexpected conflicts after the change"
        )
    if not actual:
        from src.calculation.conversion.physical import PhysicalUnitConverter

        try:
            PhysicalUnitConverter(units=physical, rules=[])
        except ValueError:
            raise AdminCatalogError(
                409, "catalog verification failed: converter cannot be built"
            ) from None


def preview_repair(
    db: Session,
    *,
    conflict_id: str,
    deactivate_unit_id: int,
    retain_unit_id: int,
    reason: str,
) -> dict[str, Any]:
    check = _check_repair(
        db,
        conflict_id=conflict_id,
        deactivate_unit_id=deactivate_unit_id,
        retain_unit_id=retain_unit_id,
    )
    plan = _base_plan(db, check, conflict_id=conflict_id, reason=reason)
    db.rollback()
    return {"plan": plan, "plan_digest": plan_digest(plan)}


def _base_plan(
    db: Session, check: RepairCheck, *, conflict_id: str, reason: str
) -> dict[str, Any]:
    return {
        "repair_id": uuid.uuid4().hex,
        "conflict_id": conflict_id,
        "deactivate_unit_id": check.target.id,
        "deactivate_symbol": check.target.symbol,
        "retain_unit_id": check.retained.id,
        "retain_symbol": check.retained.symbol,
        "reason": reason,
        "before_image": unit_snapshot(check.target),
        "catalog_revision": unit_catalog_revision(db),
        "expires_at": (datetime.now(timezone.utc) + REPAIR_PLAN_TTL).isoformat(),
        "pre_repair_conflicts": check.pre_ids,
        "post_repair_conflicts": check.post_ids,
    }


def _locked_check(
    db: Session, plan: dict[str, Any], *, factor_correction: bool
) -> tuple[int, RepairCheck, dict[str, Any]]:
    """Re-run every precondition under the catalog lock against the plan."""
    expires_at = _validate_plan_fields(plan)
    if datetime.now(timezone.utc) > expires_at:
        raise AdminCatalogError(409, "repair plan expired; preview again")
    # Revision check, verification and mutation happen under one catalog lock.
    lock_unit_catalog(db)
    revision = unit_catalog_revision(db)
    if revision != plan.get("catalog_revision"):
        raise AdminCatalogError(409, "unit catalog changed; preview again")
    check = _check_repair(
        db,
        conflict_id=plan["conflict_id"],
        deactivate_unit_id=int(plan["deactivate_unit_id"]),
        retain_unit_id=int(plan["retain_unit_id"]),
        lock=True,
        factor_correction=factor_correction,
    )
    snapshot = unit_snapshot(check.target)
    if snapshot != plan.get("before_image"):
        raise AdminCatalogError(409, "unit changed since preview; preview again")
    if sorted(check.post_ids) != sorted(plan.get("post_repair_conflicts") or []):
        raise AdminCatalogError(409, "unit catalog changed; preview again")
    return revision, check, snapshot


def _deactivate(
    db: Session,
    *,
    plan: dict[str, Any],
    check: RepairCheck,
    revision: int,
    snapshot: dict[str, Any],
    actor: Actor,
    counts: dict[str, Any],
    detail: str,
) -> dict[str, Any]:
    target, retained = check.target, check.retained
    metadata = dict(target.unit_metadata or {})
    metadata["deactivation"] = {
        "repair_id": plan["repair_id"],
        "superseded_by": retained.symbol,
        "reason": plan.get("reason"),
        "actor": actor.user_id,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    target.is_active = False
    target.unit_metadata = metadata
    row = _record(
        db,
        actor,
        action="unit_repair_commit",
        scope="unit_catalog",
        result="ok",
        repair_id=plan["repair_id"],
        unit_ids=[target.id, retained.id],
        catalog_revision_before=revision,
        counts=dict(counts, pre_conflicts=check.pre_ids, post_conflicts=check.post_ids),
        detail=_bounded(detail),
        unit_snapshot=snapshot,
    )
    row.catalog_revision_after = row.id
    db.flush()
    verify_catalog_in_transaction(db, check.post_ids)
    db.commit()
    return {
        "repair_id": plan["repair_id"],
        "deactivated_unit_id": target.id,
        "retained_unit_id": retained.id,
        "catalog_revision": row.id,
        "remaining_conflicts": check.post_ids,
    }


def commit_repair(
    db: Session, *, plan: dict[str, Any], digest: str, actor: Actor
) -> dict[str, Any]:
    if not hmac.compare_digest(plan_digest(plan), digest):
        raise AdminCatalogError(422, "plan digest does not match")
    revision, check, snapshot = _locked_check(db, plan, factor_correction=False)
    return _deactivate(
        db,
        plan=plan,
        check=check,
        revision=revision,
        snapshot=snapshot,
        actor=actor,
        counts={},
        detail=f"deactivated {check.target.symbol} in favour of "
        f"{check.retained.symbol}",
    )


# --------------------------------------------------------------------------
# Factor-correcting repair: the retained unit carries the canonical factor
# --------------------------------------------------------------------------


def preview_factor_correction(
    db: Session,
    *,
    conflict_id: str,
    deactivate_unit_id: int,
    retain_unit_id: int,
    reason: str,
    acknowledgement: str,
) -> dict[str, Any]:
    check = _check_repair(
        db,
        conflict_id=conflict_id,
        deactivate_unit_id=deactivate_unit_id,
        retain_unit_id=retain_unit_id,
        factor_correction=True,
    )
    factor = check.factor or {}
    expected = factor["acknowledgement"]
    if not hmac.compare_digest(acknowledgement.encode(), expected.encode()):
        raise AdminCatalogError(422, "acknowledgement must be exactly: " + expected)
    plan = _base_plan(db, check, conflict_id=conflict_id, reason=reason)
    identifiers = impact_identifiers(check.target)
    db.rollback()
    impact = impact_report(db, identifiers=identifiers)
    plan.update(
        {
            "operation": "factor_correction",
            "base_symbol": factor["base_symbol"],
            "base_unit_id": factor["base_unit_id"],
            "old_factor": factor["old_factor"],
            "new_factor": factor["new_factor"],
            "old_offset": factor["old_offset"],
            "new_offset": factor["new_offset"],
            "acknowledgement": expected,
            "reference_sha256": factor["reference_sha256"],
            "impact_counts": impact["counts"],
            "impact_digest": impact["digest"],
            "impact_timed_out": impact["timed_out"],
        }
    )
    return {"plan": plan, "plan_digest": factor_plan_digest(plan), "impact": impact}


def commit_factor_correction(
    db: Session,
    *,
    plan: dict[str, Any],
    digest: str,
    acknowledgement: str,
    actor: Actor,
) -> dict[str, Any]:
    if not hmac.compare_digest(factor_plan_digest(plan), digest):
        raise AdminCatalogError(422, "plan digest does not match")
    if plan.get("operation") != "factor_correction" or not isinstance(
        plan.get("acknowledgement"), str
    ):
        raise AdminCatalogError(422, "invalid plan")
    if not hmac.compare_digest(
        acknowledgement.encode(), plan["acknowledgement"].encode()
    ):
        raise AdminCatalogError(422, "acknowledgement does not match the plan")
    revision, check, snapshot = _locked_check(db, plan, factor_correction=True)
    factor = check.factor or {}
    bound = (
        "base_symbol",
        "base_unit_id",
        "old_factor",
        "new_factor",
        "old_offset",
        "new_offset",
        "acknowledgement",
        "reference_sha256",
    )
    if any(factor.get(key) != plan.get(key) for key in bound):
        raise AdminCatalogError(409, "unit catalog changed; preview again")
    return _deactivate(
        db,
        plan=plan,
        check=check,
        revision=revision,
        snapshot=snapshot,
        actor=actor,
        counts={
            "operation": "factor_correction",
            "old_factor": factor["old_factor"],
            "new_factor": factor["new_factor"],
            "old_offset": factor["old_offset"],
            "new_offset": factor["new_offset"],
            "base": factor["base_symbol"],
            "impact_counts": plan.get("impact_counts"),
            "impact_digest": plan.get("impact_digest"),
            "impact_timed_out": plan.get("impact_timed_out"),
            "reference_sha256": factor["reference_sha256"],
        },
        detail=f"factor correction: deactivated {check.target.symbol} "
        f"(factor {factor['old_factor']}) in favour of {check.retained.symbol} "
        f"(factor {factor['new_factor']}, base {factor['base_symbol']})",
    )


# --------------------------------------------------------------------------
# Impact report: stored values that possibly used a unit's identifiers
# --------------------------------------------------------------------------


_IMPACT_TABLES = ("esg_values", "value_revisions")


def impact_identifiers(unit: Unit) -> list[str]:
    """Every identifier the resolver accepts for the unit, lowercased."""
    names = (*_identifiers(unit.symbol, unit.aliases), unit.name or "")
    return sorted({name.lower() for name in names if name})


def _impact_model(table: str):
    if table == "esg_values":
        return ESGValue, ESGValue.conversion_applied.is_(True)
    return ValueRevision, ValueRevision.conversion_trace.isnot(None)


def _encode_cursor(table: str, last_id: str) -> str:
    raw = json.dumps([table, last_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: Optional[str]) -> tuple[str, Optional[str]]:
    if not cursor:
        return _IMPACT_TABLES[0], None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        table, last_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError):
        raise AdminCatalogError(422, "invalid cursor") from None
    if table not in _IMPACT_TABLES or not isinstance(last_id, str):
        raise AdminCatalogError(422, "invalid cursor")
    return table, last_id


def impact_report(
    db: Session,
    *,
    identifiers: list[str],
    cursor: Optional[str] = None,
    limit: int = IMPACT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Count stored rows whose unit text matches; SELECT-only, read-only txn.

    Rows are only "possibly affected": a match says the stored unit text is
    one of the identifiers, not that a conversion used the wrong factor.
    Values, metadata, traces, entities and concepts are never returned.
    """
    start_table, after = _decode_cursor(cursor)
    limit = max(1, min(limit, IMPACT_PAGE_LIMIT))
    report: dict[str, Any] = {
        "identifiers": identifiers,
        "classification": "possibly affected",
        "counts": None,
        "items": [],
        "next_cursor": None,
        "truncated": False,
        "timed_out": False,
    }
    db.rollback()
    postgres = db.get_bind().dialect.name == "postgresql"
    try:
        if postgres:
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(
                text(f"SET LOCAL statement_timeout = '{IMPACT_STATEMENT_TIMEOUT}'")
            )
        present = [
            table
            for table in _IMPACT_TABLES
            if inspect(db.connection()).has_table(table)
        ]
        counts: dict[str, Any] = {}
        for table in _IMPACT_TABLES:
            if table not in present:
                counts[table] = None
                continue
            model, signal = _impact_model(table)
            per_field = {}
            for field_name in ("unit", "original_unit"):
                column = getattr(model, field_name)
                rows = (
                    db.query(signal, func.count())
                    .filter(func.lower(column).in_(identifiers))
                    .group_by(signal)
                    .all()
                )
                tally = {"converted": 0, "not_converted": 0}
                for converted, number in rows:
                    tally["converted" if converted else "not_converted"] += int(number)
                per_field[field_name] = tally
            per_field["rows"] = int(
                db.query(func.count(model.id))
                .filter(_impact_match(model, identifiers))
                .scalar()
                or 0
            )
            counts[table] = per_field
        report["counts"] = counts

        items: list[dict[str, str]] = []
        tables = list(_IMPACT_TABLES[_IMPACT_TABLES.index(start_table) :])
        for table in tables:
            if table not in present:
                continue
            model, _ = _impact_model(table)
            query = db.query(model.id).filter(_impact_match(model, identifiers))
            if after is not None and table == start_table:
                query = query.filter(model.id > after)
            remaining = limit - len(items)
            ids = [row[0] for row in query.order_by(model.id).limit(remaining + 1)]
            items.extend({"table": table, "id": row_id} for row_id in ids[:remaining])
            if len(ids) > remaining:
                report["next_cursor"] = _encode_cursor(table, items[-1]["id"])
                break
            if len(items) == limit:
                later = [t for t in tables[tables.index(table) + 1 :] if t in present]
                if any(
                    db.query(_impact_model(t)[0].id)
                    .filter(_impact_match(_impact_model(t)[0], identifiers))
                    .first()
                    for t in later
                ):
                    report["next_cursor"] = _encode_cursor(later[0], "")
                break
        report["items"] = items
        report["truncated"] = report["next_cursor"] is not None
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) != "57014":
            raise
        report.update(
            counts=None, items=[], next_cursor=None, truncated=True, timed_out=True
        )
    finally:
        db.rollback()
    report["digest"] = hashlib.sha256(
        _canonical(
            {
                "identifiers": identifiers,
                "counts": report["counts"],
                "timed_out": report["timed_out"],
            }
        )
    ).hexdigest()
    return report


def _impact_match(model, identifiers: list[str]):
    return func.lower(model.unit).in_(identifiers) | func.lower(
        model.original_unit
    ).in_(identifiers)


def unit_impact(
    db: Session, *, unit_id: int, cursor: Optional[str], limit: int
) -> dict[str, Any]:
    unit = db.get(Unit, unit_id)
    if unit is None:
        db.rollback()
        raise AdminCatalogError(404, "unit not found")
    identifiers = impact_identifiers(unit)
    report = impact_report(db, identifiers=identifiers, cursor=cursor, limit=limit)
    return dict(report, unit_id=unit_id)


def reverse_repair(db: Session, *, repair_id: str, actor: Actor) -> dict[str, Any]:
    lock_unit_catalog(db)
    commit_row = (
        db.query(AdminCatalogOperation)
        .filter(
            AdminCatalogOperation.repair_id == repair_id,
            AdminCatalogOperation.action == "unit_repair_commit",
            AdminCatalogOperation.result == "ok",
        )
        .first()
    )
    if commit_row is None:
        raise AdminCatalogError(404, "repair not found")
    already = (
        db.query(AdminCatalogOperation.id)
        .filter(
            AdminCatalogOperation.repair_id == repair_id,
            AdminCatalogOperation.action == "unit_repair_reverse",
            AdminCatalogOperation.result == "ok",
        )
        .first()
    )
    if already is not None:
        raise AdminCatalogError(409, "repair already reversed")
    revision = unit_catalog_revision(db)
    if revision != commit_row.catalog_revision_after:
        raise AdminCatalogError(
            409, "a later unit-catalog operation exists; reverse it first"
        )

    snapshot = dict(commit_row.unit_snapshot or {})
    unit = (
        db.query(Unit).filter(Unit.id == snapshot.get("id")).with_for_update().first()
    )
    if unit is None:
        raise AdminCatalogError(404, "unit not found")
    current = unit_snapshot(unit)
    current_metadata = dict(current.get("unit_metadata") or {})
    deactivation = current_metadata.pop("deactivation", None) or {}
    expected = dict(snapshot, is_active=False, unit_metadata=current_metadata)
    if (
        deactivation.get("repair_id") != repair_id
        or current != dict(expected, unit_metadata=current.get("unit_metadata"))
        or current_metadata != (snapshot.get("unit_metadata") or {})
    ):
        raise AdminCatalogError(409, "unit changed since the repair; cannot reverse")

    units = _active_units(db)
    restored_units = sorted([*units, unit], key=lambda u: u.id)
    ids_by_symbol = {u.symbol: u.id for u in restored_units}
    _, restored = _analysis_for(db, restored_units)
    restored_ids = sorted(conflict_id_for(c, ids_by_symbol) for c in restored.conflicts)
    expected_ids = sorted((commit_row.counts or {}).get("pre_conflicts", []))
    if restored_ids != expected_ids:
        raise AdminCatalogError(409, "restoring would not reproduce the prior catalog")

    unit.is_active = True
    unit.unit_metadata = snapshot.get("unit_metadata")
    row = _record(
        db,
        actor,
        action="unit_repair_reverse",
        scope="unit_catalog",
        result="ok",
        repair_id=repair_id,
        unit_ids=list(commit_row.unit_ids or []),
        catalog_revision_before=revision,
        detail=_bounded(f"reactivated {unit.symbol}"),
        unit_snapshot=current,
    )
    row.catalog_revision_after = row.id
    db.flush()
    verify_catalog_in_transaction(db, expected_ids)
    db.commit()
    return {
        "repair_id": repair_id,
        "reactivated_unit_id": unit.id,
        "catalog_revision": row.id,
    }
