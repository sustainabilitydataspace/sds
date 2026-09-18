"""Typed observation providers for contract-first calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Protocol

from src.calculation.contracts import CalculationContractInput, ContractExecutionError


@dataclass(frozen=True)
class ObservationRecord:
    """Raw numeric observation used as calculation input."""

    value_id: str
    concept: str
    entity: str
    period: date
    value: Decimal
    unit: str
    value_type: str = "numeric"
    currency: str | None = None
    original_currency: str | None = None
    value_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    conversion_trace: list[dict[str, Any]] | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ObservationSnapshot:
    """Typed raw observation slice for one contract input."""

    local_variable: str
    concept: str
    entities: tuple[str, ...]
    period_start: date
    period_end: date
    observations: tuple[ObservationRecord, ...]


class ObservationProvider(Protocol):
    """Runtime provider that returns raw observations, not pre-summed scalars."""

    def get_snapshot(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> ObservationSnapshot:
        """Return a typed observation snapshot for a contract input."""

    def get_observations(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> list[ObservationRecord]:
        """Return raw observations for a contract input."""


class StoreObservationProvider:
    """Observation provider backed by the existing value store interface."""

    def __init__(self, store, *, page_size: int = 1000):
        self.store = store
        self.page_size = page_size

    def get_observations(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> list[ObservationRecord]:
        return list(
            self.get_snapshot(
                contract_input,
                entities=entities,
                period_start=period_start,
                period_end=period_end,
            ).observations
        )

    def get_snapshot(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> ObservationSnapshot:
        records: list[ObservationRecord] = []
        for entity in entities:
            if hasattr(self.store, "iterate"):
                records.extend(
                    _record_from_store_item(item, contract_input=contract_input)
                    for item in self.store.iterate(
                        concept=contract_input.concept,
                        entity=entity,
                        period_start=period_start,
                        period_end=period_end,
                        unit=None,
                        changed_since=None,
                    )
                )
                continue

            offset = 0
            while True:
                page, total = self.store.list(
                    concept=contract_input.concept,
                    entity=entity,
                    period_start=period_start,
                    period_end=period_end,
                    unit=None,
                    limit=self.page_size,
                    offset=offset,
                )
                records.extend(
                    _record_from_store_item(item, contract_input=contract_input)
                    for item in page
                )
                offset += len(page)
                if not page or offset >= total:
                    break
        return ObservationSnapshot(
            local_variable=contract_input.local_variable,
            concept=contract_input.concept,
            entities=tuple(entities),
            period_start=period_start,
            period_end=period_end,
            observations=tuple(
                sorted(
                    _dedupe_observations_by_value_id(records),
                    key=lambda item: (item.period, item.value_id),
                )
            ),
        )


class StaticObservationProvider:
    """In-memory provider for tests and non-DB runtime seams."""

    def __init__(self, observations: Iterable[ObservationRecord]):
        self._observations = list(observations)

    def replace(self, observations: Iterable[ObservationRecord]) -> None:
        self._observations = list(observations)

    def get_observations(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> list[ObservationRecord]:
        return list(
            self.get_snapshot(
                contract_input,
                entities=entities,
                period_start=period_start,
                period_end=period_end,
            ).observations
        )

    def get_snapshot(
        self,
        contract_input: CalculationContractInput,
        *,
        entities: list[str],
        period_start: date,
        period_end: date,
    ) -> ObservationSnapshot:
        observations = sorted(
            [
                item
                for item in self._observations
                if item.concept == contract_input.concept
                and item.entity in set(entities)
                and period_start <= item.period <= period_end
            ],
            key=lambda item: (item.period, item.value_id),
        )
        return ObservationSnapshot(
            local_variable=contract_input.local_variable,
            concept=contract_input.concept,
            entities=tuple(entities),
            period_start=period_start,
            period_end=period_end,
            observations=tuple(observations),
        )


def _record_from_store_item(
    item: Any, *, contract_input: CalculationContractInput
) -> ObservationRecord:
    value_type = str(getattr(item, "value_type", "numeric") or "numeric").lower()
    value = getattr(item, "value", None)
    if value_type not in {"numeric", "number"} or isinstance(value, (bool, str)):
        raise ContractExecutionError(
            f"Non-numeric value for {contract_input.concept} cannot be used in a calculation"
        )
    if value is None:
        raise ContractExecutionError(
            f"Missing numeric value for {contract_input.concept}"
        )

    return ObservationRecord(
        value_id=str(getattr(item, "id")),
        concept=str(getattr(item, "concept")),
        entity=str(getattr(item, "entity")),
        period=getattr(item, "period"),
        value=Decimal(str(value)),
        unit=str(getattr(item, "unit")),
        value_type=value_type,
        currency=getattr(item, "currency", None),
        original_currency=getattr(item, "original_currency", None),
        value_date=getattr(item, "value_date", None),
        period_start=getattr(item, "period_start", None),
        period_end=getattr(item, "period_end", None),
        conversion_trace=getattr(item, "conversion_trace", None),
        updated_at=getattr(item, "updated_at", None),
    )


def _dedupe_observations_by_value_id(
    observations: list[ObservationRecord],
) -> list[ObservationRecord]:
    """Keep one operational observation when revision rows project the same value id."""

    by_value_id: dict[str, ObservationRecord] = {}
    for observation in observations:
        current = by_value_id.get(observation.value_id)
        if current is None or _observation_precedence(observation) > (
            _observation_precedence(current)
        ):
            by_value_id[observation.value_id] = observation
    return list(by_value_id.values())


def _observation_precedence(observation: ObservationRecord) -> tuple[str, str]:
    updated_at = observation.updated_at.isoformat() if observation.updated_at else ""
    return updated_at, observation.value_id
