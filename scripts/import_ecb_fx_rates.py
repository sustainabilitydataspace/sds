"""Convert ECB historical euro reference rates into SDS FX rate rows."""

from __future__ import annotations

import argparse
import csv
import io
import os
import stat
import urllib.request
import zipfile
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
RATE_SCALE = Decimal("0.000001")
DEFAULT_OUTPUT = PROJECT_ROOT / ".local_artifacts" / "fx" / "ecb-reference-history-sds.csv"
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100


def parse_ecb_hist_csv(
    csv_text: str,
    *,
    quote_currency: str = "EUR",
    provider: str = "ECB",
    rate_type: str = "reference",
) -> list[dict[str, Any]]:
    """Parse ECB `eurofxref-hist.csv` rows into base-currency to EUR rates.

    ECB publishes rates as one EUR equals N foreign currency units. SDS stores
    normal conversion rows as `base_currency -> quote_currency`; therefore USD
    rows become USD -> EUR by inverting the ECB value.
    """

    reader = csv.DictReader(io.StringIO(csv_text))
    date_column = _date_column(reader.fieldnames or [])
    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        rate_date = date.fromisoformat(str(raw_row[date_column]).strip())
        for code, raw_value in raw_row.items():
            if code == date_column:
                continue
            value = str(raw_value or "").strip()
            if not value or value.upper() in {"N/A", "NA", "NAN"}:
                continue
            rate_value = (Decimal("1") / Decimal(value)).quantize(
                RATE_SCALE,
                rounding=ROUND_HALF_UP,
            )
            rows.append(
                {
                    "rate_date": rate_date,
                    "base_currency": code.strip().upper(),
                    "quote_currency": quote_currency.upper(),
                    "rate_value": rate_value,
                    "provider": provider,
                    "rate_type": rate_type,
                    "rate_metadata": {
                        "source": "ECB euro foreign exchange reference rates",
                        "source_url": ECB_HIST_URL,
                        "source_quote": f"1 {quote_currency.upper()} = {value} {code.strip().upper()}",
                    },
                }
            )
    return rows


def write_sds_fx_csv(rows: list[dict[str, Any]], output_path: Path = DEFAULT_OUTPUT) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rate_date",
                "base_currency",
                "quote_currency",
                "rate_value",
                "provider",
                "rate_type",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "rate_date": row["rate_date"].isoformat(),
                    "base_currency": row["base_currency"],
                    "quote_currency": row["quote_currency"],
                    "rate_value": str(row["rate_value"]),
                    "provider": row["provider"],
                    "rate_type": row["rate_type"],
                }
            )


def load_ecb_hist_csv_from_zip(zip_bytes: bytes) -> str:
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ValueError("ECB history zip exceeds input byte budget")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        csv_members = [item for item in members if item.filename.endswith(".csv")]
        if len(members) != 1 or len(csv_members) != 1:
            raise ValueError("ECB history zip must contain exactly one CSV file")
        member = csv_members[0]
        if (
            member.filename.startswith("/")
            or "\\" in member.filename
            or any(part in {"", ".", ".."} for part in member.filename.split("/"))
            or member.flag_bits & 0x1
        ):
            raise ValueError("ECB history zip contains an unsafe CSV member")
        if member.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("ECB history zip exceeds expanded byte budget")
        if member.file_size and (
            member.compress_size == 0
            or member.file_size / member.compress_size > MAX_ZIP_COMPRESSION_RATIO
        ):
            raise ValueError("ECB history zip exceeds compression ratio budget")
        expanded = bytearray()
        with archive.open(member) as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, MAX_ZIP_MEMBER_BYTES + 1))
                if not chunk:
                    break
                expanded.extend(chunk)
                if len(expanded) > MAX_ZIP_MEMBER_BYTES:
                    raise ValueError("ECB history zip exceeds expanded byte budget")
        if len(expanded) != member.file_size:
            raise ValueError("ECB history zip CSV size mismatch")
        return bytes(expanded).decode("utf-8-sig")


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or initial.st_size > max_bytes
        ):
            raise ValueError("ECB input must be an independent bounded regular file")
        content = bytearray()
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError("ECB input exceeds byte budget")
        final = os.fstat(fd)
        if (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
            initial.st_ctime_ns,
            initial.st_nlink,
        ) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
            final.st_nlink,
        ):
            raise ValueError("ECB input changed while reading")
        return bytes(content)
    finally:
        os.close(fd)


def download_ecb_history() -> str:
    if urlsplit(ECB_HIST_URL).scheme != "https":
        raise ValueError("ECB history downloads require HTTPS")
    # ECB_HIST_URL is a fixed HTTPS endpoint and is checked immediately above.
    with urllib.request.urlopen(ECB_HIST_URL, timeout=120) as response:  # nosec B310
        content = response.read(MAX_ZIP_BYTES + 1)
        if len(content) > MAX_ZIP_BYTES:
            raise ValueError("ECB history download exceeds input byte budget")
        return load_ecb_hist_csv_from_zip(content)


def _date_column(fieldnames: list[str]) -> str:
    for candidate in ("Date", "DATE", "TIME_PERIOD"):
        if candidate in fieldnames:
            return candidate
    raise ValueError(f"ECB CSV date column not found in {fieldnames!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path)
    parser.add_argument("--input-zip", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.input_csv is not None:
        csv_text = _read_regular_file(
            args.input_csv, max_bytes=MAX_ZIP_MEMBER_BYTES
        ).decode("utf-8-sig")
    elif args.input_zip is not None:
        csv_text = load_ecb_hist_csv_from_zip(
            _read_regular_file(args.input_zip, max_bytes=MAX_ZIP_BYTES)
        )
    else:
        csv_text = download_ecb_history()

    write_sds_fx_csv(parse_ecb_hist_csv(csv_text), args.output)


if __name__ == "__main__":
    main()
