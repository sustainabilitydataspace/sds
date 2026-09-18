#!/usr/bin/env python3
"""R8 gate: execute and evidence raw-value transformations row by row."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter

from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from gate_value_import_performance import (  # noqa: E402
    GATE_SOURCE,
    _count_gate_rows,
    _engine,
    _purge_gate_rows,
    get_default_database_url,
)
from import_values_csv import import_values_csv  # noqa: E402
from value_import_db_probe import (  # noqa: E402
    DATABASE_UNAVAILABLE_EXIT_CODE,
    DatabaseUnavailable,
)
from value_import_gate_hierarchy import (  # noqa: E402
    VALUE_IMPORT_GATE_ENTITY,
    ensure_value_import_gate_hierarchy,
)

from src.database.models import ESGValue  # noqa: E402

DEFAULT_ROW_COUNT = 1000
DEFAULT_MIN_SUCCESS_RATE = 0.95
DEFAULT_BATCH_SIZE = 250
DEFAULT_MAX_SECONDS = 30.0
DEFAULT_MATRIX = (
    REPO_ROOT / "artifacts" / "e4_raw_transform_matrix.csv"
)
DEFAULT_REPORT = (
    REPO_ROOT / "artifacts" / "e4_raw_transform_report.json"
)
R8_SOURCE = "e4_raw_transform_gate"
TRANSFORM_RULES = (
    {
        "rule_id": "water_l_to_m3",
        "domain": "water",
        "concept": "urn:sds:sample:water_volume",
        "raw_unit": "L",
        "expected_unit": "m3",
    },
    {
        "rule_id": "energy_kwh_to_mwh",
        "domain": "energy",
        "concept": "urn:sds:sample:energy_use",
        "raw_unit": "kWh",
        "expected_unit": "MWh",
    },
    {
        "rule_id": "emissions_kgco2e_to_tco2e",
        "domain": "emissions",
        "concept": "urn:sds:sample:emissions_mass",
        "raw_unit": "kgCO2e",
        "expected_unit": "tCO2e",
    },
)
MATRIX_FIELDS = (
    "row_id",
    "rule_id",
    "domain",
    "external_key",
    "concept",
    "raw_value",
    "raw_unit",
    "expected_value",
    "expected_unit",
    "observed_value",
    "observed_unit",
    "observed_original_value",
    "observed_original_unit",
    "conversion_applied",
    "status",
    "error",
)


def _decimal_text(value) -> str:
    if value is None:
        return ""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if decimal_value == 0:
        return "0"
    return format(decimal_value.normalize(), "f")


def build_fixture_files(
    *,
    input_path: Path,
    expected_path: Path,
    row_count: int,
    external_key_prefix: str,
) -> None:
    input_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    start_date = date(2028, 1, 1)
    input_fields = (
        "concept",
        "entity",
        "period",
        "external_key",
        "value",
        "unit",
        "expected_unit",
        "value_type",
        "metadata_json",
    )
    expected_fields = (
        "row_id",
        "rule_id",
        "domain",
        "external_key",
        "concept",
        "raw_value",
        "raw_unit",
        "expected_value",
        "expected_unit",
    )
    with input_path.open("w", encoding="utf-8", newline="") as input_handle, expected_path.open(
        "w", encoding="utf-8", newline=""
    ) as expected_handle:
        input_writer = csv.DictWriter(input_handle, fieldnames=input_fields)
        expected_writer = csv.DictWriter(expected_handle, fieldnames=expected_fields)
        input_writer.writeheader()
        expected_writer.writeheader()
        for index in range(row_count):
            rule = TRANSFORM_RULES[index % len(TRANSFORM_RULES)]
            ordinal = index + 1
            raw_value = Decimal(ordinal * 1000)
            expected_value = Decimal(ordinal)
            period = start_date + timedelta(days=index)
            external_key = f"{external_key_prefix}:{ordinal:06d}"
            input_writer.writerow(
                {
                    "concept": rule["concept"],
                    "entity": VALUE_IMPORT_GATE_ENTITY,
                    "period": period.isoformat(),
                    "external_key": external_key,
                    "value": _decimal_text(raw_value),
                    "unit": rule["raw_unit"],
                    "expected_unit": rule["expected_unit"],
                    "value_type": "numeric",
                    "metadata_json": json.dumps(
                        {
                            "source": R8_SOURCE,
                            "rule_id": rule["rule_id"],
                            "reporting_period_id": period.isoformat(),
                            "period_type": "point",
                            "reporting_boundary_id": "operational_control",
                        },
                        separators=(",", ":"),
                    ),
                }
            )
            expected_writer.writerow(
                {
                    "row_id": f"R8-{ordinal:06d}",
                    "rule_id": rule["rule_id"],
                    "domain": rule["domain"],
                    "external_key": external_key,
                    "concept": rule["concept"],
                    "raw_value": _decimal_text(raw_value),
                    "raw_unit": rule["raw_unit"],
                    "expected_value": _decimal_text(expected_value),
                    "expected_unit": rule["expected_unit"],
                }
            )


def evaluate_transformations(
    expected_rows: list[dict[str, str]], actual_by_key: dict[str, dict]
) -> tuple[list[dict[str, str]], dict[str, int | float]]:
    matrix: list[dict[str, str]] = []
    success = 0
    for expected in expected_rows:
        actual = actual_by_key.get(expected["external_key"])
        errors: list[str] = []
        if actual is None:
            errors.append("persisted row missing")
            observed = {}
        else:
            observed = actual
            if _decimal_text(actual.get("value")) != _decimal_text(
                expected["expected_value"]
            ):
                errors.append(
                    f"expected value {expected['expected_value']}, observed "
                    f"{_decimal_text(actual.get('value'))}"
                )
            if str(actual.get("unit") or "") != expected["expected_unit"]:
                errors.append(
                    f"expected unit {expected['expected_unit']}, observed "
                    f"{actual.get('unit') or ''}"
                )
            if _decimal_text(actual.get("original_value")) != _decimal_text(
                expected["raw_value"]
            ):
                errors.append("original value was not preserved")
            if str(actual.get("original_unit") or "") != expected["raw_unit"]:
                errors.append("original unit was not preserved")
            if actual.get("conversion_applied") is not True:
                errors.append("conversion_applied is not true")
        status = "success" if not errors else "error"
        if status == "success":
            success += 1
        matrix.append(
            {
                **expected,
                "observed_value": _decimal_text(observed.get("value")),
                "observed_unit": str(observed.get("unit") or ""),
                "observed_original_value": _decimal_text(
                    observed.get("original_value")
                ),
                "observed_original_unit": str(observed.get("original_unit") or ""),
                "conversion_applied": str(
                    bool(observed.get("conversion_applied"))
                ).lower(),
                "status": status,
                "error": "; ".join(errors),
            }
        )
    total = len(expected_rows)
    errors = total - success
    return matrix, {
        "total": total,
        "success": success,
        "error": errors,
        "success_rate": success / total if total else 0.0,
    }


def _load_expected(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_actual_rows(db, external_key_prefix: str) -> dict[str, dict]:
    rows = (
        db.query(ESGValue)
        .filter(ESGValue.external_key.like(f"{external_key_prefix}:%"))
        .all()
    )
    return {
        row.external_key: {
            "value": row.value,
            "unit": row.unit,
            "original_value": row.original_value,
            "original_unit": row.original_unit,
            "conversion_applied": row.conversion_applied,
        }
        for row in rows
    }


def _write_matrix(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def public_matrix_path(path: Path) -> str:
    """Return a stable repository path even when the file is container-mounted."""
    if path.name == DEFAULT_MATRIX.name:
        return "artifacts/e4_raw_transform_matrix.csv"
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def run_gate(
    *,
    db_url: str,
    row_count: int,
    min_success_rate: float,
    max_seconds: float,
    batch_size: int,
    matrix_path: Path,
    report_path: Path,
) -> tuple[dict, list[str]]:
    engine = _engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    external_key_prefix = "gate-r8-transform-" + datetime.now(timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sds_r8_transform_gate_") as temp_dir:
        input_path = Path(temp_dir) / "raw-values.csv"
        expected_path = Path(temp_dir) / "expected.csv"
        build_fixture_files(
            input_path=input_path,
            expected_path=expected_path,
            row_count=row_count,
            external_key_prefix=external_key_prefix,
        )
        expected_rows = _load_expected(expected_path)
        db = SessionLocal()
        try:
            ensure_value_import_gate_hierarchy(db, created_by=R8_SOURCE)
            _purge_gate_rows(db, external_key_prefix)
        finally:
            db.close()

        start = perf_counter()
        imported = import_values_csv(
            csv_path=input_path,
            db_url=db_url,
            default_entity=VALUE_IMPORT_GATE_ENTITY,
            created_by=R8_SOURCE,
            batch_size=batch_size,
            tenant_id="sds_public_demo",
        )

        db = SessionLocal()
        try:
            actual_by_key = _load_actual_rows(db, external_key_prefix)
            counts = _count_gate_rows(db, external_key_prefix)
            matrix, transform_summary = evaluate_transformations(
                expected_rows, actual_by_key
            )
            deleted = _purge_gate_rows(db, external_key_prefix)
            residue = _count_gate_rows(db, external_key_prefix)
        finally:
            db.close()
        elapsed = perf_counter() - start

    _write_matrix(matrix_path, matrix)
    if imported != row_count:
        failures.append(f"imported {imported} rows, expected {row_count}")
    if transform_summary["success_rate"] < min_success_rate:
        failures.append(
            f"success rate {transform_summary['success_rate']:.6f} below "
            f"threshold {min_success_rate:.6f}"
        )
    if elapsed >= max_seconds:
        failures.append(
            f"elapsed {elapsed:.3f}s does not satisfy strict threshold < {max_seconds:.3f}s"
        )
    for table_name, count in counts.items():
        if count != row_count:
            failures.append(f"{table_name} count {count}, expected {row_count}")
    for table_name, count in residue.items():
        if count != 0:
            failures.append(f"residue after purge: {table_name} count {count}, expected 0")
    report = {
        "gate": "e4_raw_value_transform_matrix",
        "row_count": row_count,
        "imported": imported,
        "min_success_rate": min_success_rate,
        "max_seconds": max_seconds,
        "elapsed_seconds": elapsed,
        "transform_summary": transform_summary,
        "rules": dict(Counter(row["rule_id"] for row in matrix)),
        "counts": counts,
        "deleted": deleted,
        "residue_after_purge": residue,
        "matrix_path": public_matrix_path(matrix_path),
        "passed": not failures,
        "failures": failures,
    }
    _write_report(report_path, report)
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute and evidence the E4/R8 raw-value transformation matrix."
    )
    parser.add_argument("--db-url", default=get_default_database_url())
    parser.add_argument("--rows", type=int, default=DEFAULT_ROW_COUNT)
    parser.add_argument(
        "--min-success-rate", type=float, default=DEFAULT_MIN_SUCCESS_RATE
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report, failures = run_gate(
            db_url=args.db_url,
            row_count=args.rows,
            min_success_rate=args.min_success_rate,
            max_seconds=args.max_seconds,
            batch_size=args.batch_size,
            matrix_path=args.matrix,
            report_path=args.report,
        )
    except DatabaseUnavailable as exc:
        report = {
            "gate": "e4_raw_value_transform_matrix",
            "row_count": args.rows,
            "passed": False,
            "status": "not_evaluated",
            "failure_kind": "database_unavailable",
            "failures": [str(exc)],
        }
        _write_report(args.report, report)
        print("E4 R8 raw transformation gate: NOT_EVALUATED")
        return DATABASE_UNAVAILABLE_EXIT_CODE
    summary = report["transform_summary"]
    print("E4 R8 raw transformation gate")
    print(f"  rows: {summary['total']}")
    print(f"  success: {summary['success']}")
    print(f"  error: {summary['error']}")
    print(f"  success_rate: {summary['success_rate']:.2%}")
    print(f"  elapsed_seconds: {report['elapsed_seconds']:.3f}")
    print(f"  matrix: {report['matrix_path']}")
    print("PASS" if not failures else "FAIL")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
