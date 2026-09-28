#!/usr/bin/env python3
"""Operational gate for strict value-import throughput and persistence parity."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from import_values_csv import import_values_csv  # noqa: E402
from value_import_db_probe import (  # noqa: E402
    DATABASE_UNAVAILABLE_EXIT_CODE,
    DatabaseUnavailable,
    probe_database,
    require_disposable_value_import_target,
)
from value_import_gate_hierarchy import (  # noqa: E402
    VALUE_IMPORT_GATE_COMPANY_ID,
    VALUE_IMPORT_GATE_CONCEPTS,
    VALUE_IMPORT_GATE_ENTITY,
    ensure_value_import_gate_hierarchy,
)

from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import (  # noqa: E402
    CurrentValuePointer,
    ESGValue,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)

DEFAULT_REPORT = REPO_ROOT / ".local_artifacts" / "value-import-performance-report.json"
DEFAULT_ROW_COUNT = 1000
DEFAULT_MAX_SECONDS = 30.0
DEFAULT_BATCH_SIZE = 250
GATE_SOURCE = "value_import_performance_gate"

_PLACEHOLDER_TENANT_IDS = {
    "string",
    "tenant",
    "tenant-id",
    "tenant_id",
    "your-tenant-id",
    "<tenant-id>",
    "placeholder",
    "change_me",
    "changeme",
    "default",
    "none",
    "null",
    "__public__",
}


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_url import resolve_operational_database_url

    if cli_url is not None:
        return resolve_operational_database_url(cli_url)

    from src.config.settings import settings

    configured = (
        settings.database_url.get_secret_value() if settings.database_url else None
    )
    return resolve_operational_database_url(cli_url, configured_url=configured)


def _require_explicit_tenant_id(tenant_id: str | None) -> str:
    if tenant_id is None:
        raise ValueError("tenant_id is required for value-import performance gate")
    normalized = tenant_id.strip()
    if not normalized:
        raise ValueError("tenant_id is required for value-import performance gate")
    if normalized != tenant_id:
        raise ValueError("tenant_id must not include leading or trailing whitespace")
    if normalized.lower() in _PLACEHOLDER_TENANT_IDS:
        raise ValueError("tenant_id must be explicit and non-placeholder")
    return tenant_id


def build_gate_csv(
    path: Path,
    *,
    row_count: int,
    external_key_prefix: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start_date = date(2027, 1, 1)
    concepts = VALUE_IMPORT_GATE_CONCEPTS
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "concept",
                "entity",
                "period",
                "external_key",
                "value",
                "unit",
                "value_type",
                "metadata_json",
            ],
        )
        writer.writeheader()
        for index in range(row_count):
            period = start_date + timedelta(days=index)
            writer.writerow(
                {
                    "concept": concepts[index % len(concepts)],
                    "entity": VALUE_IMPORT_GATE_ENTITY,
                    "period": period.isoformat(),
                    "external_key": f"{external_key_prefix}:{index + 1:06d}",
                    "value": str(100 + index),
                    "unit": "m3",
                    "value_type": "numeric",
                    "metadata_json": json.dumps(
                        {
                            "source": GATE_SOURCE,
                            "reporting_period_id": period.isoformat(),
                            "period_type": "point",
                            "reporting_boundary_id": "operational_control",
                        },
                        separators=(",", ":"),
                    ),
                }
            )


def _engine(db_url: str):
    probe_database(
        db_url, operation_label="value-import performance", require_pg15=True
    )
    engine_kwargs = {"pool_pre_ping": True}
    if db_url.startswith("postgresql"):
        engine_kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(db_url, **engine_kwargs)
    init_db_for_engine(engine)
    return engine


def _count_gate_rows(db, external_key_prefix: str, tenant_id: str) -> dict[str, int]:
    key_pattern = f"{external_key_prefix}:%"
    revision_ids = [
        row[0]
        for row in db.query(ValueRevision.id)
        .filter(
            ValueRevision.external_key.like(key_pattern),
            ValueRevision.tenant_id == tenant_id,
        )
        .all()
    ]
    context_ids = [
        row[0]
        for row in db.query(ValueRevision.context_id)
        .filter(
            ValueRevision.external_key.like(key_pattern),
            ValueRevision.tenant_id == tenant_id,
        )
        .distinct()
        .all()
    ]
    return {
        "esg_values": db.query(ESGValue)
        .filter(
            ESGValue.external_key.like(key_pattern), ESGValue.tenant_id == tenant_id
        )
        .count(),
        "value_revisions": len(revision_ids),
        "value_revision_events": (
            db.query(ValueRevisionEvent)
            .filter(
                ValueRevisionEvent.revision_id.in_(revision_ids),
                ValueRevisionEvent.tenant_id == tenant_id,
            )
            .count()
            if revision_ids
            else 0
        ),
        "current_value_pointers": (
            db.query(CurrentValuePointer)
            .filter(
                CurrentValuePointer.revision_id.in_(revision_ids),
                CurrentValuePointer.tenant_id == tenant_id,
            )
            .count()
            if revision_ids
            else 0
        ),
        "value_contexts": (
            db.query(ValueContext)
            .filter(
                ValueContext.id.in_(context_ids), ValueContext.tenant_id == tenant_id
            )
            .count()
            if context_ids
            else 0
        ),
    }


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_gate(
    *,
    db_url: str,
    row_count: int,
    max_seconds: float,
    batch_size: int,
    report_path: Path,
    tenant_id: str | None,
) -> tuple[dict, list[str]]:
    explicit_tenant_id = _require_explicit_tenant_id(tenant_id)
    if explicit_tenant_id != VALUE_IMPORT_GATE_COMPANY_ID:
        raise ValueError(
            f"Value-import fixture tenant must be {VALUE_IMPORT_GATE_COMPANY_ID!r}"
        )
    require_disposable_value_import_target(db_url)
    engine = _engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    external_key_prefix = "gate-value-import-" + datetime.now(timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )
    report: dict = {
        "gate": "value_import_performance",
        "row_count": row_count,
        "max_seconds": max_seconds,
        "batch_size": batch_size,
        "external_key_prefix": external_key_prefix,
    }
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sds_value_import_gate_") as temp_dir:
        csv_path = Path(temp_dir) / "values.csv"
        build_gate_csv(
            csv_path,
            row_count=row_count,
            external_key_prefix=external_key_prefix,
        )

        db = SessionLocal()
        try:
            ensure_value_import_gate_hierarchy(db, created_by=GATE_SOURCE)
        finally:
            db.close()

        imported = None
        elapsed = None
        counts = None
        operation_error = None
        try:
            start = perf_counter()
            imported = import_values_csv(
                csv_path=csv_path,
                db_url=db_url,
                default_entity=VALUE_IMPORT_GATE_ENTITY,
                created_by=GATE_SOURCE,
                batch_size=batch_size,
                tenant_id=explicit_tenant_id,
            )
            elapsed = perf_counter() - start

            db = SessionLocal()
            try:
                counts = _count_gate_rows(db, external_key_prefix, explicit_tenant_id)
            finally:
                db.close()
        except Exception as exc:  # Keep the gate reportable after partial writes.
            operation_error = exc

    report.update(
        {
            "imported": imported,
            "elapsed_seconds": elapsed,
            "counts": counts,
            "disposal": "Discard the separately provisioned disposable PostgreSQL database",
        }
    )

    if operation_error is not None:
        error_kind = type(operation_error).__name__
        failures.append(f"benchmark operation failed: {error_kind}")
        report["operation_error"] = error_kind
    elif imported != row_count:
        failures.append(f"imported {imported} rows, expected {row_count}")
    if counts is not None:
        for table_name, count in counts.items():
            if count != row_count:
                failures.append(f"{table_name} count {count}, expected {row_count}")
    if elapsed is not None and elapsed > max_seconds:
        failures.append(f"elapsed {elapsed:.3f}s exceeds threshold {max_seconds:.3f}s")
    report["passed"] = not failures
    report["failures"] = failures
    write_report(report_path, report)
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate strict operational value import throughput and persistence parity."
    )
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROW_COUNT)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Required explicit tenant/company identifier for DB-writing gate runs",
    )
    args = parser.parse_args()

    try:
        _require_explicit_tenant_id(args.tenant_id)
        args.db_url = get_default_database_url(args.db_url)
        report, failures = run_gate(
            db_url=args.db_url,
            row_count=args.rows,
            max_seconds=args.max_seconds,
            batch_size=args.batch_size,
            report_path=args.report,
            tenant_id=args.tenant_id,
        )
    except ValueError as exc:
        print("Value import performance gate")
        print("FAIL")
        print(f"  - {exc}")
        return 1
    except DatabaseUnavailable as exc:
        report = {
            "gate": "value_import_performance",
            "row_count": args.rows,
            "max_seconds": args.max_seconds,
            "batch_size": args.batch_size,
            "passed": False,
            "status": "not_evaluated",
            "failure_kind": "database_unavailable",
            "failures": [str(exc)],
        }
        write_report(args.report, report)
        print("Value import performance gate")
        print("  status: not_evaluated")
        print(f"  report: {args.report}")
        print("FAIL")
        print(f"  - {exc}")
        return DATABASE_UNAVAILABLE_EXIT_CODE

    print("Value import performance gate")
    print(f"  rows: {report['row_count']}")
    print(f"  imported: {report['imported']}")
    elapsed = report["elapsed_seconds"]
    print(
        f"  elapsed_seconds: {elapsed:.3f}"
        if elapsed is not None
        else "  elapsed_seconds: unavailable"
    )
    print(f"  threshold_seconds: {report['max_seconds']:.3f}")
    print(f"  batch_size: {report['batch_size']}")
    print(f"  counts: {report['counts']}")
    print(f"  report: {args.report}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
