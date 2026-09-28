from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from src.services.fx_history_import import import_fx_history_rows, plan_fx_import_chunks


class RecordingService:
    def __init__(self):
        self.calls = []
        self.repository = RecordingRepository()

    def import_rates_csv_result(self, rows, **kwargs):
        self.calls.append((rows, kwargs))
        return SimpleNamespace(
            batch=SimpleNamespace(id=len(self.calls)),
            submitted_rows=len(rows),
            created_rows=len(rows),
            updated_rows=0,
            unchanged_rows=0,
        )


class RecordingRepository:
    def __init__(self):
        self.monthly_calls = []
        self.prune_calls = []

    def materialize_monthly_periods(self, **kwargs):
        self.monthly_calls.append(kwargs)
        return SimpleNamespace(
            scanned_observations=kwargs.get("scanned_observations", 0),
            created_periods=1,
            updated_periods=0,
            unchanged_periods=0,
        )

    def delete_seed_observations_not_in_keys(self, **kwargs):
        self.prune_calls.append(kwargs)
        return SimpleNamespace(scanned_rows=1, deleted_rows=1)


def _row(base_currency: str, day: int) -> dict:
    return {
        "rate_date": date(2024, 3, day),
        "base_currency": base_currency,
        "quote_currency": "EUR",
        "rate_value": Decimal(f"0.9{day:04d}"),
        "provider": "ECB",
        "rate_type": "reference",
        "rate_metadata": {"source_row": day},
    }


def test_plan_fx_import_chunks_groups_by_provider_type_and_base_currency():
    rows = [_row("USD", 3), _row("GBP", 1), _row("USD", 1), _row("USD", 2)]

    chunks = plan_fx_import_chunks(
        rows,
        max_rows=2,
        source_name="ECB history",
        source_url="https://example.test/ecb.zip",
    )

    assert [(chunk.base_currency, len(chunk.rows)) for chunk in chunks] == [
        ("GBP", 1),
        ("USD", 2),
        ("USD", 1),
    ]
    assert all(len(chunk.source_hash) == 64 for chunk in chunks)
    assert all(
        set(row["base_currency"] for row in chunk.rows) == {chunk.base_currency}
        for chunk in chunks
    )
    assert chunks[1].chunk_index == 1
    assert chunks[1].chunk_total == 2
    assert chunks[1].source_hash == chunks[2].source_hash


def test_import_fx_history_rows_chunks_writes_and_materializes_monthly_periods():
    service = RecordingService()
    rows = [_row("USD", 1), _row("USD", 2), _row("USD", 3), _row("GBP", 1)]

    summary = import_fx_history_rows(
        rows,
        service=service,
        max_rows=2,
        source_name="ECB history",
        source_url="https://example.test/ecb.zip",
        created_by="test",
        materialize_monthly=True,
    )

    assert summary.total_rows == 4
    assert summary.chunk_count == 3
    assert summary.created_rows == 4
    assert summary.updated_rows == 0
    assert summary.unchanged_rows == 0
    assert summary.seed_rows_pruned == 1
    assert summary.monthly_periods_created == 2
    assert len(service.calls) == 3
    assert [call[1]["base_currency"] for call in service.calls] == ["GBP", "USD", "USD"]
    assert all(len(call[0]) <= 2 for call in service.calls)
    assert all(len(call[1]["source_hash"]) == 64 for call in service.calls)
    assert len(service.repository.prune_calls) == 1
    assert len(service.repository.prune_calls[0]["allowed_keys"]) == 4
    assert service.repository.prune_calls[0]["seed_name"] == "ECB history"
    assert service.repository.monthly_calls == [
        {
            "provider": "ECB",
            "rate_type": "reference",
            "base_currency": "GBP",
            "quote_currency": "EUR",
            "start": date(2024, 3, 1),
            "end": date(2024, 3, 1),
        },
        {
            "provider": "ECB",
            "rate_type": "reference",
            "base_currency": "USD",
            "quote_currency": "EUR",
            "start": date(2024, 3, 1),
            "end": date(2024, 3, 3),
        },
    ]


def test_import_fx_history_rows_dry_run_does_not_write():
    service = RecordingService()

    summary = import_fx_history_rows(
        [_row("USD", 1), _row("USD", 2)],
        service=service,
        max_rows=1,
        source_name="ECB history",
        source_url="https://example.test/ecb.zip",
        dry_run=True,
    )

    assert summary.total_rows == 2
    assert summary.chunk_count == 2
    assert summary.dry_run is True
    assert summary.created_rows == 0
    assert service.calls == []
    assert service.repository.monthly_calls == []
