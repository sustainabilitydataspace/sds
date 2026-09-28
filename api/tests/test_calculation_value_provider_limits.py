from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.calculation.contracts import CalculationContractInput, ContractExecutionError
from src.calculation.value_provider import StoreObservationProvider


def _input() -> CalculationContractInput:
    return CalculationContractInput(
        local_variable="energy",
        concept="urn:sds:energy",
        required=True,
    )


def _row(index: int):
    return SimpleNamespace(
        id=f"value-{index}",
        concept="urn:sds:energy",
        entity="facility",
        period=date(2024, 1, index),
        value=index,
        unit="MWh",
        value_type="numeric",
    )


def test_store_provider_stops_iterating_after_the_observation_limit() -> None:
    consumed: list[int] = []

    class IteratingStore:
        def iterate(self, **_filters):
            for index in range(1, 10_000):
                consumed.append(index)
                yield _row(index)

    provider = StoreObservationProvider(IteratingStore(), max_observations=3)

    with pytest.raises(ContractExecutionError, match="observation limit of 3"):
        provider.get_snapshot(
            _input(),
            entities=["facility"],
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )

    assert consumed == [1, 2, 3, 4]


def test_store_provider_rejects_an_oversized_paged_query_before_loading_all_pages() -> (
    None
):
    calls: list[int] = []

    class PagedStore:
        def list(self, **filters):
            calls.append(filters["offset"])
            return [_row(1), _row(2)], 4

    provider = StoreObservationProvider(PagedStore(), page_size=2, max_observations=3)

    with pytest.raises(ContractExecutionError, match="observation limit of 3"):
        provider.get_snapshot(
            _input(),
            entities=["facility"],
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )

    assert calls == [0]


def test_store_provider_uses_one_bounded_database_snapshot_for_all_entities() -> None:
    calls: list[dict] = []

    class SnapshotStore:
        def iterate_snapshot(self, **filters):
            calls.append(filters)
            return [_row(1), _row(2)]

        def iterate(self, **_filters):
            raise AssertionError(
                "offset iteration must not run when a snapshot seam exists"
            )

    provider = StoreObservationProvider(SnapshotStore(), max_observations=3)
    snapshot = provider.get_snapshot(
        _input(),
        entities=["facility", "warehouse"],
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )

    assert [record.value_id for record in snapshot.observations] == [
        "value-1",
        "value-2",
    ]
    assert len(calls) == 1
    assert calls[0]["entities"] == ["facility", "warehouse"]
    assert calls[0]["limit"] == 4


def test_store_provider_rejects_an_oversized_database_snapshot() -> None:
    class SnapshotStore:
        def iterate_snapshot(self, **_filters):
            return [_row(index) for index in range(1, 5)]

    provider = StoreObservationProvider(SnapshotStore(), max_observations=3)

    with pytest.raises(ContractExecutionError, match="observation limit of 3"):
        provider.get_snapshot(
            _input(),
            entities=["facility"],
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
        )


def test_store_provider_requires_positive_collection_limits() -> None:
    with pytest.raises(ValueError, match="page_size must be at least 1"):
        StoreObservationProvider(object(), page_size=0)

    with pytest.raises(ValueError, match="max_observations must be at least 1"):
        StoreObservationProvider(object(), max_observations=0)
