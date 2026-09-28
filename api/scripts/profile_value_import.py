#!/usr/bin/env python3
"""Profile value import to identify SQL bottleneck distribution."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import tracemalloc
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

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
from import_values_csv import (  # noqa: E402
    _make_hierarchy_store as DatabaseHierarchyStore,
    _make_indicator_store as IndicatorStore,
    _make_value_store as DatabaseValueStore,
)

from src.calculation.unit_converter import UnitConverter  # noqa: E402
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.database.models import (  # noqa: E402
    CurrentValuePointer,
    ESGValue,
    ValueContext,
    ValueRevision,
    ValueRevisionEvent,
)
from src.ontology.local_graph import load_ontology_graph  # noqa: E402
from src.services.canonical_concept_store import CanonicalConceptStore  # noqa: E402
from src.services.runtime_execution import build_conversion_engine  # noqa: E402
from src.services.value_csv_import import load_values_from_csv  # noqa: E402
from src.services.value_ingest import prepare_value_records  # noqa: E402

DEFAULT_REPORT = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "value_import_profile_report.json"
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
        raise ValueError("tenant_id is required for value-import profiling")
    normalized = tenant_id.strip()
    if not normalized:
        raise ValueError("tenant_id is required for value-import profiling")
    if normalized != tenant_id:
        raise ValueError("tenant_id must not include leading or trailing whitespace")
    if normalized.lower() in _PLACEHOLDER_TENANT_IDS:
        raise ValueError("tenant_id must be explicit and non-placeholder")
    return tenant_id


def build_profile_csv(path: Path, row_count: int, external_key_prefix: str) -> None:
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
                            "source": "value_import_profile",
                            "reporting_period_id": period.isoformat(),
                            "period_type": "point",
                            "reporting_boundary_id": "operational_control",
                        },
                        separators=(",", ":"),
                    ),
                }
            )


def _count_profile_rows(db, external_key_prefix: str) -> dict[str, int]:
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


@dataclass
class QueryCounter:
    total: int = 0
    by_statement: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    statement_max_chars: int = 1000

    def categorize(self, statement: str) -> str:
        stmt_upper = statement.upper()
        if "INSERT" in stmt_upper:
            return "INSERT"
        if "UPDATE" in stmt_upper:
            return "UPDATE"
        if "SELECT" in stmt_upper:
            if "MAX(" in stmt_upper:
                return "SELECT_MAX"
            if "COUNT(" in stmt_upper:
                return "SELECT_COUNT"
            return "SELECT"
        if "DELETE" in stmt_upper:
            return "DELETE"
        return "OTHER"

    def record(self, statement: str) -> None:
        self.total += 1
        statement_key = " ".join(statement.split())
        if len(statement_key) > self.statement_max_chars:
            statement_key = statement_key[: self.statement_max_chars] + " ..."
        self.by_statement[statement_key] = self.by_statement.get(statement_key, 0) + 1
        cat = self.categorize(statement)
        self.by_category[cat] = self.by_category.get(cat, 0) + 1


def attach_query_counter(engine) -> QueryCounter:
    counter = QueryCounter()

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        counter.record(statement)

    return counter


def _batched(iterable, n):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch


def _canonical_concept_store_for(db):
    if not hasattr(db, "query"):
        return None
    return CanonicalConceptStore(db=db)


def run_profile(
    *,
    db_url: str,
    row_count: int,
    batch_size: int,
    report_path: Path,
    tenant_id: str | None,
) -> dict:
    explicit_tenant_id = _require_explicit_tenant_id(tenant_id)
    if explicit_tenant_id != VALUE_IMPORT_GATE_COMPANY_ID:
        raise ValueError(
            f"Value-import fixture tenant must be {VALUE_IMPORT_GATE_COMPANY_ID!r}"
        )
    require_disposable_value_import_target(db_url)
    probe_database(db_url, operation_label="value-import profiling", require_pg15=True)
    engine = create_engine(
        db_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    external_key_prefix = "profile-value-import-" + datetime.now(timezone.utc).strftime(
        "%Y%m%d%H%M%S%f"
    )
    tracemalloc.start()

    with tempfile.TemporaryDirectory(prefix="sds_value_import_profile_") as temp_dir:
        csv_path = Path(temp_dir) / "values.csv"
        build_profile_csv(
            csv_path, row_count=row_count, external_key_prefix=external_key_prefix
        )

        db = SessionLocal()
        try:
            ensure_value_import_gate_hierarchy(db, created_by="value_import_profile")
        finally:
            db.close()

        # Attach counter to the engine before import
        counter = attach_query_counter(engine)

        csv_load_start = perf_counter()
        rows = load_values_from_csv(
            csv_path,
            default_entity=VALUE_IMPORT_GATE_ENTITY,
            default_metadata={"source": "csv_import"},
        )
        csv_load_elapsed = perf_counter() - csv_load_start

        graph, _descriptor = load_ontology_graph()

        db = SessionLocal()
        try:
            hierarchy_store = DatabaseHierarchyStore(db)
            indicator_store = IndicatorStore(db=db)
            canonical_concept_store = _canonical_concept_store_for(db)
            value_store = DatabaseValueStore(db, tenant_id=explicit_tenant_id)
            converter = UnitConverter(db_session=db)
            conversion_engine = build_conversion_engine(db, converter)

            imported = 0
            prepared_batch_count = 0
            max_prepared_batch_size = 0
            start = perf_counter()
            indexed_rows = list(enumerate(rows, start=2))
            for batch in _batched(indexed_rows, batch_size):
                prepared = prepare_value_records(
                    rows=[(str(uuid.uuid4()), row) for _row_number, row in batch],
                    row_numbers=[row_number for row_number, _row in batch],
                    converter=converter,
                    conversion_engine=conversion_engine,
                    hierarchy_store=hierarchy_store,
                    indicator_store=indicator_store,
                    canonical_concept_store=canonical_concept_store,
                    graph=graph,
                    company_id=explicit_tenant_id,
                    strict=True,
                )
                prepared_batch_count += 1
                max_prepared_batch_size = max(max_prepared_batch_size, len(prepared))

                if prepared:
                    value_store.save(
                        records=prepared,
                        created_by="value_import_profile",
                        refresh=False,
                        return_responses=False,
                    )

                imported += len(prepared)
            elapsed = perf_counter() - start
            unit_conversion_count = getattr(
                converter, "_conversion_start_log_count", None
            )
            unit_conversion_log_events = getattr(
                converter, "_conversion_start_log_emitted", None
            )
        finally:
            db.close()

        db = SessionLocal()
        try:
            counts = _count_profile_rows(db, external_key_prefix)
        finally:
            db.close()

    memory_current, memory_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sequence_max_query_count = sum(
        count
        for statement, count in counter.by_statement.items()
        if "MAX(" in statement.upper()
        and (
            "VALUE_REVISION_EVENTS" in statement.upper()
            or "VALUE_REVISIONS" in statement.upper()
        )
    )

    report = {
        "profile": "value_import",
        "row_count": row_count,
        "batch_size": batch_size,
        "imported": imported,
        "row_outcomes": {
            "inserted": imported,
            "updated": 0,
            "unchanged": 0,
            "rejected": 0,
        },
        "elapsed_seconds": round(elapsed, 3),
        "csv_load_elapsed_seconds": round(csv_load_elapsed, 3),
        "rows_per_second": round(imported / elapsed, 2) if elapsed > 0 else 0,
        "total_queries": counter.total,
        "queries_per_row": round(counter.total / imported, 2) if imported > 0 else 0,
        "queries_by_category": counter.by_category,
        "sequence_max_query_count": sequence_max_query_count,
        "refresh": {
            "requested": False,
            "explicit_refresh_count": 0,
        },
        "unit_conversion_count": unit_conversion_count,
        "unit_conversion_start_log_events": unit_conversion_log_events,
        "prepared_batch_count": prepared_batch_count,
        "max_prepared_batch_size": max_prepared_batch_size,
        "csv_rows_loaded": len(rows),
        "memory_current_bytes": memory_current,
        "memory_peak_bytes": memory_peak,
        "counts": counts,
        "disposal": "Discard the separately provisioned disposable PostgreSQL database",
        "top_statements": dict(
            sorted(counter.by_statement.items(), key=lambda x: x[1], reverse=True)[:20]
        ),
        "external_key_prefix": external_key_prefix,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile value import SQL bottleneck distribution."
    )
    parser.add_argument("--db-url", type=str, default=None)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Required explicit tenant/company identifier for DB-writing profile runs",
    )
    args = parser.parse_args()

    try:
        _require_explicit_tenant_id(args.tenant_id)
        args.db_url = get_default_database_url(args.db_url)
        report = run_profile(
            db_url=args.db_url,
            row_count=args.rows,
            batch_size=args.batch_size,
            report_path=args.report,
            tenant_id=args.tenant_id,
        )
    except ValueError as exc:
        print("Value import profile")
        print("FAIL")
        print(f"  - {exc}")
        return 1
    except DatabaseUnavailable as exc:
        report = {
            "profile": "value_import",
            "row_count": args.rows,
            "batch_size": args.batch_size,
            "passed": False,
            "status": "not_evaluated",
            "failure_kind": "database_unavailable",
            "failures": [str(exc)],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("Value import profile")
        print("  status: not_evaluated")
        print(f"  report: {args.report}")
        print("FAIL")
        print(f"  - {exc}")
        return DATABASE_UNAVAILABLE_EXIT_CODE

    print("Value import profile")
    print(f"  rows: {report['row_count']}")
    print(f"  batch_size: {report['batch_size']}")
    print(f"  imported: {report['imported']}")
    print(f"  elapsed_seconds: {report['elapsed_seconds']:.3f}")
    print(f"  rows_per_second: {report['rows_per_second']}")
    print(f"  total_queries: {report['total_queries']}")
    print(f"  queries_per_row: {report['queries_per_row']}")
    print(f"  queries_by_category: {report['queries_by_category']}")
    print(f"  report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
