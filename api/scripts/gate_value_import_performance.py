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
)
from value_import_gate_hierarchy import (  # noqa: E402
    VALUE_IMPORT_GATE_ENTITY,
    ensure_value_import_gate_hierarchy,
)

from src.config.settings import settings  # noqa: E402
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import (  # noqa: E402
    CurrentValuePointer,
    ESGValue,
    ReportedValuePointer,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)

DEFAULT_REPORT = (
    REPO_ROOT
    / "artifacts"
    / "e4-r10-value-import-performance-report-v1-0.json"
)
DEFAULT_ROW_COUNT = 1000
DEFAULT_MAX_SECONDS = 30.0
DEFAULT_BATCH_SIZE = 250
GATE_SOURCE = "value_import_performance_gate"
VALUE_IMPORT_GATE_CONCEPTS = (
    "urn:sds:sample:water_volume",
    "urn:sds:sample:energy_use",
)
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


def get_default_database_url() -> str:
    if settings.database_url:
        return settings.database_url.get_secret_value()

    env_database_url = os.getenv("DATABASE_URL")
    if env_database_url:
        return env_database_url

    postgres_user = os.getenv("POSTGRES_USER", "sds")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "password")
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "sds")
    return f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"


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
    probe_database(db_url, operation_label="value-import performance")
    engine_kwargs = {"pool_pre_ping": True}
    if db_url.startswith("postgresql"):
        engine_kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(db_url, **engine_kwargs)
    init_db_for_engine(engine)
    return engine


def _count_gate_rows(db, external_key_prefix: str) -> dict[str, int]:
    key_pattern = f"{external_key_prefix}:%"
    revision_ids = [
        row[0]
        for row in db.query(ValueRevision.id)
        .filter(ValueRevision.external_key.like(key_pattern))
        .all()
    ]
    context_ids = [
        row[0]
        for row in db.query(ValueRevision.context_id)
        .filter(ValueRevision.external_key.like(key_pattern))
        .distinct()
        .all()
    ]
    return {
        "esg_values": db.query(ESGValue)
        .filter(ESGValue.external_key.like(key_pattern))
        .count(),
        "value_revisions": len(revision_ids),
        "value_revision_events": (
            db.query(ValueRevisionEvent)
            .filter(ValueRevisionEvent.revision_id.in_(revision_ids))
            .count()
            if revision_ids
            else 0
        ),
        "current_value_pointers": (
            db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.revision_id.in_(revision_ids))
            .count()
            if revision_ids
            else 0
        ),
        "value_contexts": (
            db.query(ValueContext).filter(ValueContext.id.in_(context_ids)).count()
            if context_ids
            else 0
        ),
    }


def _purge_gate_rows(db, external_key_prefix: str) -> dict[str, int]:
    key_pattern = f"{external_key_prefix}:%"
    revision_ids = [
        row[0]
        for row in db.query(ValueRevision.id)
        .filter(ValueRevision.external_key.like(key_pattern))
        .all()
    ]
    context_ids = [
        row[0]
        for row in db.query(ValueRevision.context_id)
        .filter(ValueRevision.external_key.like(key_pattern))
        .distinct()
        .all()
    ]

    deleted = {
        "reported_value_pointers": (
            db.query(ReportedValuePointer)
            .filter(ReportedValuePointer.revision_id.in_(revision_ids))
            .delete(synchronize_session=False)
            if revision_ids
            else 0
        ),
        "current_value_pointers": (
            db.query(CurrentValuePointer)
            .filter(CurrentValuePointer.revision_id.in_(revision_ids))
            .delete(synchronize_session=False)
            if revision_ids
            else 0
        ),
        "value_revision_events": (
            db.query(ValueRevisionEvent)
            .filter(ValueRevisionEvent.revision_id.in_(revision_ids))
            .delete(synchronize_session=False)
            if revision_ids
            else 0
        ),
        "value_revisions": (
            db.query(ValueRevision)
            .filter(ValueRevision.id.in_(revision_ids))
            .delete(synchronize_session=False)
            if revision_ids
            else 0
        ),
    }
    remaining_context_ids = (
        {
            row[0]
            for row in db.query(ValueRevision.context_id)
            .filter(ValueRevision.context_id.in_(context_ids))
            .distinct()
            .all()
        }
        if context_ids
        else set()
    )
    orphan_context_ids = [
        context_id
        for context_id in context_ids
        if context_id not in remaining_context_ids
    ]
    deleted["value_contexts"] = (
        db.query(ValueContext)
        .filter(ValueContext.id.in_(orphan_context_ids))
        .delete(synchronize_session=False)
        if orphan_context_ids
        else 0
    )
    deleted["esg_values"] = (
        db.query(ESGValue)
        .filter(ESGValue.external_key.like(key_pattern))
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


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
            _purge_gate_rows(db, external_key_prefix)
        finally:
            db.close()

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
            counts = _count_gate_rows(db, external_key_prefix)
            deleted = _purge_gate_rows(db, external_key_prefix)
            residue = _count_gate_rows(db, external_key_prefix)
        finally:
            db.close()

    report.update(
        {
            "imported": imported,
            "elapsed_seconds": elapsed,
            "counts": counts,
            "deleted": deleted,
            "residue_after_purge": residue,
        }
    )

    if imported != row_count:
        failures.append(f"imported {imported} rows, expected {row_count}")
    for table_name, count in counts.items():
        if count != row_count:
            failures.append(f"{table_name} count {count}, expected {row_count}")
    if elapsed >= max_seconds:
        failures.append(
            f"elapsed {elapsed:.3f}s does not satisfy strict threshold < {max_seconds:.3f}s"
        )
    for table_name, count in residue.items():
        if count != 0:
            failures.append(f"residue after purge: {table_name} count {count}, expected 0")

    report["passed"] = not failures
    report["failures"] = failures
    write_report(report_path, report)
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate strict operational value import throughput and persistence parity."
    )
    parser.add_argument("--db-url", type=str, default=get_default_database_url())
    parser.add_argument("--rows", type=int, default=DEFAULT_ROW_COUNT)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--tenant-id",
        type=str,
        default="sds_public_demo",
        help="Explicit tenant/company identifier for the self-contained public-demo gate",
    )
    args = parser.parse_args()

    try:
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
    print(f"  elapsed_seconds: {report['elapsed_seconds']:.3f}")
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
