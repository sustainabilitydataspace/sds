#!/usr/bin/env python3
"""Prepare a standardized SDS register from framework and granulation CSV inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from io import StringIO
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
API_DIR = SCRIPT_DIR.parent / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from indicator_identifiers import build_indicator_identifier
from esg_classification import classify_esg_dimension
from src.services.indicator_metadata_overrides import apply_indicator_metadata_overrides

DEFAULT_FRAMEWORK_PATH = Path("data/sds_package/framework_datapoints.csv")
DEFAULT_GRANULATED_PATH = Path("data/sds_package/granulated_variables.csv")
DEFAULT_OUTPUT_PATH = Path("data/processed/e1_dataset_register.csv")
LOG_PATH = Path("data/processed/prepare_log.txt")


def write_console_line(message: str) -> None:
    """Write a console line without failing on narrow Windows encodings."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        stream.write(safe_message + "\n")
        stream.flush()


def log_message(message: str, verbose: bool = True) -> None:
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}\n"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line)
    if verbose:
        write_console_line(message)


def read_csv_with_encoding(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            content = path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        if content.strip():
            rows = list(csv.DictReader(StringIO(content)))
            log_message(f"Read {path} with encoding {encoding} ({len(rows)} rows)")
            return rows
    raise ValueError(f"Could not read {path} with a supported encoding")


def validate_no_duplicates(rows: list[dict[str, str]], key_field: str = "identifier") -> bool:
    identifiers = [row.get(key_field, "").strip() for row in rows if row.get(key_field)]
    duplicates = {
        key: count for key, count in Counter(identifiers).items() if count > 1
    }
    if not duplicates:
        return True

    log_message(f"Warning: duplicate values found in {key_field}", True)
    for duplicate, count in list(duplicates.items())[:5]:
        log_message(f"   - {duplicate}: {count} rows", True)
    if len(duplicates) > 5:
        log_message(f"   ... and {len(duplicates) - 5} more", True)
    return False


def calculate_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return ""


def prepare_from_granulation(
    framework_path: Path = DEFAULT_FRAMEWORK_PATH,
    granulated_path: Path = DEFAULT_GRANULATED_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    verbose: bool = True,
) -> int:
    """Convert framework and granulation CSV inputs into the SDS register format."""
    log_message("=== Starting standards package to SDS register transformation ===", verbose)

    if not framework_path.exists():
        log_message(f"Error: missing framework source {framework_path}", True)
        log_message(
            "Use --framework/--granulated with paths to a controlled standards package, "
            "or place those files in the local git-ignored data/sds_package cache.",
            True,
        )
        sys.exit(1)

    framework_checksum = calculate_checksum(framework_path)
    log_message(f"Framework checksum: {framework_checksum}", verbose)

    try:
        framework_rows = read_csv_with_encoding(framework_path)
    except Exception as exc:
        log_message(f"Error reading framework source: {exc}", True)
        sys.exit(1)

    frameworks: dict[str, dict[str, str]] = {}
    for row in framework_rows:
        code = _row_value(row, "DatapointCode", "code", "identifier")
        if not code:
            continue
        frameworks[code] = {
            "framework": _row_value(row, "FrameworkID", "framework", "standard"),
            "label": _row_value(row, "Label", "title", "indicator"),
            "description": _row_value(row, "Description", "description"),
            "datatype": _row_value(row, "DataType", "valueType"),
            "unit": _row_value(row, "DefaultUnit", "unitName"),
        }

    log_message(f"Loaded {len(frameworks)} framework rows", verbose)

    granulated: dict[str, list[dict[str, str]]] = {}
    if granulated_path.exists():
        granulation_checksum = calculate_checksum(granulated_path)
        log_message(f"Granulation checksum: {granulation_checksum}", verbose)
        try:
            granulation_rows = read_csv_with_encoding(granulated_path)
            for row in granulation_rows:
                code = _row_value(row, "DatapointCode", "code", "identifier")
                unit = _row_value(row, "UnitName", "unitName", "unit")
                unit_type = _row_value(row, "UnitType", "unitType")
                if code:
                    granulated.setdefault(code, []).append(
                        {"unit": unit, "unit_type": unit_type}
                    )
            log_message(f"Loaded {len(granulated)} granulated row groups", verbose)
        except Exception as exc:
            log_message(f"Warning reading granulation source: {exc}", verbose)

    fieldnames = [
        "identifier",
        "title",
        "indicator",
        "description",
        "dimension",
        "unitName",
        "unitType",
        "periodicity",
        "periodType",
        "sourceRef",
        "codeESRS",
        "codeGRI",
        "codeGRI_expanded",
        "evidencePath",
        "sourceRow",
        "owner",
        "accessRights",
        "validationMethod",
        "doubleMateriality",
        "valueType",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_rows: list[dict[str, str]] = []
    unclassified_codes: list[str] = []
    for index, (code, framework) in enumerate(frameworks.items(), start=1):
        # Deterministic ESG classification by framework + standard code/series,
        # NOT the free-text Topic field. GRI Topic carries bare codes ("GRI 403")
        # rather than themes, so the old keyword heuristic with an 'E' default
        # mislabelled all 680 GRI rows and emitted zero 'G'. See
        # scripts/esg_classification.py and the consensus run
        # workspace/consensus/20260612-e1-dimension-fault-attribution.
        framework_id = framework.get("framework", "")
        dimension = classify_esg_dimension(framework_id, code)
        if dimension is None:
            unclassified_codes.append(f"{framework_id}:{code}")
            dimension = ""

        framework_upper = framework_id.upper()
        unit_name = framework.get("unit", "")
        unit_type = ""
        if granulated.get(code):
            first_granulated = granulated[code][0]
            unit_name = unit_name or first_granulated.get("unit", "")
            unit_type = first_granulated.get("unit_type", "")

        output_row = {
            "identifier": build_indicator_identifier(
                framework_upper, code, fallback=f"item_{index}"
            ),
            "title": framework.get("label") or f"{framework_id} {code}".strip(),
            "indicator": framework.get("label", ""),
            "description": framework.get("description", ""),
            "dimension": dimension,
            "unitName": unit_name,
            "unitType": unit_type,
            "periodicity": "annual",
            "periodType": "fiscal_year",
            "sourceRef": f"{framework_id} Official" if framework_id else "Unknown",
            "codeESRS": code if framework_upper == "ESRS" else "",
            "codeGRI": code if framework_upper == "GRI" else "",
            "codeGRI_expanded": "",
            "evidencePath": "",
            "sourceRow": "",
            "owner": "system",
            "accessRights": "Internal",
            "validationMethod": "automated",
            "doubleMateriality": "",
            "valueType": framework.get("datatype", "").lower() or "numeric",
        }
        apply_indicator_metadata_overrides(output_row)
        output_rows.append(output_row)

    if unclassified_codes:
        log_message(
            "Error: %d code(s) without a classifiable ESG dimension: %s"
            % (len(unclassified_codes), ", ".join(unclassified_codes[:10])),
            True,
        )
        sys.exit(1)

    validate_no_duplicates(output_rows, "identifier")

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    meta_path = output_path.with_suffix(".meta.json")
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "version": "2.0",
        "source_frameworks_checksum": framework_checksum,
        "count": len(output_rows),
        "dimensions": dict(Counter(row["dimension"] for row in output_rows)),
        "frameworks": dict(Counter(row["sourceRef"] for row in output_rows)),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    log_message(f"Generated {len(output_rows)} records in {output_path}", True)
    log_message(f"Dimensions: {metadata['dimensions']}", verbose)
    log_message(f"Metadata written to {meta_path}", verbose)

    return len(output_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prepare framework and granulation CSV inputs as an SDS register"
    )
    parser.add_argument("--framework", type=Path, default=DEFAULT_FRAMEWORK_PATH)
    parser.add_argument("--granulated", type=Path, default=DEFAULT_GRANULATED_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        count = prepare_from_granulation(
            framework_path=args.framework,
            granulated_path=args.granulated,
            output_path=args.output,
            verbose=not args.quiet,
        )
        raise SystemExit(0 if count >= 0 else 1)
    except Exception as exc:
        log_message(f"Fatal error: {exc}", True)
        raise SystemExit(1)
