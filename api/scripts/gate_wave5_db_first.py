#!/usr/bin/env python3
"""Wave 5 gate: strict runtime must be DB-first and free of legacy semantic planes."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import HTTPException
from rdflib import Graph
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.config.settings import settings
from src.calculation.unit_converter import UnitConverter, UnitConversionError
from src.services.canonical_data import CanonicalDataUnavailableError
from src.services.indicator_store import IndicatorStore
from src.services.standard_mapping_store import StandardMappingStore
import src.api.routers.indicators as indicators_router
import src.api.routers.mappings as mappings_router
import src.api.routers.ontology as ontology_router
from src.api.main import app, _dependency_health
import src.ontology.local_graph as local_graph
from src.database.session import engine


DEFAULT_REPORT = REPO_ROOT / "data" / "extracted" / "analysis" / "wave5_db_first_report.json"


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _assert_store_strict_failure(store_cls, method_name: str, *args) -> str:
    failing_db = MagicMock()
    failing_db.query.side_effect = RuntimeError("db unavailable")
    store = store_cls(db=failing_db)
    try:
        getattr(store, method_name)(*args)
    except CanonicalDataUnavailableError:
        return "strict_error"
    return "unexpected_fallback"


async def _assert_ontology_strict_failure() -> str:
    service = MagicMock()
    service.has_semantic_data.return_value = False
    try:
        await ontology_router.list_concepts(
            taxonomy=None,
            concept_type=None,
            search=None,
            limit=10,
            offset=0,
            graph=Graph(),
            concept_service=service,
            current_user=MagicMock(),
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            return "strict_503"
        return f"unexpected_http_{exc.status_code}"
    return "unexpected_fallback"


async def _assert_indicator_router_strict_failure() -> str:
    try:
        await indicators_router.list_indicators(
            limit=10,
            offset=0,
            db=None,
            user=MagicMock(),
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            return "strict_503"
        return f"unexpected_http_{exc.status_code}"
    return "unexpected_fallback"


async def _assert_mapping_router_strict_failure() -> str:
    try:
        await mappings_router.list_mappings(
            limit=10,
            offset=0,
            db=None,
            user=MagicMock(),
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            return "strict_503"
        return f"unexpected_http_{exc.status_code}"
    return "unexpected_fallback"


def _assert_local_graph_strict_failure() -> str:
    original = local_graph._resolve_ontology_files
    local_graph.load_ontology_graph.cache_clear()
    local_graph._resolve_ontology_files = lambda: [Path("base.owl")]
    try:
        try:
            local_graph.load_ontology_graph()
        except RuntimeError as exc:
            if "core_tbox.owl + generated_projection.owl" in str(exc):
                return "strict_error"
            return f"unexpected_runtime:{exc}"
        return "unexpected_fallback"
    finally:
        local_graph._resolve_ontology_files = original
        local_graph.load_ontology_graph.cache_clear()


def _assert_sync_surface_removed() -> str:
    paths = app.openapi().get("paths", {})
    legacy = sorted(path for path in paths if path.startswith("/api/v1/sync"))
    if legacy:
        return f"present:{','.join(legacy)}"
    return "removed"


def _assert_dependency_surface_is_canonical() -> str:
    _status_code, payload = _dependency_health()
    statuses = payload["dependencies"]
    if set(statuses) == {"database"}:
        return "database_only"
    return f"unexpected:{','.join(sorted(statuses))}"


def _assert_units_db_first_backend() -> str:
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        converter = UnitConverter(db_session=db)
        info = converter.get_storage_info()
        return f"backend:{info['backend']}"
    except UnitConversionError as exc:
        return f"error:{exc}"
    finally:
        db.close()


def main() -> int:
    previous_require_database = settings.require_database
    settings.require_database = True

    failures: list[str] = []
    report = {
        "gate": "wave5_db_first",
        "stores": {
            "indicators": _assert_store_strict_failure(IndicatorStore, "get_all"),
            "mappings": _assert_store_strict_failure(StandardMappingStore, "get_all"),
        },
        "catalog_routers": {
            "indicators": asyncio.run(_assert_indicator_router_strict_failure()),
            "mappings": asyncio.run(_assert_mapping_router_strict_failure()),
        },
        "ontology_router": asyncio.run(_assert_ontology_strict_failure()),
        "local_graph_loader": _assert_local_graph_strict_failure(),
        "legacy_sync_surface": _assert_sync_surface_removed(),
        "dependency_surface": _assert_dependency_surface_is_canonical(),
        "units_backend": _assert_units_db_first_backend(),
    }

    settings.require_database = previous_require_database

    for component, status in report["stores"].items():
        if status != "strict_error":
            failures.append(f"{component} store did not fail strictly in DB-first mode: {status}")
    for component, status in report["catalog_routers"].items():
        if status != "strict_503":
            failures.append(f"{component} router did not return 503 in DB-first mode: {status}")

    if report["ontology_router"] != "strict_503":
        failures.append(f"ontology router did not return 503 in DB-first mode: {report['ontology_router']}")
    if report["local_graph_loader"] != "strict_error":
        failures.append(
            "local ontology loader did not reject single-file fallback in DB-first mode: "
            f"{report['local_graph_loader']}"
        )
    if report["legacy_sync_surface"] != "removed":
        failures.append(f"legacy sync surface is still published: {report['legacy_sync_surface']}")
    if report["dependency_surface"] != "database_only":
        failures.append(
            f"dependency health surface is not canonical database-only: {report['dependency_surface']}"
        )
    if report["units_backend"] != "backend:postgresql":
        failures.append(f"unit converter did not use PostgreSQL backend in DB-first mode: {report['units_backend']}")

    report["failures"] = failures
    write_report(DEFAULT_REPORT, report)

    print("Wave 5 DB-first gate")
    for component, status in report["stores"].items():
        print(f"  {component}: {status}")
    for component, status in report["catalog_routers"].items():
        print(f"  router_{component}: {status}")
    print(f"  ontology_router: {report['ontology_router']}")
    print(f"  local_graph_loader: {report['local_graph_loader']}")
    print(f"  legacy_sync_surface: {report['legacy_sync_surface']}")
    print(f"  dependency_surface: {report['dependency_surface']}")
    print(f"  units_backend: {report['units_backend']}")
    print(f"  report: {DEFAULT_REPORT}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
