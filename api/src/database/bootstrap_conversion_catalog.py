"""Bootstrap deterministic conversion catalog reference data."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session

import structlog
from src.database.models import (
    Currency,
    FXPolicy,
    FXRateBatch,
    FXRateObservation,
    FXRatePeriod,
)

logger = structlog.get_logger(__name__)

CURRENCIES_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "public-demo"
    / "currencies_seed.json"
)
CONVERSION_BOOTSTRAP_LOCK_KEY = 640_104_216
DEFAULT_FX_POLICY_ID = "ecb-reference-monthly-average"
DEFAULT_FX_POLICY_SELECTION_MODE = "monthly_average"


def _with_postgres_lock(db: Session):
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return None

    db.execute(
        text("SELECT pg_advisory_lock(:key)"),
        {"key": CONVERSION_BOOTSTRAP_LOCK_KEY},
    )
    return CONVERSION_BOOTSTRAP_LOCK_KEY


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


def bootstrap_conversion_catalog_if_empty(
    db: Session,
    *,
    currencies_seed_path: Path = CURRENCIES_SEED_PATH,
) -> Dict[str, Any]:
    """Seed or refresh the bundled currency and FX policy catalog."""

    summary: Dict[str, Any] = {
        "currencies_before": db.query(Currency).count(),
        "policies_before": db.query(FXPolicy).count(),
        "rate_batches_before": db.query(FXRateBatch).count(),
        "rate_observations_before": db.query(FXRateObservation).count(),
        "rate_periods_before": db.query(FXRatePeriod).count(),
        "currencies_created": 0,
        "currencies_updated": 0,
        "policies_created": 0,
        "policies_updated": 0,
        "rate_batches_created": 0,
        "rate_observations_created": 0,
        "rate_observations_updated": 0,
        "rate_periods_created": 0,
        "rate_periods_updated": 0,
        "currency_codes": [],
        "currencies_total": 0,
        "policies_total": 0,
        "rate_batches_total": 0,
        "rate_observations_total": 0,
        "rate_periods_total": 0,
    }

    if not currencies_seed_path.exists():
        logger.warning(
            "Conversion catalog bootstrap seed file missing",
            currencies_path=str(currencies_seed_path),
        )
        _set_totals(summary, db)
        return summary

    currencies_payload = _load_currency_seed(currencies_seed_path)
    lock_key = _with_postgres_lock(db)
    try:
        for currency in currencies_payload:
            code = currency["code"]
            existing = _first_or_none(db, Currency, Currency.code == code)
            payload = {
                "numeric_code": currency.get("numeric_code"),
                "name": currency["name"],
                "minor_units": int(currency["minor_units"]),
                "valid_from": _parse_optional_date(currency.get("valid_from")),
                "valid_to": _parse_optional_date(currency.get("valid_to")),
                "redenomination_of": currency.get("redenomination_of"),
                "redenomination_factor": _parse_optional_decimal(
                    currency.get("redenomination_factor")
                ),
                "currency_metadata": dict(currency.get("currency_metadata") or {}),
                "is_active": bool(currency.get("is_active", True)),
            }
            if existing is None:
                db.add(Currency(code=code, **payload))
                summary["currencies_created"] += 1
            else:
                summary["currencies_updated"] += int(
                    _update_if_changed(existing, **payload) > 0
                )

        summary["currency_codes"] = sorted(row["code"] for row in currencies_payload)

        for policy_id, policy_payload in _fx_policy_seed_rows():
            existing_policy = _first_or_none(db, FXPolicy, FXPolicy.id == policy_id)
            if existing_policy is None:
                db.add(FXPolicy(id=policy_id, **policy_payload))
                summary["policies_created"] += 1
            else:
                summary["policies_updated"] += int(
                    _update_if_changed(existing_policy, **policy_payload) > 0
                )

        db.commit()
        _set_totals(summary, db)
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        _release_postgres_lock(db, lock_key)


def _load_currency_seed(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("currency seed must be a list")
    return [_normalize_currency_row(row) for row in payload]


def _normalize_currency_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("currency seed rows must be objects")
    code = str(row.get("code", "")).strip().upper()
    if len(code) != 3:
        raise ValueError(f"invalid currency code in seed: {code!r}")
    return {**row, "code": code}


def _fx_policy_seed_rows() -> list[tuple[str, dict[str, Any]]]:
    shared_payload = {
        "rate_type": "reference",
        "selection_mode": DEFAULT_FX_POLICY_SELECTION_MODE,
        "business_day_rule": "previous_available",
        "triangulation_allowed": False,
        "fallback_behavior": "fail_closed",
        "rounding_scale": 6,
        "rounding_mode": "ROUND_HALF_UP",
        "is_active": True,
    }
    return [
        (
            DEFAULT_FX_POLICY_ID,
            {
                **shared_payload,
                "name": "ECB reference monthly average",
                "provider": "ECB",
                "policy_metadata": {
                    "source": "ECB operational history loader required",
                    "loader": "scripts/load_ecb_fx_history.py",
                },
            },
        ),
    ]


def _parse_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _first_or_none(db: Session, model, *criteria):
    row = db.query(model).filter(*criteria).first()
    return row if isinstance(row, model) else None


def _set_totals(summary: Dict[str, Any], db: Session) -> None:
    summary["currencies_total"] = db.query(Currency).count()
    summary["policies_total"] = db.query(FXPolicy).count()
    summary["rate_batches_total"] = db.query(FXRateBatch).count()
    summary["rate_observations_total"] = db.query(FXRateObservation).count()
    summary["rate_periods_total"] = db.query(FXRatePeriod).count()
