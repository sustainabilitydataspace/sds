"""Operational import planning for historical FX rate rows."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

DEFAULT_MAX_ROWS_PER_CHUNK = 10_000


@dataclass(frozen=True)
class FXImportChunk:
    provider: str
    rate_type: str
    base_currency: str
    chunk_index: int
    chunk_total: int
    rows: list[dict[str, Any]]
    source_hash: str
    source_name: str
    source_url: str | None


@dataclass
class FXHistoryImportSummary:
    total_rows: int
    group_count: int
    chunk_count: int
    dry_run: bool
    created_rows: int = 0
    updated_rows: int = 0
    unchanged_rows: int = 0
    batches_created: int = 0
    monthly_periods_created: int = 0
    monthly_periods_updated: int = 0
    monthly_periods_unchanged: int = 0
    seed_rows_pruned: int = 0
    chunks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_fx_import_chunks(
    rows: Iterable[Mapping[str, Any]],
    *,
    max_rows: int = DEFAULT_MAX_ROWS_PER_CHUNK,
    source_name: str,
    source_url: str | None = None,
) -> list[FXImportChunk]:
    """Group FX rows by import key and split them into deterministic chunks."""

    if max_rows < 1 or max_rows > DEFAULT_MAX_ROWS_PER_CHUNK:
        raise ValueError(f"max_rows must be between 1 and {DEFAULT_MAX_ROWS_PER_CHUNK}")

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = _normalize_rate_row(row)
        grouped[
            (
                normalized["provider"],
                normalized["rate_type"],
                normalized["base_currency"],
            )
        ].append(normalized)

    chunks: list[FXImportChunk] = []
    for (provider, rate_type, base_currency), group_rows in sorted(grouped.items()):
        ordered_rows = sorted(
            group_rows,
            key=lambda item: (
                item["quote_currency"],
                item["rate_date"],
                str(item["rate_value"]),
            ),
        )
        split_rows = [
            ordered_rows[index : index + max_rows]
            for index in range(0, len(ordered_rows), max_rows)
        ]
        source_hash = _source_hash_for_rows(
            ordered_rows,
            provider=provider,
            rate_type=rate_type,
            base_currency=base_currency,
            source_name=source_name,
            source_url=source_url,
        )
        for chunk_index, chunk_rows in enumerate(split_rows, start=1):
            chunks.append(
                FXImportChunk(
                    provider=provider,
                    rate_type=rate_type,
                    base_currency=base_currency,
                    chunk_index=chunk_index,
                    chunk_total=len(split_rows),
                    rows=chunk_rows,
                    source_hash=source_hash,
                    source_name=source_name,
                    source_url=source_url,
                )
            )
    return chunks


def import_fx_history_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    service,
    max_rows: int = DEFAULT_MAX_ROWS_PER_CHUNK,
    source_name: str,
    source_url: str | None = None,
    created_by: str | None = None,
    materialize_monthly: bool = False,
    prune_seed_rows: bool = True,
    dry_run: bool = False,
) -> FXHistoryImportSummary:
    """Import SDS-shaped FX rows through grouped, chunked, idempotent batches."""

    chunks = plan_fx_import_chunks(
        list(rows),
        max_rows=max_rows,
        source_name=source_name,
        source_url=source_url,
    )
    group_keys = {
        (chunk.provider, chunk.rate_type, chunk.base_currency) for chunk in chunks
    }
    summary = FXHistoryImportSummary(
        total_rows=sum(len(chunk.rows) for chunk in chunks),
        group_count=len(group_keys),
        chunk_count=len(chunks),
        dry_run=dry_run,
        chunks=[_chunk_summary(chunk) for chunk in chunks],
    )
    if dry_run:
        return summary

    for chunk in chunks:
        result = service.import_rates_csv_result(
            chunk.rows,
            provider=chunk.provider,
            rate_type=chunk.rate_type,
            base_currency=chunk.base_currency,
            source_hash=chunk.source_hash,
            created_by=created_by,
            source_name=source_name,
            source_url=source_url,
            batch_metadata={
                "source": "historical_fx_loader",
                "source_name": source_name,
                "source_url": source_url,
                "chunk_index": chunk.chunk_index,
                "chunk_total": chunk.chunk_total,
                "rows": len(chunk.rows),
            },
        )
        summary.created_rows += int(getattr(result, "created_rows", 0))
        summary.updated_rows += int(getattr(result, "updated_rows", 0))
        summary.unchanged_rows += int(getattr(result, "unchanged_rows", 0))
        if getattr(result, "batch", None) is not None:
            summary.batches_created += 1

    if prune_seed_rows:
        for provider, rate_type, allowed_keys in _allowed_keys_by_source(chunks):
            result = service.repository.delete_seed_observations_not_in_keys(
                provider=provider,
                rate_type=rate_type,
                allowed_keys=allowed_keys,
                seed_name=source_name,
            )
            summary.seed_rows_pruned += int(getattr(result, "deleted_rows", 0))

    if materialize_monthly:
        for window in _monthly_windows(chunks):
            result = service.repository.materialize_monthly_periods(
                provider=window["provider"],
                rate_type=window["rate_type"],
                base_currency=window["base_currency"],
                quote_currency=window["quote_currency"],
                start=window["start"],
                end=window["end"],
            )
            summary.monthly_periods_created += int(
                getattr(result, "created_periods", 0)
            )
            summary.monthly_periods_updated += int(
                getattr(result, "updated_periods", 0)
            )
            summary.monthly_periods_unchanged += int(
                getattr(result, "unchanged_periods", 0)
            )
    return summary


def _normalize_rate_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rate_date": _parse_date(row["rate_date"]),
        "base_currency": str(row["base_currency"]).strip().upper(),
        "quote_currency": str(row["quote_currency"]).strip().upper(),
        "rate_value": Decimal(str(row["rate_value"])),
        "provider": str(row.get("provider") or "ECB").strip(),
        "rate_type": str(row.get("rate_type") or "reference").strip(),
        "observed_at": row.get("observed_at"),
        "rate_metadata": dict(row.get("rate_metadata") or {}),
    }


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _source_hash_for_rows(
    rows: list[dict[str, Any]],
    *,
    provider: str,
    rate_type: str,
    base_currency: str,
    source_name: str,
    source_url: str | None,
) -> str:
    payload = {
        "provider": provider,
        "rate_type": rate_type,
        "base_currency": base_currency,
        "source_name": source_name,
        "source_url": source_url,
        "rows": [_canonical_hash_row(row) for row in rows],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_hash_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rate_date": row["rate_date"].isoformat(),
        "base_currency": row["base_currency"],
        "quote_currency": row["quote_currency"],
        "rate_value": str(row["rate_value"]),
        "provider": row["provider"],
        "rate_type": row["rate_type"],
        "rate_metadata": row.get("rate_metadata") or {},
    }


def _chunk_summary(chunk: FXImportChunk) -> dict[str, Any]:
    return {
        "provider": chunk.provider,
        "rate_type": chunk.rate_type,
        "base_currency": chunk.base_currency,
        "chunk_index": chunk.chunk_index,
        "chunk_total": chunk.chunk_total,
        "rows": len(chunk.rows),
        "source_hash": chunk.source_hash,
    }


def _monthly_windows(chunks: list[FXImportChunk]) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for chunk in chunks:
        for row in chunk.rows:
            rows_by_key[
                (
                    chunk.provider,
                    chunk.rate_type,
                    chunk.base_currency,
                    row["quote_currency"],
                )
            ].append(row)

    windows: list[dict[str, Any]] = []
    for (provider, rate_type, base_currency, quote_currency), rows in sorted(
        rows_by_key.items()
    ):
        rate_dates = sorted(row["rate_date"] for row in rows)
        windows.append(
            {
                "provider": provider,
                "rate_type": rate_type,
                "base_currency": base_currency,
                "quote_currency": quote_currency,
                "start": rate_dates[0],
                "end": rate_dates[-1],
            }
        )
    return windows


def _allowed_keys_by_source(
    chunks: list[FXImportChunk],
) -> list[tuple[str, str, set[tuple[str, str, str, str, date]]]]:
    grouped: dict[tuple[str, str], set[tuple[str, str, str, str, date]]] = defaultdict(
        set
    )
    for chunk in chunks:
        for row in chunk.rows:
            grouped[(chunk.provider, chunk.rate_type)].add(
                (
                    chunk.provider,
                    chunk.rate_type,
                    chunk.base_currency,
                    row["quote_currency"],
                    row["rate_date"],
                )
            )
    return [
        (provider, rate_type, allowed_keys)
        for (provider, rate_type), allowed_keys in sorted(grouped.items())
    ]
