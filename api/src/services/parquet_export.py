"""Helpers for Parquet export surfaces."""

from __future__ import annotations

import io
import json
from typing import Any, Iterable

import pandas as pd


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def parquet_bytes_from_rows(rows: Iterable[dict[str, Any]]) -> bytes:
    """Serialize tabular rows to Parquet bytes."""
    normalized_rows = [
        {key: _normalize_scalar(value) for key, value in row.items()} for row in rows
    ]
    dataframe = pd.DataFrame(normalized_rows)
    buffer = io.BytesIO()
    dataframe.to_parquet(buffer, index=False, engine="pyarrow")
    return buffer.getvalue()
