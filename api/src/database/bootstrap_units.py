"""Bootstrap bundled unit definitions into PostgreSQL when needed."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

import structlog
from src.calculation.unit_database import UnitDatabase
from src.database.models import ConversionRule, Unit, UnitCategory

logger = structlog.get_logger(__name__)

UNITS_JSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "public-demo"
    / "units_database.json"
)
UNITS_BOOTSTRAP_LOCK_KEY = 640_104_215


def _with_postgres_lock(db: Session):
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return None

    db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": UNITS_BOOTSTRAP_LOCK_KEY})
    return UNITS_BOOTSTRAP_LOCK_KEY


def _release_postgres_lock(db: Session, lock_key: int | None) -> None:
    if lock_key is None:
        return
    db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


def _update_if_changed(target, **values: Any) -> int:
    updated = 0
    for key, value in values.items():
        current = getattr(target, key)
        if current != value:
            setattr(target, key, value)
            updated += 1
    return updated


def _parse_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _get_or_create_category_for_unit(
    db: Session,
    category_map: dict[str, UnitCategory],
    unit_definition,
    summary: dict[str, int],
) -> UnitCategory:
    category_name = unit_definition.category.value
    category = category_map.get(category_name)
    if category is not None:
        return category

    category = db.query(UnitCategory).filter(UnitCategory.name == category_name).first()
    if category is None:
        category = UnitCategory(
            name=category_name,
            base_unit=unit_definition.base_unit or unit_definition.symbol,
            description="",
            special_conversions=category_name == "temperature",
        )
        db.add(category)
        db.flush()
        summary["categories_seeded"] += 1

    category_map[category_name] = category
    return category


def bootstrap_units_if_empty(
    db: Session, *, units_json_path: Path = UNITS_JSON_PATH
) -> Dict[str, int]:
    """Seed or refresh bundled unit definitions from units_database.json."""

    summary = {
        "categories_before": db.query(UnitCategory).count(),
        "units_before": db.query(Unit).count(),
        "rules_before": db.query(ConversionRule).count(),
        "categories_seeded": 0,
        "categories_updated": 0,
        "units_seeded": 0,
        "units_updated": 0,
        "rules_seeded": 0,
        "rules_updated": 0,
        "categories_total": 0,
        "units_total": 0,
        "rules_total": 0,
    }

    if not units_json_path.exists():
        logger.warning("Units bootstrap file missing", path=str(units_json_path))
        summary["categories_total"] = summary["categories_before"]
        summary["units_total"] = summary["units_before"]
        summary["rules_total"] = summary["rules_before"]
        return summary

    unit_database = UnitDatabase(str(units_json_path))
    lock_key = _with_postgres_lock(db)
    try:
        database_data = getattr(unit_database, "database_data", {})
        if not isinstance(database_data, Mapping):
            database_data = {}

        categories_payload = database_data.get("categories", {})
        if not isinstance(categories_payload, Mapping):
            categories_payload = {}
        category_map: dict[str, UnitCategory] = {}

        for category_name, category_data in categories_payload.items():
            if not isinstance(category_data, Mapping):
                logger.warning(
                    "Skipping invalid unit category payload",
                    category=category_name,
                    payload=category_data,
                )
                continue
            existing = (
                db.query(UnitCategory)
                .filter(UnitCategory.name == category_name)
                .first()
            )
            if existing is None:
                existing = UnitCategory(
                    name=category_name,
                    base_unit=category_data.get("base_unit", ""),
                    description=category_data.get("description", ""),
                    special_conversions=bool(
                        category_data.get("special_conversions", False)
                    ),
                )
                db.add(existing)
                db.flush()
                summary["categories_seeded"] += 1
            else:
                summary["categories_updated"] += int(
                    _update_if_changed(
                        existing,
                        base_unit=category_data.get("base_unit", ""),
                        description=category_data.get("description", ""),
                        special_conversions=bool(
                            category_data.get("special_conversions", False)
                        ),
                    )
                    > 0
                )
            category_map[category_name] = existing

        for unit_definition in unit_database.get_unit_definitions():
            category = _get_or_create_category_for_unit(
                db, category_map, unit_definition, summary
            )
            existing = (
                db.query(Unit).filter(Unit.symbol == unit_definition.symbol).first()
            )
            metadata = dict(unit_definition.metadata)
            payload = {
                "category_id": category.id,
                "name": unit_definition.name,
                "conversion_factor": unit_definition.conversion_factor,
                "conversion_offset": unit_definition.conversion_offset,
                "aliases": list(unit_definition.aliases),
                "unit_metadata": metadata,
                "dimension_vector": metadata.get("dimension_vector"),
                "source_system": metadata.get("source_system"),
                "source_version": metadata.get("source_version"),
                "valid_from": _parse_optional_date(metadata.get("valid_from")),
                "valid_to": _parse_optional_date(metadata.get("valid_to")),
                "is_active": True,
            }
            if existing is None:
                db.add(
                    Unit(
                        symbol=unit_definition.symbol,
                        **payload,
                    )
                )
                summary["units_seeded"] += 1
            else:
                summary["units_updated"] += int(
                    _update_if_changed(existing, **payload) > 0
                )

        rules_payload = database_data.get("custom_conversion_rules", [])
        if not isinstance(rules_payload, list):
            rules_payload = []
        for rule_data in rules_payload:
            if not isinstance(rule_data, dict):
                logger.warning(
                    "Skipping invalid conversion rule payload", payload=rule_data
                )
                continue

            existing = (
                db.query(ConversionRule)
                .filter(
                    ConversionRule.from_unit == rule_data.get("from_unit"),
                    ConversionRule.to_unit == rule_data.get("to_unit"),
                )
                .first()
            )
            metadata = dict(rule_data.get("metadata", {}))
            payload = {
                "formula": rule_data["formula"],
                "reverse_formula": rule_data.get("reverse_formula"),
                "description": rule_data.get("description")
                or metadata.get("description"),
                "conditions": dict(rule_data.get("conditions", {})),
                "rule_metadata": metadata,
                "rule_hash": metadata.get("rule_hash"),
                "priority": int(metadata.get("priority", 100)),
                "valid_from": _parse_optional_date(metadata.get("valid_from")),
                "valid_to": _parse_optional_date(metadata.get("valid_to")),
                "source_system": metadata.get("source_system"),
                "source_version": metadata.get("source_version"),
                "is_active": True,
            }
            if existing is None:
                db.add(
                    ConversionRule(
                        from_unit=rule_data["from_unit"],
                        to_unit=rule_data["to_unit"],
                        **payload,
                    )
                )
                summary["rules_seeded"] += 1
            else:
                summary["rules_updated"] += int(
                    _update_if_changed(existing, **payload) > 0
                )

        db.commit()

        summary["categories_total"] = db.query(UnitCategory).count()
        summary["units_total"] = db.query(Unit).count()
        summary["rules_total"] = db.query(ConversionRule).count()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        _release_postgres_lock(db, lock_key)
