#!/usr/bin/env python3
"""Import operational ESG values from a strict CSV contract."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.calculation.unit_converter import UnitConverter  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.database.init_db import init_db_for_engine  # noqa: E402
from src.ontology.local_graph import load_ontology_graph  # noqa: E402
from src.services.canonical_concept_store import CanonicalConceptStore  # noqa: E402
from src.services.hierarchy_store import DatabaseHierarchyStore  # noqa: E402
from src.services.indicator_store import IndicatorStore  # noqa: E402
from src.services.runtime_execution import build_conversion_engine  # noqa: E402
from src.services.value_csv_import import (  # noqa: E402
    ValueCsvContractError,
    load_values_from_csv,
)
from src.services.value_ingest import (  # noqa: E402
    ValueIngestError,
    prepare_value_records,
)
from src.services.value_store import DatabaseValueStore  # noqa: E402

_CALCULATION_VALUE_CONCEPT_FIELDS = (
    "canonical_datapoint_id",
    "node_id",
    "indicator_identifier",
    "component_id",
    "component_node_id",
    "variable_uri",
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
    normalized = (tenant_id or "").strip()
    if not normalized:
        raise ValueIngestError(
            status_code=400,
            message="tenant_id is required for non-dry-run values import",
        )
    if normalized.lower() in _PLACEHOLDER_TENANT_IDS:
        raise ValueIngestError(
            status_code=400,
            message="tenant_id must be explicit and non-placeholder",
        )
    return normalized


def _batched(iterable, n):
    """Yield successive n-sized chunks from iterable."""
    if n < 1:
        raise ValueIngestError(status_code=400, message="batch_size must be >= 1")
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch


def _validate_unique_external_keys(rows) -> None:
    seen: dict[str, int] = {}
    for row_number, row in enumerate(rows, start=2):
        if not row.external_key:
            continue
        if row.external_key in seen:
            raise ValueIngestError(
                status_code=400,
                message=(
                    "Duplicate external_key inside values CSV: "
                    f"external_key={row.external_key} first seen at CSV row "
                    f"{seen[row.external_key]}; duplicated at CSV row {row_number}"
                ),
            )
        seen[row.external_key] = row_number


def load_known_register_identifiers(path: Path | None) -> set[str]:
    if path is None:
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "identifier" not in reader.fieldnames:
            return set()
        return {
            (row.get("identifier") or "").strip()
            for row in reader
            if (row.get("identifier") or "").strip()
        }


def _collect_calculation_value_concepts_from_object(value) -> set[str]:
    concepts: set[str] = set()
    if not isinstance(value, dict):
        return concepts
    for field_name in _CALCULATION_VALUE_CONCEPT_FIELDS:
        raw = value.get(field_name)
        if isinstance(raw, str) and raw.strip():
            concepts.add(raw.strip())
    return concepts


def load_known_calculation_value_concepts(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return set()

    concepts: set[str] = set()
    for node in payload.get("nodes") or []:
        concepts.update(_collect_calculation_value_concepts_from_object(node))
        formula = node.get("formula") if isinstance(node, dict) else None
        if not isinstance(formula, dict):
            continue
        for component_ref in formula.get("component_refs") or []:
            concepts.update(
                _collect_calculation_value_concepts_from_object(component_ref)
            )
    return concepts


class _KnownPackageIndicatorStore:
    def __init__(
        self,
        delegate,
        *,
        known_identifiers: set[str],
        known_calculation_value_concepts: set[str],
    ):
        self._delegate = delegate
        self._known_identifiers = known_identifiers
        self._known_calculation_value_concepts = known_calculation_value_concepts

    def get_by_identifier(self, identifier: str):
        match = self._delegate.get_by_identifier(identifier)
        if match is not None:
            return match
        if identifier in self._known_identifiers:
            return {"identifier": identifier}
        return None

    def get_calculation_value_concept(self, concept: str):
        lookup = getattr(self._delegate, "get_calculation_value_concept", None)
        if callable(lookup):
            match = lookup(concept)
            if match is not None:
                return match
        if concept in self._known_calculation_value_concepts:
            return {"canonical_datapoint_id": concept}
        return None

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


def _canonical_concept_store_for(db):
    if not hasattr(db, "query"):
        return None
    return CanonicalConceptStore(db=db)


def _prepare_values_csv_import(
    *,
    csv_path: Path,
    db_url: str,
    default_entity: str | None,
    created_by: str | None,
    batch_size: int = 250,
    persist: bool,
    known_register_identifiers: set[str] | None = None,
    known_calculation_value_concepts: set[str] | None = None,
    ensure_schema: bool = True,
    tenant_id: str | None = None,
) -> int:
    explicit_tenant_id = _require_explicit_tenant_id(tenant_id) if persist else None

    rows = load_values_from_csv(
        csv_path,
        default_entity=default_entity,
        default_metadata={"source": "csv_import"},
    )
    _validate_unique_external_keys(rows)

    engine_kwargs = {"pool_pre_ping": True}
    if db_url.startswith("postgresql"):
        engine_kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(db_url, **engine_kwargs)
    if ensure_schema:
        init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    graph, _descriptor = load_ontology_graph()

    db = SessionLocal()
    try:
        hierarchy_store = DatabaseHierarchyStore(db)
        indicator_store = IndicatorStore(db=db)
        canonical_concept_store = _canonical_concept_store_for(db)
        if known_register_identifiers or known_calculation_value_concepts:
            indicator_store = _KnownPackageIndicatorStore(
                indicator_store,
                known_identifiers=known_register_identifiers or set(),
                known_calculation_value_concepts=known_calculation_value_concepts
                or set(),
            )
        if explicit_tenant_id is None:
            value_store = DatabaseValueStore(db)
        else:
            value_store = DatabaseValueStore(db, tenant_id=explicit_tenant_id)
        converter = UnitConverter(db_session=db)
        conversion_engine = build_conversion_engine(db, converter)

        prepared_count = 0
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

            if persist and prepared:
                if created_by is None:
                    raise ValueIngestError(
                        status_code=400,
                        message="created_by is required when persisting values",
                    )
                value_store.save(
                    records=prepared,
                    created_by=created_by,
                    refresh=False,
                    return_responses=False,
                )

            prepared_count += len(prepared)

        return prepared_count
    finally:
        db.close()


def import_values_csv(
    *,
    csv_path: Path,
    db_url: str,
    default_entity: str | None,
    created_by: str,
    batch_size: int = 250,
    tenant_id: str | None = None,
) -> int:
    return _prepare_values_csv_import(
        csv_path=csv_path,
        db_url=db_url,
        default_entity=default_entity,
        created_by=created_by,
        batch_size=batch_size,
        persist=True,
        ensure_schema=True,
        tenant_id=tenant_id,
    )


def validate_values_csv_import(
    *,
    csv_path: Path,
    db_url: str,
    default_entity: str | None,
    batch_size: int = 250,
    known_register_identifiers: set[str] | None = None,
    known_calculation_value_concepts: set[str] | None = None,
) -> int:
    return _prepare_values_csv_import(
        csv_path=csv_path,
        db_url=db_url,
        default_entity=default_entity,
        created_by=None,
        batch_size=batch_size,
        persist=False,
        known_register_identifiers=known_register_identifiers,
        known_calculation_value_concepts=known_calculation_value_concepts,
        ensure_schema=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import operational ESG values from CSV."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="CSV file with concept,entity,period,value,unit and optional value_type",
    )
    parser.add_argument(
        "--db-url", type=str, default=get_default_database_url(), help="PostgreSQL URL"
    )
    parser.add_argument(
        "--default-entity",
        type=str,
        default=None,
        help="Default entity when CSV row leaves entity blank",
    )
    parser.add_argument(
        "--created-by",
        type=str,
        default="import_values_csv.py",
        help="Audit value for created_by",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Required tenant/company identifier for non-dry-run imports",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV contract without writing to the database",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="Number of values to prepare and persist per batch",
    )
    parser.add_argument(
        "--known-register-csv",
        type=Path,
        default=None,
        help=(
            "Register CSV whose identifiers should count as known during dry-run "
            "validation of a full SDS package."
        ),
    )
    parser.add_argument(
        "--known-calculation-contract-json",
        type=Path,
        default=None,
        help=(
            "Calculation contract JSON whose node/component concepts should count "
            "as known during dry-run validation of a full SDS package."
        ),
    )
    args = parser.parse_args(argv)

    try:
        if (
            args.known_register_csv is not None
            or args.known_calculation_contract_json is not None
        ) and not args.dry_run:
            print(
                "FAIL: --known-register-csv and --known-calculation-contract-json "
                "are only valid with --dry-run"
            )
            return 1

        if args.dry_run:
            validated = validate_values_csv_import(
                csv_path=args.csv,
                db_url=args.db_url,
                default_entity=args.default_entity,
                batch_size=args.batch_size,
                known_register_identifiers=load_known_register_identifiers(
                    args.known_register_csv
                ),
                known_calculation_value_concepts=load_known_calculation_value_concepts(
                    args.known_calculation_contract_json
                ),
            )
            print(f"Validated {validated} values from {args.csv}")
            print("PASS")
            return 0

        imported = import_values_csv(
            csv_path=args.csv,
            db_url=args.db_url,
            default_entity=args.default_entity,
            created_by=args.created_by,
            batch_size=args.batch_size,
            tenant_id=args.tenant_id,
        )
    except (FileNotFoundError, ValueCsvContractError, ValueIngestError) as error:
        print(f"FAIL: {error}")
        return 1
    except Exception as error:  # pragma: no cover - defensive CLI path
        print(f"FAIL: {error}")
        return 1

    print(f"Imported {imported} values from {args.csv}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
