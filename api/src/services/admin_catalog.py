"""Admin calculation-contract import and unit-catalog repair services.

Every mutation records an ``admin_catalog_operations`` row in the same
transaction. The highest ``ok`` row in scope ``unit_catalog`` is the catalog
revision that API processes compare before serving unit lookups.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
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
)
from src.services.calculation_contract_import import (
    CalculationContractImportError,
    import_calculation_contract_bytes,
    load_calculation_contract_bytes,
    package_hash_for_payload,
)

REPAIR_PLAN_TTL = timedelta(minutes=10)
MAX_CONFLICTS_PAGE = 100
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


def _plan_key() -> bytes:
    """Server-held key: plans cannot be forged or altered by clients."""
    from src.config.settings import settings

    secret = settings.export_signing_secret.get_secret_value().encode()
    return hmac.new(secret, b"sds-unit-repair-plan-v1", hashlib.sha256).digest()


def plan_digest(plan: dict[str, Any]) -> str:
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hmac.new(_plan_key(), canonical.encode(), hashlib.sha256).hexdigest()


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


def _check_repair(
    db: Session,
    *,
    conflict_id: str,
    deactivate_unit_id: int,
    retain_unit_id: int,
    lock: bool = False,
) -> tuple[Unit, Unit, list[str], list[str]]:
    """Validate every repair precondition; return units and conflict ids."""
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
    if Decimal(target.conversion_factor) != Decimal(retained.conversion_factor):
        raise AdminCatalogError(422, "units have different conversion factors")
    if Decimal(target.conversion_offset or 0) != Decimal(
        retained.conversion_offset or 0
    ):
        raise AdminCatalogError(422, "units have different conversion offsets")
    if physical[target.symbol].dimension != physical[retained.symbol].dimension:
        raise AdminCatalogError(422, "units have different dimensions")

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
    return target, retained, pre_ids, post_ids


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


def preview_repair(
    db: Session,
    *,
    conflict_id: str,
    deactivate_unit_id: int,
    retain_unit_id: int,
    reason: str,
) -> dict[str, Any]:
    target, retained, pre_ids, post_ids = _check_repair(
        db,
        conflict_id=conflict_id,
        deactivate_unit_id=deactivate_unit_id,
        retain_unit_id=retain_unit_id,
    )
    plan = {
        "repair_id": uuid.uuid4().hex,
        "conflict_id": conflict_id,
        "deactivate_unit_id": target.id,
        "deactivate_symbol": target.symbol,
        "retain_unit_id": retained.id,
        "retain_symbol": retained.symbol,
        "reason": reason,
        "before_image": unit_snapshot(target),
        "catalog_revision": unit_catalog_revision(db),
        "expires_at": (datetime.now(timezone.utc) + REPAIR_PLAN_TTL).isoformat(),
        "pre_repair_conflicts": pre_ids,
        "post_repair_conflicts": post_ids,
    }
    db.rollback()
    return {"plan": plan, "plan_digest": plan_digest(plan)}


def commit_repair(
    db: Session, *, plan: dict[str, Any], digest: str, actor: Actor
) -> dict[str, Any]:
    if not hmac.compare_digest(plan_digest(plan), digest):
        raise AdminCatalogError(422, "plan digest does not match")
    expires_at = _validate_plan_fields(plan)
    if datetime.now(timezone.utc) > expires_at:
        raise AdminCatalogError(409, "repair plan expired; preview again")
    # Revision check and mutation happen under one catalog-wide lock.
    lock_unit_catalog(db)
    revision = unit_catalog_revision(db)
    if revision != plan.get("catalog_revision"):
        raise AdminCatalogError(409, "unit catalog changed; preview again")

    target, retained, pre_ids, post_ids = _check_repair(
        db,
        conflict_id=plan["conflict_id"],
        deactivate_unit_id=int(plan["deactivate_unit_id"]),
        retain_unit_id=int(plan["retain_unit_id"]),
        lock=True,
    )
    snapshot = unit_snapshot(target)
    if snapshot != plan.get("before_image"):
        raise AdminCatalogError(409, "unit changed since preview; preview again")

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
        counts={"pre_conflicts": pre_ids, "post_conflicts": post_ids},
        detail=_bounded(f"deactivated {target.symbol} in favour of {retained.symbol}"),
        unit_snapshot=snapshot,
    )
    row.catalog_revision_after = row.id
    db.commit()
    return {
        "repair_id": plan["repair_id"],
        "deactivated_unit_id": target.id,
        "retained_unit_id": retained.id,
        "catalog_revision": row.id,
        "remaining_conflicts": post_ids,
    }


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
    db.commit()
    return {
        "repair_id": repair_id,
        "reactivated_unit_id": unit.id,
        "catalog_revision": row.id,
    }
