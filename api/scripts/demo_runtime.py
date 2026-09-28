#!/usr/bin/env python3
"""Qualify the deterministic public SDS demo through supported runtime surfaces.

The database path uses the supported synchronous strict CSV CLI. UC-01 through
UC-03 prove persisted observations and compatible unit conversions. UC-04
through UC-06 are bounded data-contract demonstrations only; they do not
implement policy enforcement, circularity, scoring, certification, or connector
control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(API_ROOT))

# Database dependencies stay lazy so ``manifest`` remains a dependency-free
# public integrity check.
_engine = None
ensure_value_import_gate_hierarchy = None
import_values_csv = None
CurrentValuePointer = None
ESGValue = None
ReportedValuePointer = None
UnitConverter = None
UnitService = None
ValueContext = None
ValueRevision = None
ValueRevisionEvent = None
sessionmaker = None

DEMO_DIR = API_ROOT / "demo"
MANIFEST_PATH = DEMO_DIR / "manifest.json"
VALUES_PATH = DEMO_DIR / "values.csv"
USE_CASES_PATH = DEMO_DIR / "use_cases.json"
EXTERNAL_KEY_PREFIX = "demo-wave1"
# This public demo reuses the disposable strict-import hierarchy so its entity
# is visible to the importer. It is not a production tenant or private ID.
TENANT_ID = "value_import_gate"
CREATED_BY = "sds_public_demo"

# These switches are intentionally scoped to the host process that runs the
# public demo commands.  Compose configures its own service process; importing
# the strict CLI from this process must not silently inherit or guess that
# configuration.
REQUIRED_REVISION_WRITE_ENV = {
    "VALUE_REVISION_API_ENABLED": "true",
    "VALUE_REVISION_DUAL_WRITE_ENABLED": "true",
    "VALUE_REVISION_PRIMARY_READ_PATH": "revision",
}


def get_default_database_url(cli_url: str | None = None) -> str:
    from scripts.operational_db_settings import (
        resolve_operational_database_url_with_settings,
    )

    return resolve_operational_database_url_with_settings(cli_url)


def get_default_admin_password() -> str | None:
    """Return the CI bootstrap secret without accepting it on the command line."""
    return os.getenv("DEMO_CI_BOOTSTRAP_ADMIN_PASSWORD") or os.getenv(
        "BOOTSTRAP_ADMIN_PASSWORD"
    )


def require_revision_write_configuration() -> None:
    """Fail closed unless this host CLI explicitly enables revision writes.

    This runs before importing the strict importer (and therefore before its
    settings singleton can be constructed).  It deliberately does not mutate
    the environment or product defaults: Make/CI or a direct caller must make
    the bounded demo-process configuration explicit.
    """
    invalid = {
        name: os.getenv(name)
        for name, expected in REQUIRED_REVISION_WRITE_ENV.items()
        if os.getenv(name, "").strip().lower() != expected
    }
    if invalid:
        required = ", ".join(
            f"{name}={value}" for name, value in REQUIRED_REVISION_WRITE_ENV.items()
        )
        received = ", ".join(f"{name}={value!r}" for name, value in invalid.items())
        raise ValueError(
            "public demo database commands require explicit revision-write "
            f"configuration ({required}); invalid or unavailable: {received}"
        )


def _load_database_dependencies() -> None:
    global CurrentValuePointer, ESGValue, ReportedValuePointer
    global UnitConverter, UnitService
    global ValueContext, ValueRevision, ValueRevisionEvent, _engine
    global ensure_value_import_gate_hierarchy, import_values_csv, sessionmaker
    # Must precede all strict-import/runtime imports; those modules construct
    # settings from this host process, not from Compose service defaults.
    require_revision_write_configuration()
    if _engine is not None:
        return
    from sqlalchemy.orm import sessionmaker as sqlalchemy_sessionmaker

    from scripts.gate_value_import_performance import _engine as gate_engine
    from scripts.gate_value_import_performance import (
        ensure_value_import_gate_hierarchy as gate_ensure_hierarchy,
    )
    from scripts.import_values_csv import import_values_csv as strict_import_values_csv
    from src.calculation.unit_converter import UnitConverter as runtime_unit_converter
    from src.database.models import CurrentValuePointer as model_current_value_pointer
    from src.database.models import ESGValue as model_esg_value
    from src.database.models import ReportedValuePointer as model_reported_value_pointer
    from src.database.models import ValueContext as model_value_context
    from src.database.models import ValueRevision as model_value_revision
    from src.database.models import ValueRevisionEvent as model_value_revision_event
    from src.services.unit_service import UnitService as runtime_unit_service

    _engine = gate_engine
    ensure_value_import_gate_hierarchy = gate_ensure_hierarchy
    import_values_csv = strict_import_values_csv
    CurrentValuePointer = model_current_value_pointer
    ESGValue = model_esg_value
    ReportedValuePointer = model_reported_value_pointer
    UnitConverter = runtime_unit_converter
    UnitService = runtime_unit_service
    ValueContext = model_value_context
    ValueRevision = model_value_revision
    ValueRevisionEvent = model_value_revision_event
    sessionmaker = sqlalchemy_sessionmaker


def _bootstrap_unit_catalog(db) -> dict[str, int]:
    """Seed the supported DB-backed unit catalog without burdening manifest checks."""
    from src.database.bootstrap_units import bootstrap_units_if_empty

    return bootstrap_units_if_empty(db)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("format") != "sds-public-demo-v1":
        raise ValueError("unsupported demo manifest format")
    if manifest.get("synthetic_public_data") is not True:
        raise ValueError("demo manifest must declare synthetic public data")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("demo manifest has no file checksums")
    for filename, digest in expected.items():
        if _sha256(DEMO_DIR / filename) != digest:
            raise ValueError(f"demo manifest checksum mismatch: {filename}")
    return manifest


def _expected_use_cases() -> dict[str, dict[str, Any]]:
    payload = json.loads(USE_CASES_PATH.read_text(encoding="utf-8"))
    if payload.get("synthetic_public_data") is not True:
        raise ValueError("use-case fixture must declare synthetic public data")
    use_cases = payload.get("use_cases")
    if set(use_cases or {}) != {f"UC-0{number}" for number in range(1, 7)}:
        raise ValueError("use-case fixture must contain UC-01 through UC-06")
    if not all(isinstance(item, dict) for item in use_cases.values()):
        raise ValueError("use-case fixture entries must be objects")
    return use_cases


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _assert_decimal_equal(actual: Any, expected: Any, label: str) -> None:
    if _decimal(actual) != _decimal(expected):
        raise ValueError(f"{label} mismatch: {actual!r} != {expected!r}")


def _metadata_subset(
    metadata: dict[str, Any], expected: dict[str, Any], label: str
) -> None:
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(
                f"{label} metadata {key!r} mismatch: "
                f"{metadata.get(key)!r} != {value!r}"
            )


def _row_specs(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows = item.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("use-case fixture must define one or more rows")
    return rows


def _demo_external_keys() -> set[str]:
    """Return only the public demo's manifest-backed row identities."""
    return {
        row["external_key"]
        for use_case in _expected_use_cases().values()
        for row in _row_specs(use_case)
    }


def _purge_demo_rows(db: Any) -> dict[str, int]:
    """Remove only rows owned by this public demo, never a key namespace.

    Revision records carry both tenant and ``created_by`` provenance; legacy
    ESG rows carry ``created_by``.  Together with the exact manifest keys,
    those supported fields identify the demo without treating ``demo-wave1:``
    as an ownership boundary.
    """
    external_keys = _demo_external_keys()
    revisions = (
        db.query(ValueRevision.id, ValueRevision.context_id)
        .filter(
            ValueRevision.external_key.in_(external_keys),
            ValueRevision.tenant_id == TENANT_ID,
            ValueRevision.created_by == CREATED_BY,
        )
        .all()
    )
    revision_ids = [row[0] for row in revisions]
    if revision_ids:
        raise ValueError(
            "immutable demo revision history cannot be reset in place; "
            "recreate an explicitly disposable database instead"
        )
    context_ids = {row[1] for row in revisions}
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
    orphan_context_ids = context_ids - remaining_context_ids
    deleted["value_contexts"] = (
        db.query(ValueContext)
        .filter(
            ValueContext.id.in_(orphan_context_ids),
            ValueContext.tenant_id == TENANT_ID,
            ValueContext.created_by == CREATED_BY,
        )
        .delete(synchronize_session=False)
        if orphan_context_ids
        else 0
    )
    deleted["esg_values"] = (
        db.query(ESGValue)
        .filter(
            ESGValue.external_key.in_(external_keys),
            ESGValue.created_by == CREATED_BY,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def _assert_persisted_row(
    row: Any,
    expected: dict[str, Any],
    *,
    converter: Any,
    use_case: str,
) -> dict[str, Any]:
    label = f"{use_case} ({expected['external_key']})"
    for field in ("concept", "entity"):
        if getattr(row, field) != expected[field]:
            raise ValueError(
                f"{label} {field} mismatch: {getattr(row, field)!r} "
                f"!= {expected[field]!r}"
            )

    metadata = row.value_metadata or {}
    _metadata_subset(metadata, expected["metadata"], label)
    if metadata.get("prohibited_conclusion") != expected["prohibited_conclusion"]:
        raise ValueError(f"{label} must retain its explicit prohibited conclusion")

    verification = expected["verification"]
    if verification == "unit_conversion":
        if row.original_value is None or row.original_unit is None:
            raise ValueError(f"{label} must retain its source value and source unit")
        _assert_decimal_equal(row.original_value, expected["source_value"], label)
        if row.original_unit != expected["source_unit"]:
            raise ValueError(
                f"{label} source unit mismatch: {row.original_unit!r} "
                f"!= {expected['source_unit']!r}"
            )
        result = converter.convert(
            row.original_value,
            row.original_unit,
            expected["target_unit"],
        )
        _assert_decimal_equal(
            result.converted_value,
            expected["target_value"],
            f"{label} runtime conversion",
        )
        _assert_decimal_equal(row.value, expected["target_value"], label)
        return {
            "external_key": expected["external_key"],
            "source_unit": row.original_unit,
            "target_unit": result.converted_unit,
            "target_value": str(result.converted_value),
        }

    if verification != "bounded_data_contract":
        raise ValueError(f"{label} has unsupported verification type: {verification!r}")
    _assert_decimal_equal(row.value, expected["stored_value"], label)
    if row.unit != expected["stored_unit"]:
        raise ValueError(
            f"{label} stored unit mismatch: {row.unit!r} "
            f"!= {expected['stored_unit']!r}"
        )
    return {
        "external_key": expected["external_key"],
        "verification": verification,
        "prohibited_conclusion": metadata["prohibited_conclusion"],
    }


def verify_database(db_url: str) -> dict[str, Any]:
    """Verify persistence plus actual DB-backed unit-service conversions."""
    _load_database_dependencies()
    expected = _expected_use_cases()
    engine = _engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        rows = {
            row.external_key: row
            for row in db.query(ESGValue)
            .filter(
                ESGValue.external_key.in_(_demo_external_keys()),
                ESGValue.created_by == CREATED_BY,
            )
            .all()
        }
        expected_rows = [row for item in expected.values() for row in _row_specs(item)]
        expected_keys = {item["external_key"] for item in expected_rows}
        missing = sorted(expected_keys - rows.keys())
        unexpected = sorted(rows.keys() - expected_keys)
        if missing or unexpected:
            raise ValueError(
                "demo persistence keys mismatch: "
                f"missing={missing!r}, unexpected={unexpected!r}"
            )

        converter = UnitConverter(db_session=db)
        if converter.get_storage_info()["backend"] != "postgresql":
            raise ValueError("demo verification requires the PostgreSQL unit catalog")
        unit_service = UnitService(db)
        results: dict[str, list[dict[str, Any]]] = {}
        for name, item in expected.items():
            results[name] = [
                _assert_persisted_row(
                    rows[row_spec["external_key"]],
                    row_spec,
                    converter=converter,
                    use_case=name,
                )
                for row_spec in _row_specs(item)
            ]
        uc02 = expected["UC-02"]["rows"][0]
        service_result = unit_service.convert(
            _decimal(uc02["source_value"]),
            uc02["source_unit"],
            uc02["target_unit"],
        )
        _assert_decimal_equal(
            service_result.converted_value,
            uc02["target_value"],
            "UC-02 DB unit service conversion",
        )
        return {
            "persisted_rows": len(rows),
            "service_verification": results,
            "db_unit_service_verification": {
                "external_key": uc02["external_key"],
                "source_unit": service_result.original_unit,
                "target_unit": service_result.converted_unit,
                "target_value": str(service_result.converted_value),
            },
            "boundary": "UC-04..UC-06 are bounded data-contract demonstrations",
        }
    finally:
        db.close()


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("API request URL must use http or https")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        # The explicit scheme allowlist above excludes file and custom handlers.
        with urlopen(request, timeout=20) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ValueError(
            f"API request {method} {url} failed: {error.code} {detail}"
        ) from error
    except URLError as error:
        raise ValueError(
            f"API request {method} {url} failed: {error.reason}"
        ) from error


def _login_api_principal(base_url: str, *, username: str, password: str) -> str:
    login = _http_json(
        f"{base_url}/auth/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    token = login.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("API login did not return an access token")
    return token


def _require_demo_tenant_principal(identity: Any) -> None:
    """Reject API reads unless the authenticated account is the demo tenant DM."""
    if not isinstance(identity, dict):
        raise ValueError("API verification requires a tenant-bound principal")
    if identity.get("company_id") != TENANT_ID:
        raise ValueError(
            "API verification requires a principal bound to the demo tenant"
        )
    if identity.get("role") != "data_manager":
        raise ValueError("API verification requires a tenant-bound data_manager")


def verify_api(
    api_url: str,
    *,
    tenant_principal_token: str | None,
) -> dict[str, Any]:
    """Verify demo rows through a verified tenant-bound data-manager token."""
    if not tenant_principal_token:
        raise ValueError("API verification requires a tenant-bound principal")
    expected = _expected_use_cases()
    base_url = api_url.rstrip("/")
    _require_demo_tenant_principal(
        _http_json(f"{base_url}/auth/me", token=tenant_principal_token)
    )

    expected_rows = [row for item in expected.values() for row in _row_specs(item)]
    values = _http_json(
        f"{base_url}/api/v1/values?entity=gate_facility&limit=1000",
        token=tenant_principal_token,
    )
    visible = {
        item.get("external_key"): item
        for item in values.get("items", [])
        if item.get("external_key", "").startswith(f"{EXTERNAL_KEY_PREFIX}:")
    }
    expected_keys = {item["external_key"] for item in expected_rows}
    if set(visible) != expected_keys:
        raise ValueError("API value list does not expose exactly the demo rows")

    conversions: list[dict[str, Any]] = []
    for row_spec in expected_rows:
        if row_spec["verification"] != "unit_conversion":
            continue
        response = _http_json(
            f"{base_url}/api/v1/convert",
            method="POST",
            payload={
                "value": row_spec["source_value"],
                "from_unit": row_spec["source_unit"],
                "to_unit": row_spec["target_unit"],
            },
            token=tenant_principal_token,
        )
        _assert_decimal_equal(
            response.get("converted_value"),
            row_spec["target_value"],
            f"API conversion for {row_spec['external_key']}",
        )
        conversions.append(
            {
                "external_key": row_spec["external_key"],
                "converted_unit": response.get("converted_unit"),
                "converted_value": response.get("converted_value"),
            }
        )
    return {"api_visible_rows": len(visible), "api_conversions": conversions}


def verify_ci_api_principal_path(
    api_url: str,
    *,
    admin_username: str,
    admin_password: str,
) -> dict[str, Any]:
    """Run the CI-only API proof with a fresh tenant-bound data manager.

    The bootstrap admin is used only for the supported user-creation request.
    It is never used for values or conversion reads. The API has no supported
    user-deletion endpoint, so this path is valid only in a disposable runtime
    whose volume is destroyed by the workflow.
    """
    base_url = api_url.rstrip("/")
    admin_token = _login_api_principal(
        base_url, username=admin_username, password=admin_password
    )
    identity_suffix = secrets.token_hex(12)
    username = f"demo_ci_dm_{identity_suffix}"
    password = secrets.token_urlsafe(32)
    created = _http_json(
        f"{base_url}/auth/users",
        method="POST",
        payload={
            "username": username,
            "email": f"{username}@example.com",
            "full_name": "Disposable CI demo verifier",
            "company_id": TENANT_ID,
            "role": "data_manager",
            "password": password,
        },
        token=admin_token,
    )
    _require_demo_tenant_principal(created)
    tenant_principal_token = _login_api_principal(
        base_url, username=username, password=password
    )
    result = verify_api(
        base_url,
        tenant_principal_token=tenant_principal_token,
    )
    result["api_principal_boundary"] = (
        "runtime-random tenant-bound data_manager; user cleanup is unsupported "
        "and requires disposable-volume removal"
    )
    return result


def install(db_url: str) -> dict[str, Any]:
    verify_manifest()
    _expected_use_cases()
    _load_database_dependencies()

    engine = _engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        _purge_demo_rows(db)
        _bootstrap_unit_catalog(db)
        ensure_value_import_gate_hierarchy(db, created_by=CREATED_BY)
    finally:
        db.close()
    imported = import_values_csv(
        csv_path=VALUES_PATH,
        db_url=db_url,
        default_entity="gate_facility",
        created_by=CREATED_BY,
        tenant_id=TENANT_ID,
    )
    result = verify_database(db_url)
    result["imported_rows"] = imported
    return result


def reset(db_url: str) -> dict[str, int]:
    _load_database_dependencies()
    engine = _engine(db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        return _purge_demo_rows(db)
    finally:
        db.close()


def benchmark(db_url: str) -> dict[str, Any]:
    """Run the separate 1k regression tripwire without writing public evidence."""
    _load_database_dependencies()
    from gate_value_import_performance import run_gate

    with tempfile.TemporaryDirectory(prefix="sds_public_demo_benchmark_") as temp_dir:
        report, failures = run_gate(
            db_url=db_url,
            row_count=1000,
            max_seconds=30.0,
            batch_size=250,
            report_path=Path(temp_dir) / "benchmark-report.json",
            tenant_id=TENANT_ID,
        )
    if failures:
        raise ValueError("benchmark failed: " + "; ".join(failures))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("manifest", "install", "verify", "reset", "benchmark")
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--ci-api-principal",
        action="store_true",
        help=(
            "run the disposable CI-only API qualification with a generated "
            "tenant-bound data_manager"
        ),
    )
    parser.add_argument("--api-url")
    parser.add_argument(
        "--admin-username", default=os.getenv("DEMO_ADMIN_USERNAME", "admin")
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "manifest":
            result = verify_manifest()
        elif args.command == "install":
            args.db_url = get_default_database_url(args.db_url)
            result = install(args.db_url)
        elif args.command == "verify":
            verify_manifest()
            args.db_url = get_default_database_url(args.db_url)
            result = verify_database(args.db_url)
            if args.api_url and not args.ci_api_principal:
                raise ValueError(
                    "--api-url requires --ci-api-principal; ordinary demo verification "
                    "is DB-backed only"
                )
            if args.ci_api_principal:
                if not args.api_url:
                    raise ValueError("--ci-api-principal requires --api-url")
                admin_password = get_default_admin_password()
                if not admin_password:
                    raise ValueError(
                        "DEMO_CI_BOOTSTRAP_ADMIN_PASSWORD is required for "
                        "--ci-api-principal"
                    )
                result["api_verification"] = verify_ci_api_principal_path(
                    args.api_url,
                    admin_username=args.admin_username,
                    admin_password=admin_password,
                )
        elif args.command == "benchmark":
            verify_manifest()
            args.db_url = get_default_database_url(args.db_url)
            result = benchmark(args.db_url)
        else:
            args.db_url = get_default_database_url(args.db_url)
            result = reset(args.db_url)
    except Exception as error:
        print(f"FAIL: {error}")
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
