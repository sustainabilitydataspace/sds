#!/usr/bin/env python3
"""Load ECB historical reference rates into the SDS FX tables."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "api"
DEFAULT_START_DATE = date(2020, 1, 1)
DEFAULT_SOURCE_NAME = "ECB euro foreign exchange reference rates history"
DEFAULT_SOURCE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


def _ensure_api_path() -> None:
    api_root_text = str(API_ROOT)
    if api_root_text not in sys.path:
        sys.path.insert(0, api_root_text)


def _load_ecb_importer():
    module_path = PROJECT_ROOT / "scripts" / "import_ecb_fx_rates.py"
    spec = importlib.util.spec_from_file_location("import_ecb_fx_rates", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load ECB importer from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_rows(args) -> list[dict]:
    importer = _load_ecb_importer()
    if args.input_csv is not None:
        csv_text = args.input_csv.read_text(encoding="utf-8-sig")
        if _is_sds_fx_csv(csv_text):
            return parse_sds_fx_csv(csv_text)
        return importer.parse_ecb_hist_csv(csv_text)
    elif args.input_zip is not None:
        csv_text = importer.load_ecb_hist_csv_from_zip(args.input_zip.read_bytes())
    else:
        csv_text = importer.download_ecb_history()
    return importer.parse_ecb_hist_csv(csv_text)


def filter_fx_rows(
    rows: list[dict],
    *,
    start_date: date,
    end_date: date,
    base_currencies: set[str] | None = None,
) -> list[dict]:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    filtered: list[dict] = []
    for row in rows:
        rate_date = _parse_rate_date(row["rate_date"])
        base_currency = str(row["base_currency"]).strip().upper()
        if rate_date < start_date or rate_date > end_date:
            continue
        if base_currencies is not None and base_currency not in base_currencies:
            continue
        filtered.append({**row, "rate_date": rate_date, "base_currency": base_currency})
    return filtered


def parse_sds_fx_csv(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {
        "rate_date",
        "base_currency",
        "quote_currency",
        "rate_value",
        "provider",
        "rate_type",
    }
    missing = required.difference(reader.fieldnames or [])
    if missing:
        raise ValueError(f"SDS FX CSV missing required columns: {sorted(missing)}")
    rows = []
    for row in reader:
        rows.append(
            {
                "rate_date": str(row["rate_date"]).strip(),
                "base_currency": str(row["base_currency"]).strip().upper(),
                "quote_currency": str(row["quote_currency"]).strip().upper(),
                "rate_value": Decimal(str(row["rate_value"]).strip()),
                "provider": str(row["provider"]).strip(),
                "rate_type": str(row["rate_type"]).strip(),
            }
        )
    return rows


def _is_sds_fx_csv(csv_text: str) -> bool:
    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return False
    fields = {field.strip() for field in header}
    return {"rate_date", "base_currency", "quote_currency", "rate_value"}.issubset(
        fields
    )


def _parse_rate_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _normalize_currency_set(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    currencies = {str(value).strip().upper() for value in values}
    invalid = sorted(
        value for value in currencies if len(value) != 3 or not value.isalpha()
    )
    if invalid:
        raise ValueError(f"Invalid base currency code(s): {invalid}")
    return currencies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path)
    parser.add_argument("--input-zip", type=Path)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--base-currency",
        action="append",
        help="Limit the load to one base currency. Repeat for multiple currencies.",
    )
    parser.add_argument("--created-by", default="system")
    parser.add_argument("--source-name", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--materialize-monthly", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to the configured SDS database. Without this flag the script is dry-run only.",
    )
    args = parser.parse_args()

    _ensure_api_path()
    rows = _load_rows(args)
    rows = filter_fx_rows(
        rows,
        start_date=args.start_date,
        end_date=args.end_date,
        base_currencies=_normalize_currency_set(args.base_currency),
    )

    from src.database.repositories.fx_repository import FXRepository
    from src.database.session import SessionLocal
    from src.services.fx_history_import import import_fx_history_rows
    from src.services.fx_service import FXService

    db = SessionLocal()
    try:
        service = FXService(FXRepository(db))
        summary = import_fx_history_rows(
            rows,
            service=service,
            max_rows=args.chunk_size,
            source_name=args.source_name,
            source_url=args.source_url,
            created_by=args.created_by,
            materialize_monthly=args.materialize_monthly,
            dry_run=not args.apply,
        )
    finally:
        db.close()

    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
