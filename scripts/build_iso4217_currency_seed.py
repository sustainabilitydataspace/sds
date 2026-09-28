"""Build the SDS ISO 4217 currency seed from official SIX XML lists."""

from __future__ import annotations

import argparse
import calendar
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "api" / "src" / "data" / "currencies_iso4217_seed.json"
LIST_ONE_URL = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml"
LIST_THREE_URL = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-three.xml"

CURATED_VALID_FROM = {
    "EUR": "1999-01-01",
}


def build_currency_seed(list_one_path: Path, list_three_path: Path) -> list[dict[str, Any]]:
    current_rows = _parse_current_list(list_one_path)
    historic_rows = _parse_historic_list(list_three_path)
    current_codes = {row["code"] for row in current_rows}
    rows = current_rows + [row for row in historic_rows if row["code"] not in current_codes]
    return sorted(rows, key=lambda row: (row["code"], 0 if row["is_active"] else 1))


def write_currency_seed(rows: list[dict[str, Any]], output_path: Path = DEFAULT_OUTPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_current_list(path: Path) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    published = root.attrib.get("Pblshd")
    grouped: dict[str, list[Element]] = defaultdict(list)
    for entry in root.findall(".//CcyNtry"):
        code = _text(entry, "Ccy")
        if code:
            grouped[code].append(entry)

    rows: list[dict[str, Any]] = []
    for code, entries in grouped.items():
        first = entries[0]
        minor_units, minor_units_raw = _parse_minor_units(_text(first, "CcyMnrUnts"))
        rows.append(
            {
                "code": code,
                "numeric_code": _text(first, "CcyNbr"),
                "name": _text(first, "CcyNm") or code,
                "minor_units": minor_units,
                "valid_from": CURATED_VALID_FROM.get(code),
                "valid_to": None,
                "redenomination_of": None,
                "redenomination_factor": None,
                "currency_metadata": {
                    "source": "SIX ISO 4217 list-one",
                    "source_url": LIST_ONE_URL,
                    "published": published,
                    "entities": _unique_text(entries, "CtryNm"),
                    "currency_names": _unique_text(entries, "CcyNm"),
                    "minor_units_raw": minor_units_raw,
                },
                "is_active": True,
            }
        )
    return rows


def _parse_historic_list(path: Path) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    published = root.attrib.get("Pblshd")
    grouped: dict[str, list[Element]] = defaultdict(list)
    for entry in root.findall(".//HstrcCcyNtry"):
        code = _text(entry, "Ccy")
        if code:
            grouped[code].append(entry)

    rows: list[dict[str, Any]] = []
    for code, entries in grouped.items():
        first = entries[0]
        minor_units, minor_units_raw = _parse_minor_units(_text(first, "CcyMnrUnts"))
        raw_withdrawal_dates = [value for value in _unique_text(entries, "WthdrwlDt") if value]
        withdrawal_dates = [_normalise_withdrawal_date(value) for value in raw_withdrawal_dates]
        rows.append(
            {
                "code": code,
                "numeric_code": _text(first, "CcyNbr"),
                "name": _text(first, "CcyNm") or code,
                "minor_units": minor_units,
                "valid_from": None,
                "valid_to": max(withdrawal_dates) if withdrawal_dates else None,
                "redenomination_of": None,
                "redenomination_factor": None,
                "currency_metadata": {
                    "source": "SIX ISO 4217 list-three",
                    "source_url": LIST_THREE_URL,
                    "published": published,
                    "entities": _unique_text(entries, "CtryNm"),
                    "currency_names": _unique_text(entries, "CcyNm"),
                    "minor_units_raw": minor_units_raw,
                    "withdrawal_dates": raw_withdrawal_dates,
                    "valid_to_precision": _valid_to_precision(raw_withdrawal_dates),
                },
                "is_active": False,
            }
        )
    return rows


def _text(entry: Element, tag: str) -> str | None:
    node = entry.find(tag)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _unique_text(entries: list[Element], tag: str) -> list[str]:
    values = [value for entry in entries if (value := _text(entry, tag))]
    return sorted(dict.fromkeys(values))


def _parse_minor_units(raw: str | None) -> tuple[int, str | None]:
    if raw is None:
        return 0, None
    value = raw.strip()
    if value.isdecimal():
        return int(value), value
    return 0, value


def _normalise_withdrawal_date(value: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = value.split("-")
        day = calendar.monthrange(int(year), int(month))[1]
        return f"{year}-{month}-{day:02d}"
    if re.fullmatch(r"\d{4}", value):
        return f"{value}-12-31"
    month_range = re.fullmatch(r"\d{4}-\d{2}\s+to\s+(\d{4})-(\d{2})", value)
    if month_range:
        year, month = month_range.groups()
        day = calendar.monthrange(int(year), int(month))[1]
        return f"{year}-{month}-{day:02d}"
    year_range = re.fullmatch(r"\d{4}\s+to\s+(\d{4})", value)
    if year_range:
        return f"{year_range.group(1)}-12-31"
    compact_year_range = re.fullmatch(r"\d{4}-(\d{4})", value)
    if compact_year_range:
        return f"{compact_year_range.group(1)}-12-31"
    return value


def _valid_to_precision(values: list[str]) -> str | None:
    if not values:
        return None
    if all(re.fullmatch(r"\d{4}-\d{2}", value) for value in values):
        return "month"
    if all(re.fullmatch(r"\d{4}", value) for value in values):
        return "year"
    if all("to" in value or re.fullmatch(r"\d{4}-\d{4}", value) for value in values):
        return "range"
    return "day"


def _download(url: str, destination: Path) -> None:
    if urlsplit(url).scheme != "https":
        raise ValueError("ISO 4217 downloads require HTTPS")
    # The dynamic argument is constrained to HTTPS immediately above.
    with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
        destination.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-one", type=Path)
    parser.add_argument("--list-three", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        list_one = args.list_one or temp_path / "list-one.xml"
        list_three = args.list_three or temp_path / "list-three.xml"
        if args.list_one is None:
            _download(LIST_ONE_URL, list_one)
        if args.list_three is None:
            _download(LIST_THREE_URL, list_three)
        write_currency_seed(build_currency_seed(list_one, list_three), args.output)


if __name__ == "__main__":
    main()
