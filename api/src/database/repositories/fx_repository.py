"""Repository adapters for persisted FX rates and policies."""

from __future__ import annotations

import calendar
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import MultipleResultsFound

from src.calculation.conversion.fx import FXAmbiguousRateError, FXPolicy
from src.database.models import FXPolicy as DBFXPolicy
from src.database.models import FXRateBatch, FXRateObservation, FXRatePeriod

RATE_SCALE = Decimal("0.000001")
MONTHLY_AVERAGE_PERIOD_TYPE = "monthly_average"


@dataclass(frozen=True)
class FXRateBatchImportResult:
    batch: FXRateBatch | None
    submitted_rows: int
    created_rows: int
    updated_rows: int
    unchanged_rows: int


@dataclass(frozen=True)
class FXRatePeriodMaterializationResult:
    provider: str
    rate_type: str
    base_currency: str
    quote_currency: str
    scanned_observations: int
    created_periods: int
    updated_periods: int
    unchanged_periods: int


@dataclass(frozen=True)
class FXSeedPruneResult:
    scanned_rows: int
    deleted_rows: int


class FXRepository:
    """Persist and query FX rates using the Phase 2 composite lookup keys."""

    def __init__(self, db: Session):
        self.db = db

    def find_daily_rate(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        rate_date,
    ) -> FXRateObservation | None:
        try:
            return (
                self.db.query(FXRateObservation)
                .filter(
                    FXRateObservation.provider == provider,
                    FXRateObservation.rate_type == rate_type,
                    FXRateObservation.base_currency == base_currency,
                    FXRateObservation.quote_currency == quote_currency,
                    FXRateObservation.rate_date == rate_date,
                )
                .one_or_none()
            )
        except MultipleResultsFound as exc:
            raise FXAmbiguousRateError("ambiguous FX daily rate") from exc

    def find_previous_daily_rate(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        rate_date,
    ) -> FXRateObservation | None:
        return (
            self.db.query(FXRateObservation)
            .filter(
                FXRateObservation.provider == provider,
                FXRateObservation.rate_type == rate_type,
                FXRateObservation.base_currency == base_currency,
                FXRateObservation.quote_currency == quote_currency,
                FXRateObservation.rate_date <= rate_date,
            )
            .order_by(FXRateObservation.rate_date.desc(), FXRateObservation.id.desc())
            .first()
        )

    def find_period_rate(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        period_type: str,
        period_start,
        period_end,
    ) -> FXRatePeriod | None:
        try:
            return (
                self.db.query(FXRatePeriod)
                .filter(
                    FXRatePeriod.provider == provider,
                    FXRatePeriod.rate_type == rate_type,
                    FXRatePeriod.base_currency == base_currency,
                    FXRatePeriod.quote_currency == quote_currency,
                    FXRatePeriod.period_type == period_type,
                    FXRatePeriod.period_start == period_start,
                    FXRatePeriod.period_end == period_end,
                )
                .one_or_none()
            )
        except MultipleResultsFound as exc:
            raise FXAmbiguousRateError("ambiguous FX period rate") from exc

    def get_policy(self, policy_id: str) -> FXPolicy | None:
        row = (
            self.db.query(DBFXPolicy)
            .filter(DBFXPolicy.id == policy_id, DBFXPolicy.is_active.is_(True))
            .one_or_none()
        )
        if row is None:
            return None
        return FXPolicy(
            id=row.id,
            provider=row.provider,
            rate_type=row.rate_type,
            selection_mode=row.selection_mode,
            business_day_rule=row.business_day_rule,
            triangulation_allowed=bool(row.triangulation_allowed),
            fallback_behavior=row.fallback_behavior,
            rounding_scale=int(row.rounding_scale),
            rounding_mode=row.rounding_mode,
        )

    def save_rate_batch(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        source_hash: str,
        created_by: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        batch_metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> FXRateBatch | None:
        result = self.save_rate_batch_result(
            rows,
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
            source_hash=source_hash,
            created_by=created_by,
            source_name=source_name,
            source_url=source_url,
            batch_metadata=batch_metadata,
            commit=commit,
        )
        return result.batch

    def save_rate_batch_result(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        source_hash: str,
        created_by: str | None = None,
        source_name: str | None = None,
        source_url: str | None = None,
        batch_metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> FXRateBatchImportResult:
        row_list = list(rows)
        existing_by_key = self._existing_observations_for_batch(
            row_list,
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
        )
        pending: list[tuple[str, Mapping[str, Any], FXRateObservation | None]] = []
        unchanged_rows = 0
        for row in row_list:
            key = self._observation_key(
                provider=provider,
                rate_type=rate_type,
                base_currency=base_currency,
                row=row,
            )
            existing = existing_by_key.get(key)
            if existing is None:
                pending.append(("create", row, None))
            elif self._observation_needs_update(existing, row, source_hash):
                pending.append(("update", row, existing))
            else:
                unchanged_rows += 1

        if not pending:
            return FXRateBatchImportResult(
                batch=None,
                submitted_rows=len(row_list),
                created_rows=0,
                updated_rows=0,
                unchanged_rows=unchanged_rows,
            )

        try:
            batch = FXRateBatch(
                provider=provider,
                source_name=source_name,
                source_url=source_url,
                rate_type=rate_type,
                base_currency=base_currency,
                source_hash=source_hash,
                created_by=created_by,
                batch_metadata=batch_metadata or {},
            )
            self.db.add(batch)
            flush = getattr(self.db, "flush", None)
            if callable(flush):
                flush()

            created_rows = 0
            updated_rows = 0
            for operation, row, existing in pending:
                if operation == "create":
                    observation = FXRateObservation(
                        batch_id=getattr(batch, "id", None),
                        base_currency=base_currency,
                        quote_currency=row["quote_currency"],
                        rate_date=row["rate_date"],
                        rate_value=row["rate_value"],
                        provider=provider,
                        rate_type=rate_type,
                        source_hash=source_hash,
                        observed_at=row.get("observed_at"),
                        rate_metadata=row.get("rate_metadata") or {},
                    )
                    self.db.add(observation)
                    created_rows += 1
                    continue

                assert existing is not None
                self._update_if_changed(
                    existing,
                    batch_id=getattr(batch, "id", None),
                    rate_value=row["rate_value"],
                    source_hash=source_hash,
                    observed_at=row.get("observed_at"),
                    rate_metadata=row.get("rate_metadata") or {},
                )
                updated_rows += 1

            if commit:
                self.db.commit()
                refresh = getattr(self.db, "refresh", None)
                if callable(refresh):
                    refresh(batch)
            return FXRateBatchImportResult(
                batch=batch,
                submitted_rows=len(row_list),
                created_rows=created_rows,
                updated_rows=updated_rows,
                unchanged_rows=unchanged_rows,
            )
        except Exception:
            self.db.rollback()
            raise

    def list_rate_coverage(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        start,
        end,
    ) -> list[FXRateObservation]:
        return (
            self.db.query(FXRateObservation)
            .filter(
                FXRateObservation.provider == provider,
                FXRateObservation.rate_type == rate_type,
                FXRateObservation.base_currency == base_currency,
                FXRateObservation.quote_currency == quote_currency,
                FXRateObservation.rate_date >= start,
                FXRateObservation.rate_date <= end,
            )
            .order_by(FXRateObservation.rate_date, FXRateObservation.id)
            .all()
        )

    def materialize_monthly_periods(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        start: date | None = None,
        end: date | None = None,
        commit: bool = True,
    ) -> FXRatePeriodMaterializationResult:
        query = self.db.query(FXRateObservation).filter(
            FXRateObservation.provider == provider,
            FXRateObservation.rate_type == rate_type,
            FXRateObservation.base_currency == base_currency,
            FXRateObservation.quote_currency == quote_currency,
        )
        if start is not None:
            query = query.filter(FXRateObservation.rate_date >= start)
        if end is not None:
            query = query.filter(FXRateObservation.rate_date <= end)
        observations = sorted(
            query.order_by(FXRateObservation.rate_date, FXRateObservation.id).all()
            or [],
            key=lambda row: (row.rate_date, getattr(row, "id", 0) or 0),
        )

        existing_periods = self._existing_monthly_periods(
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
            quote_currency=quote_currency,
            start=start,
            end=end,
        )
        by_month: dict[tuple[date, date], list[FXRateObservation]] = defaultdict(list)
        for observation in observations:
            month_start = date(
                observation.rate_date.year, observation.rate_date.month, 1
            )
            month_end = date(
                observation.rate_date.year,
                observation.rate_date.month,
                calendar.monthrange(
                    observation.rate_date.year,
                    observation.rate_date.month,
                )[1],
            )
            by_month[(month_start, month_end)].append(observation)

        created_periods = 0
        updated_periods = 0
        unchanged_periods = 0
        try:
            for (period_start, period_end), period_observations in sorted(
                by_month.items()
            ):
                rate_value = self._average_rate(period_observations)
                source_observation_ids = [
                    getattr(observation, "id", None)
                    for observation in period_observations
                ]
                source_hash = self._period_source_hash(
                    provider=provider,
                    rate_type=rate_type,
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    period_start=period_start,
                    period_end=period_end,
                    observations=period_observations,
                )
                key = (period_start, period_end)
                existing = existing_periods.get(key)
                payload = {
                    "rate_value": rate_value,
                    "source_observation_ids": source_observation_ids,
                    "source_hash": source_hash,
                }
                if existing is None:
                    self.db.add(
                        FXRatePeriod(
                            provider=provider,
                            rate_type=rate_type,
                            base_currency=base_currency,
                            quote_currency=quote_currency,
                            period_type=MONTHLY_AVERAGE_PERIOD_TYPE,
                            period_start=period_start,
                            period_end=period_end,
                            **payload,
                        )
                    )
                    created_periods += 1
                    continue
                if self._update_if_changed(existing, **payload) > 0:
                    updated_periods += 1
                else:
                    unchanged_periods += 1

            if commit and (created_periods or updated_periods):
                self.db.commit()
            return FXRatePeriodMaterializationResult(
                provider=provider,
                rate_type=rate_type,
                base_currency=base_currency,
                quote_currency=quote_currency,
                scanned_observations=len(observations),
                created_periods=created_periods,
                updated_periods=updated_periods,
                unchanged_periods=unchanged_periods,
            )
        except Exception:
            self.db.rollback()
            raise

    def delete_seed_observations_not_in_keys(
        self,
        *,
        provider: str,
        rate_type: str,
        allowed_keys: set[tuple[str, str, str, str, date]],
        seed_name: str,
        commit: bool = True,
    ) -> FXSeedPruneResult:
        rows = (
            self.db.query(FXRateObservation)
            .filter(
                FXRateObservation.provider == provider,
                FXRateObservation.rate_type == rate_type,
            )
            .all()
            or []
        )
        scanned_rows = 0
        deleted_rows = 0
        try:
            for row in rows:
                metadata = getattr(row, "rate_metadata", None) or {}
                if metadata.get("seed") != seed_name:
                    continue
                scanned_rows += 1
                key = (
                    row.provider,
                    row.rate_type,
                    row.base_currency,
                    row.quote_currency,
                    row.rate_date,
                )
                if key in allowed_keys:
                    continue
                self.db.delete(row)
                deleted_rows += 1
            if commit and deleted_rows:
                self.db.commit()
            return FXSeedPruneResult(
                scanned_rows=scanned_rows,
                deleted_rows=deleted_rows,
            )
        except Exception:
            self.db.rollback()
            raise

    def _existing_observations_for_batch(
        self,
        rows: list[Mapping[str, Any]],
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
    ) -> dict[tuple[str, str, str, str, Any], FXRateObservation]:
        if not rows:
            return {}
        desired_keys = {
            self._observation_key(
                provider=provider,
                rate_type=rate_type,
                base_currency=base_currency,
                row=row,
            )
            for row in rows
        }
        quote_currencies = sorted({str(row["quote_currency"]) for row in rows})
        dates = [row["rate_date"] for row in rows]
        query = self.db.query(FXRateObservation).filter(
            FXRateObservation.provider == provider,
            FXRateObservation.rate_type == rate_type,
            FXRateObservation.base_currency == base_currency,
            FXRateObservation.quote_currency.in_(quote_currencies),
            FXRateObservation.rate_date >= min(dates),
            FXRateObservation.rate_date <= max(dates),
        )
        return {
            key: row
            for row in (query.all() or [])
            if (
                key := (
                    row.provider,
                    row.rate_type,
                    row.base_currency,
                    row.quote_currency,
                    row.rate_date,
                )
            )
            in desired_keys
        }

    def _observation_key(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        row: Mapping[str, Any],
    ) -> tuple[str, str, str, str, Any]:
        return (
            provider,
            rate_type,
            base_currency,
            str(row["quote_currency"]),
            row["rate_date"],
        )

    def _observation_needs_update(
        self,
        observation: FXRateObservation,
        row: Mapping[str, Any],
        source_hash: str,
    ) -> bool:
        return any(
            (
                Decimal(str(observation.rate_value)) != Decimal(str(row["rate_value"])),
                getattr(observation, "source_hash", None) != source_hash,
                getattr(observation, "observed_at", None) != row.get("observed_at"),
                (getattr(observation, "rate_metadata", None) or {})
                != (row.get("rate_metadata") or {}),
            )
        )

    def _existing_monthly_periods(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        start: date | None,
        end: date | None,
    ) -> dict[tuple[date, date], FXRatePeriod]:
        query = self.db.query(FXRatePeriod).filter(
            FXRatePeriod.provider == provider,
            FXRatePeriod.rate_type == rate_type,
            FXRatePeriod.base_currency == base_currency,
            FXRatePeriod.quote_currency == quote_currency,
            FXRatePeriod.period_type == MONTHLY_AVERAGE_PERIOD_TYPE,
        )
        if start is not None:
            query = query.filter(FXRatePeriod.period_end >= start)
        if end is not None:
            query = query.filter(FXRatePeriod.period_start <= end)
        return {(row.period_start, row.period_end): row for row in (query.all() or [])}

    def _average_rate(self, observations: list[FXRateObservation]) -> Decimal:
        total = sum(
            (Decimal(str(observation.rate_value)) for observation in observations),
            Decimal("0"),
        )
        return (total / Decimal(len(observations))).quantize(
            RATE_SCALE, rounding=ROUND_HALF_UP
        )

    def _period_source_hash(
        self,
        *,
        provider: str,
        rate_type: str,
        base_currency: str,
        quote_currency: str,
        period_start: date,
        period_end: date,
        observations: list[FXRateObservation],
    ) -> str:
        payload = {
            "provider": provider,
            "rate_type": rate_type,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "period_type": MONTHLY_AVERAGE_PERIOD_TYPE,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "observations": [
                {
                    "id": getattr(observation, "id", None),
                    "rate_date": observation.rate_date.isoformat(),
                    "rate_value": str(observation.rate_value),
                    "source_hash": getattr(observation, "source_hash", None),
                }
                for observation in observations
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _update_if_changed(self, target, **values: Any) -> int:
        updated = 0
        for key, value in values.items():
            if getattr(target, key, None) != value:
                setattr(target, key, value)
                updated += 1
        return updated
