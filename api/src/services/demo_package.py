"""Bundled demo A2.3 package: integrity, ownership checks and install.

The package under ``api/demo/a23`` reproduces the public A2.3 verification.
Its manifest digest is pinned here; every file is read once (no symlinks, no
extra or missing files) and only those verified bytes are installed.

Install runs in ONE caller-owned transaction under an advisory lock: shared
reference data (indicators, standard releases/datapoints, foreign contracts)
is inserted when absent and otherwise reused unchanged; demo-owned data
(hierarchy and values of the demo tenant, demo-profile assertions) must match
the package exactly or the install fails with 409 and nothing is written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import structlog
from src.database.models import (
    AdminDemoPackageInstall,
    AtomizerPackageImport,
    CanonicalCalculationContract,
    ESGValue,
    HierarchyConfiguration,
    Indicator,
    MappingAssertionGroup,
    MaterializedPairwiseMapping,
    StandardDatapoint,
)

logger = structlog.get_logger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parents[2] / "demo" / "a23"
PACKAGE_ID = "sds-demo-a23-v1"
MANIFEST_SHA256 = "1b98846d4875bd8195cdbbb4678f758b86d8a7b39b8cca1c78a6485362dba3df"
TENANT = "nordhaven_components_group"
MAPPING_PROFILE = "demo_a23"
CREATED_BY = "sds-demo-a23"
MAX_FILE_BYTES = 1024 * 1024
ALLOWED_DIRS = frozenset({"mappings"})
STEPS = ("indicators", "contract", "hierarchy", "values", "mappings", "materialize")
INSTALL_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"sds-demo-a23-install").digest()[:8], "big", signed=True
)
VALUE_FIELDS = ("concept", "entity", "period", "period_start", "period_end", "unit")


class DemoPackageError(Exception):
    """Refused or failed demo operation; ``status_code`` is the HTTP status."""

    def __init__(self, status_code: int, message: str, step: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.step = step


@dataclass
class Actor:
    user_id: str
    auth_method: str
    request_id: Optional[str] = None


@dataclass
class VerifiedPackage:
    package_id: str
    digest: str
    files: dict[str, bytes] = field(default_factory=dict)

    def text(self, name: str) -> str:
        return self.files[name].decode("utf-8")

    def json(self, name: str) -> Any:
        return json.loads(self.files[name])

    def csv_rows(self, name: str) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(self.text(name))))


def _after_step_hook(step: str) -> None:
    """Test seam: called after each install step inside the transaction."""


# --------------------------------------------------------------------------
# Integrity
# --------------------------------------------------------------------------


def _integrity_error(message: str) -> DemoPackageError:
    return DemoPackageError(
        500, f"demo package integrity check failed: {message}", "integrity"
    )


def _read_file(dir_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise _integrity_error(f"{name} is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_FILE_BYTES:
        raise _integrity_error(f"{name} is too large")
    return data


def _inventory(dir_fd: int, prefix: str = "") -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for entry in os.scandir(dir_fd):
        name = prefix + entry.name
        if entry.is_symlink():
            raise _integrity_error(f"{name} is a symlink")
        if entry.is_dir(follow_symlinks=False):
            if prefix or entry.name not in ALLOWED_DIRS:
                raise _integrity_error(f"unexpected directory {name}")
            sub_fd = os.open(
                entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
            )
            try:
                files.update(_inventory(sub_fd, name + "/"))
            finally:
                os.close(sub_fd)
        elif entry.is_file(follow_symlinks=False):
            files[name] = _read_file(dir_fd, entry.name)
        else:
            raise _integrity_error(f"{name} is not a regular file")
    return files


def load_package(
    package_dir: Optional[Path] = None, *, expected_digest: Optional[str] = None
) -> VerifiedPackage:
    """Read the fixed package once and verify it against the pinned digest."""
    root = Path(package_dir or PACKAGE_DIR)
    try:
        dir_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise _integrity_error("package directory unavailable") from None
    try:
        files = _inventory(dir_fd)
    except OSError:
        raise _integrity_error("package file unreadable") from None
    finally:
        os.close(dir_fd)

    manifest_bytes = files.pop("manifest.json", None)
    if manifest_bytes is None:
        raise _integrity_error("manifest.json missing")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest != (expected_digest or MANIFEST_SHA256):
        raise _integrity_error("manifest digest does not match the pinned value")
    try:
        manifest = json.loads(manifest_bytes)
        declared = manifest["files"]
        package_id = manifest["package_id"]
    except (ValueError, KeyError, TypeError):
        raise _integrity_error("manifest.json is malformed") from None
    if package_id != PACKAGE_ID or manifest.get("tenant") != TENANT:
        raise _integrity_error("unexpected package identity")
    if set(declared) != set(files):
        raise _integrity_error("package files do not match the manifest inventory")
    for name, entry in declared.items():
        data = files[name]
        if hashlib.sha256(data).hexdigest() != entry.get("sha256") or len(
            data
        ) != entry.get("size_bytes"):
            raise _integrity_error(f"{name} does not match the manifest")
    return VerifiedPackage(package_id=package_id, digest=digest, files=files)


# --------------------------------------------------------------------------
# Approval binding
# --------------------------------------------------------------------------

_GROUP_FIELDS = (
    "source_standard_id",
    "source_standard_version",
    "source_code",
    "mapping_profile",
    "relationship_type",
    "coverage_status",
    "confidence",
    "approval_status",
    "publication_status",
    "assertion_hash",
)
_COMPONENT_FIELDS = (
    "component_order",
    "sygris_canonical_uri",
    "component_role",
    "coverage_fraction",
)


def _assertion_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        row["source_standard_id"],
        row["source_standard_version"],
        row["source_code"],
        row["mapping_profile"],
    )


def _package_assertions(package: VerifiedPackage) -> list[dict[str, Any]]:
    components: dict[tuple, list[dict[str, str]]] = {}
    for row in package.csv_rows("mappings/sds_mapping_assertion_components.csv"):
        components.setdefault(_assertion_key(row), []).append(
            {name: str(row[name]) for name in _COMPONENT_FIELDS}
        )
    assertions = []
    for row in package.csv_rows("mappings/sds_mapping_assertion_groups.csv"):
        item = {name: str(row[name]) for name in _GROUP_FIELDS}
        item["components"] = sorted(
            components.get(_assertion_key(row), []),
            key=lambda c: c["component_order"],
        )
        assertions.append(item)
    return sorted(assertions, key=lambda a: a["assertion_hash"])


def _approved_assertions(package: VerifiedPackage) -> list[dict[str, Any]]:
    approval = package.json("approval.json")
    assertions = []
    for item in approval.get("assertions", []):
        normalized = {name: str(item[name]) for name in _GROUP_FIELDS}
        normalized["components"] = sorted(
            (
                {name: str(component[name]) for name in _COMPONENT_FIELDS}
                for component in item.get("components", [])
            ),
            key=lambda c: c["component_order"],
        )
        assertions.append(normalized)
    return sorted(assertions, key=lambda a: a["assertion_hash"])


def validate_approval(package: VerifiedPackage) -> list[dict[str, Any]]:
    """The mapping CSVs must equal approval.json exactly (422 otherwise)."""
    try:
        package_rows = _package_assertions(package)
        approved = _approved_assertions(package)
    except (KeyError, ValueError, TypeError):
        raise DemoPackageError(
            422, "demo assertions do not match the approval", "mappings"
        ) from None
    if (
        not approved
        or package_rows != approved
        or any(
            row["approval_status"] != "approved"
            or row["mapping_profile"] != MAPPING_PROFILE
            for row in package_rows
        )
    ):
        raise DemoPackageError(
            422, "demo assertions do not match the approval", "mappings"
        )
    return approved


# --------------------------------------------------------------------------
# Parsed package parts
# --------------------------------------------------------------------------


def _indicator_ids(package: VerifiedPackage) -> list[str]:
    return [row["identifier"] for row in package.csv_rows("indicators.csv")]


def _contract_nodes(package: VerifiedPackage) -> list[dict[str, Any]]:
    return list(package.json("calculation_contract.json")["nodes"])


def _value_items(package: VerifiedPackage):
    from src.services.value_csv_import import load_values_from_handle

    return load_values_from_handle(io.StringIO(package.text("values.csv")))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def node_digest(node: dict[str, Any]) -> str:
    """Semantic digest of a contract node, identical for package and DB rows."""
    formula = node.get("formula") or {}
    refs = [
        {
            "component_id": ref.get("component_id"),
            "local_variable": ref.get("local_variable"),
            "indicator_identifier": ref.get("indicator_identifier"),
        }
        for ref in formula.get("component_refs") or []
    ]
    payload = {
        "datapoint_id": node.get("canonical_datapoint_id") or node.get("datapoint_id"),
        "indicator_identifier": node.get("indicator_identifier"),
        "runtime_status": node.get("runtime_status"),
        "role": node.get("role"),
        "value_kind": node.get("value_kind"),
        "unit_name": node.get("unit_name"),
        "runtime_expression": formula.get("runtime_expression") or "",
        "component_refs": sorted(refs, key=_canonical),
        "aggregation": node.get("aggregation") or {},
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def contract_row_digest(row: CanonicalCalculationContract) -> str:
    """The same digest from the columns the runtime resolver reads."""
    return node_digest(
        {
            "canonical_datapoint_id": row.canonical_datapoint_id,
            "indicator_identifier": row.indicator_identifier,
            "runtime_status": row.runtime_status,
            "role": row.role,
            "value_kind": row.value_kind,
            "unit_name": row.unit_name,
            "aggregation": row.aggregation_policy or {},
            "formula": {
                "runtime_expression": row.runtime_expression,
                "component_refs": [
                    {
                        "component_id": component.component_id,
                        "local_variable": (component.source_payload or {}).get(
                            "local_variable"
                        ),
                        "indicator_identifier": component.indicator_identifier,
                    }
                    for component in row.components
                ],
            },
        }
    )


# --------------------------------------------------------------------------
# Component checks (read-only)
# --------------------------------------------------------------------------


def _indicator_state(
    db: Session, package: VerifiedPackage
) -> tuple[list[str], list[str], list[str]]:
    ids = _indicator_ids(package)
    rows = {
        row.identifier: row
        for row in db.query(Indicator).filter(Indicator.identifier.in_(ids)).all()
    }
    absent = [i for i in ids if i not in rows]
    inactive = [i for i, row in rows.items() if row.is_active is False]
    reused = [i for i, row in rows.items() if row.is_active is not False]
    return absent, reused, inactive


def _contract_package_hash(package: VerifiedPackage) -> str:
    from src.services.calculation_contract_import import (
        load_calculation_contract_bytes,
        package_hash_for_payload,
    )

    payload, sha = load_calculation_contract_bytes(
        package.files["calculation_contract.json"]
    )
    return package_hash_for_payload(payload, sha)


def _contract_state(db: Session, package: VerifiedPackage) -> str:
    """'unchanged' | 'absent' | 'reused'; raises 409 on any other overlap."""
    package_hash = _contract_package_hash(package)
    completed = (
        db.query(AtomizerPackageImport.id)
        .filter(
            AtomizerPackageImport.package_hash == package_hash,
            AtomizerPackageImport.status == "completed",
        )
        .first()
    )
    if completed is not None:
        return "unchanged"
    nodes = _contract_nodes(package)
    identifiers = [
        n["indicator_identifier"] for n in nodes if n.get("indicator_identifier")
    ]
    active = (
        db.query(CanonicalCalculationContract)
        .filter(
            CanonicalCalculationContract.is_active.is_(True),
            CanonicalCalculationContract.indicator_identifier.in_(identifiers),
        )
        .all()
    )
    if not active:
        return "absent"
    existing = {}
    for row in active:
        existing.setdefault(row.indicator_identifier, []).append(row)
    for node in nodes:
        rows = existing.get(node.get("indicator_identifier"), [])
        if len(rows) != 1 or contract_row_digest(rows[0]) != node_digest(node):
            raise DemoPackageError(
                409,
                "an active calculation contract differs from the demo package",
                "contract",
            )
    return "reused"


def _hierarchy_matches(
    record: HierarchyConfiguration, expected: dict[str, Any]
) -> bool:
    try:
        configuration = json.loads(record.configuration or "{}")
    except ValueError:
        return False

    def levels(items):
        return sorted(
            (item["id"], item["name"], item.get("parent"), int(item["level"]))
            for item in items or []
        )

    return (
        record.hierarchy_type == expected["hierarchy_type"]
        and record.name == expected["name"]
        and levels(configuration.get("levels")) == levels(expected["levels"])
    )


def _hierarchy_state(db: Session, package: VerifiedPackage) -> str:
    expected = package.json("hierarchy.json")
    entity_ids = {level["id"] for level in expected["levels"]}
    candidates = []
    for record in (
        db.query(HierarchyConfiguration)
        .filter(
            HierarchyConfiguration.company_id == TENANT,
            HierarchyConfiguration.is_active.is_(True),
        )
        .all()
    ):
        try:
            level_ids = {
                level.get("id")
                for level in json.loads(record.configuration or "{}").get("levels", [])
            }
        except ValueError:
            level_ids = set()
        if level_ids & entity_ids:
            candidates.append(record)
    if not candidates:
        return "absent"
    if len(candidates) == 1 and _hierarchy_matches(candidates[0], expected):
        return "unchanged"
    raise DemoPackageError(
        409, "the demo tenant hierarchy differs from the package", "hierarchy"
    )


def _value_matches(row: ESGValue, item) -> bool:
    expected = item.model_dump()
    for name in VALUE_FIELDS:
        stored = getattr(row, name)
        wanted = expected.get(name)
        if str(stored) != str(wanted):
            return False
    return (
        row.value is not None
        and Decimal(str(row.value)) == Decimal(str(expected["value"]))
        and (row.value_type or "numeric") == (expected.get("value_type") or "numeric")
    )


def _values_state(db: Session, package: VerifiedPackage) -> tuple[list, int]:
    items = _value_items(package)
    keys = [item.external_key for item in items]
    rows = {
        row.external_key: row
        for row in db.query(ESGValue)
        .filter(
            ESGValue.tenant_id == TENANT,
            ESGValue.ownership_state == "resolved",
            ESGValue.external_key.in_(keys),
        )
        .all()
    }
    absent, unchanged = [], 0
    for item in items:
        row = rows.get(item.external_key)
        if row is None:
            absent.append(item)
        elif _value_matches(row, item):
            unchanged += 1
        else:
            raise DemoPackageError(
                409, "a demo value differs from the package", "values"
            )
    return absent, unchanged


def _mapping_manifest_hash(package: VerifiedPackage) -> str:
    return hashlib.sha256(package.files["mappings/manifest.json"]).hexdigest()


def _group_matches(
    group: MappingAssertionGroup, expected: dict[str, Any], manifest_hash: str
) -> bool:
    provenance = group.provenance or {}
    components = sorted(
        (
            {
                "component_order": str(component.component_order),
                "sygris_canonical_uri": component.canonical_concept.canonical_uri,
                "component_role": component.component_role,
                "coverage_fraction": f"{Decimal(component.coverage_fraction):.4f}",
            }
            for component in group.components
        ),
        key=lambda c: c["component_order"],
    )
    datapoint = group.source_datapoint
    release = datapoint.standard_release if datapoint is not None else None
    return (
        group.mapping_profile == MAPPING_PROFILE
        and group.created_by == CREATED_BY
        and provenance.get("package_manifest_hash") == manifest_hash
        and release is not None
        and release.standard_id == expected["source_standard_id"]
        and release.version == expected["source_standard_version"]
        and datapoint.code == expected["source_code"]
        and group.relationship_type == expected["relationship_type"]
        and group.coverage_status == expected["coverage_status"]
        and Decimal(group.confidence) == Decimal(expected["confidence"])
        and group.approval_status == expected["approval_status"]
        and group.publication_status == expected["publication_status"]
        and group.valid_to is None
        and components == expected["components"]
    )


def _mappings_state(db: Session, package: VerifiedPackage) -> str:
    approved = validate_approval(package)
    manifest_hash = _mapping_manifest_hash(package)
    found = 0
    for expected in approved:
        groups = (
            db.query(MappingAssertionGroup)
            .filter(MappingAssertionGroup.assertion_hash == expected["assertion_hash"])
            .all()
        )
        if len(groups) > 1 or (
            groups and not _group_matches(groups[0], expected, manifest_hash)
        ):
            raise DemoPackageError(
                409, "a demo mapping assertion has another owner or content", "mappings"
            )
        found += len(groups)
    others = (
        db.query(MappingAssertionGroup.id)
        .filter(
            MappingAssertionGroup.mapping_profile == MAPPING_PROFILE,
            MappingAssertionGroup.valid_to.is_(None),
            ~MappingAssertionGroup.assertion_hash.in_(
                [a["assertion_hash"] for a in approved]
            ),
        )
        .first()
    )
    if others is not None:
        raise DemoPackageError(
            409, "the demo mapping profile holds foreign assertions", "mappings"
        )
    if found == 0:
        return "absent"
    if found == len(approved):
        return "unchanged"
    raise DemoPackageError(
        409, "demo mapping assertions are partially present", "mappings"
    )


def component_status(db: Session, package: VerifiedPackage) -> dict[str, Any]:
    """Read-only state of every component; conflicts are reported, not raised."""
    status: dict[str, Any] = {}
    checks = {
        "indicators": lambda: _indicator_state(db, package),
        "contract": lambda: _contract_state(db, package),
        "hierarchy": lambda: _hierarchy_state(db, package),
        "values": lambda: _values_state(db, package),
        "mappings": lambda: _mappings_state(db, package),
    }
    for name, check in checks.items():
        try:
            result = check()
        except DemoPackageError as exc:
            status[name] = {"state": "conflict", "detail": exc.message}
            continue
        if name == "indicators":
            absent, reused, inactive = result
            status[name] = {
                "state": (
                    "conflict" if inactive else ("absent" if absent else "present")
                ),
                "absent": len(absent),
                "present": len(reused),
                "inactive": len(inactive),
            }
        elif name == "values":
            absent, unchanged = result
            status[name] = {
                "state": "absent" if absent else "present",
                "absent": len(absent),
                "present": unchanged,
            }
        else:
            status[name] = {"state": result}
    db.rollback()
    return status


# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------


def _lock(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        raise DemoPackageError(503, "demo install requires PostgreSQL", "lock")
    db.execute(text("SET LOCAL lock_timeout = '30s'"))
    db.execute(text("SET LOCAL statement_timeout = '120s'"))
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INSTALL_LOCK_KEY})
    # Hold the canonical mapping import lock from the ownership preflight
    # through the import (lock order: demo install -> mapping import ->
    # contract package -> pairwise materialization).
    from src.services.canonical_mapping_import_lock import (
        CANONICAL_MAPPING_IMPORT_LOCK_KEY,
    )

    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": CANONICAL_MAPPING_IMPORT_LOCK_KEY},
    )


def _install_indicators(db, package, actor) -> dict[str, int]:
    from src.services.indicator_import import apply_indicator_import_transactional

    absent, reused, inactive = _indicator_state(db, package)
    if inactive:
        raise DemoPackageError(
            409, "a demo indicator exists but is inactive", "indicators"
        )
    if not absent:
        return {"created": 0, "reused": len(reused)}
    rows = package.csv_rows("indicators.csv")
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_ALL
    )
    writer.writeheader()
    writer.writerows(row for row in rows if row["identifier"] in set(absent))
    apply_indicator_import_transactional(
        csv_text=buffer.getvalue(),
        db=db,
        source_ref=PACKAGE_ID,
        source_hash=hashlib.sha256(package.files["indicators.csv"]).hexdigest(),
        created_by=f"admin:{actor.user_id}",
        commit=False,
    )
    return {"created": len(absent), "reused": len(reused)}


def _install_contract(db, package, actor) -> dict[str, int]:
    from src.services.calculation_contract_import import (
        import_calculation_contract_bytes,
    )

    state = _contract_state(db, package)
    nodes = len(_contract_nodes(package))
    if state == "unchanged":
        return {"created": 0, "unchanged": nodes}
    if state == "reused":
        return {"created": 0, "reused": nodes}
    import_calculation_contract_bytes(
        package.files["calculation_contract.json"],
        db=db,
        dry_run=False,
        created_by=f"admin:{actor.user_id}",
        retirement_scope="incoming_keys",
        source_label="sds-demo-package",
        commit=False,
    )
    db.flush()
    return {"created": nodes}


def _install_hierarchy(db, package, actor) -> dict[str, int]:
    from src.api.models import HierarchyConfiguration as HierarchyModel
    from src.services.hierarchy_store import DatabaseHierarchyStore

    if _hierarchy_state(db, package) == "unchanged":
        return {"created": 0, "unchanged": 1}
    import uuid

    config = HierarchyModel(**package.json("hierarchy.json"))
    DatabaseHierarchyStore(db).create(
        config,
        config_id=str(uuid.uuid4()),
        created_by=f"admin:{actor.user_id}",
        commit=False,
    )
    return {"created": 1, "unchanged": 0}


def _install_values(db, package, actor, *, converter, graph) -> dict[str, int]:
    from src.services.canonical_concept_store import CanonicalConceptStore
    from src.services.hierarchy_store import DatabaseHierarchyStore
    from src.services.indicator_store import IndicatorStore
    from src.services.runtime_execution import build_conversion_engine
    from src.services.value_batch import execute_value_batch
    from src.services.value_store import DatabaseValueStore

    absent, unchanged = _values_state(db, package)
    if not absent:
        return {"created": 0, "unchanged": unchanged}
    response = execute_value_batch(
        items=absent,
        converter=converter,
        conversion_engine=build_conversion_engine(db, converter),
        store=DatabaseValueStore(db, tenant_id=TENANT),
        hierarchy_store=DatabaseHierarchyStore(db),
        indicator_store=IndicatorStore(db=db),
        canonical_concept_store=CanonicalConceptStore(db=db),
        graph=graph,
        created_by=f"admin:{actor.user_id}",
        company_id=TENANT,
        strict=True,
        commit=False,
    )
    if response.rejected_rows:
        raise DemoPackageError(
            409, "the demo values were rejected by value ingest", "values"
        )
    return {"created": len(absent), "unchanged": unchanged}


def _install_mappings(db, package) -> dict[str, int]:
    """Create demo assertions once; never repair or update existing rows.

    When the demo assertions already exist (identical), nothing is written and
    they are revalidated. When absent, the import must create every demo
    group/component; finding or updating any existing row means something
    appeared after the preflight, which fails closed with 409.
    """
    from src.services.canonical_mapping_db_import import (
        import_canonical_mapping_package_to_db,
    )

    state = _mappings_state(db, package)
    _after_step_hook("mappings_preflight")
    groups = len(validate_approval(package))
    if state == "unchanged":
        if _mappings_state(db, package) != "unchanged":
            raise DemoPackageError(
                409, "demo mapping assertions changed during install", "mappings"
            )
        return {"created": 0, "updated": 0, "unchanged": groups, "reused": 0}

    with tempfile.TemporaryDirectory(prefix="sds-demo-a23-") as tmp:
        root = Path(tmp)
        for name, data in package.files.items():
            if name.startswith("mappings/"):
                (root / name.split("/", 1)[1]).write_bytes(data)
        report = import_canonical_mapping_package_to_db(
            package_dir=root,
            db=db,
            created_by=CREATED_BY,
            commit=False,
            reference_data_mode="insert_only",
        )
    if not report.valid or report.blocked:
        raise DemoPackageError(
            409, "the demo mapping package was not imported", "mappings"
        )
    counts = report.counts
    for table in ("mapping_assertion_groups", "mapping_assertion_components"):
        if (
            int(counts.get(f"{table}_created", 0)) != groups
            or int(counts.get(f"{table}_updated", 0))
            or int(counts.get(f"{table}_unchanged", 0))
        ):
            raise DemoPackageError(
                409, "demo mapping assertions appeared during install", "mappings"
            )
    db.flush()
    # Fail closed if anything touched a non-demo assertion (raises 409).
    if _mappings_state(db, package) != "unchanged":
        raise DemoPackageError(
            409, "demo mapping assertions were not imported", "mappings"
        )
    totals = {"created": 0, "updated": 0, "unchanged": 0, "reused": 0}
    for key, value in report.counts.items():
        action = key.rsplit("_", 1)[-1]
        if action in totals and isinstance(value, int):
            totals[action] += value
    return totals


def _install_materialization(db) -> tuple[dict[str, int], str]:
    from src.services.canonical_pairwise_materialization import (
        materialize_pairwise_mappings,
    )

    report = materialize_pairwise_mappings(
        db=db, mapping_profile=MAPPING_PROFILE, commit=False
    )
    if report.blocked:
        raise DemoPackageError(409, "demo materialization is blocked", "materialize")
    counts = report.counts
    return (
        {
            "created": int(counts.get("pairwise_created", 0)),
            "unchanged": int(counts.get("pairwise_unchanged", 0)),
            "covered": int(counts.get("pairwise_covered_by_precedence", 0)),
        },
        report.materialization_hash,
    )


def _structural_check(db: Session, package: VerifiedPackage) -> None:
    absent, _, inactive = _indicator_state(db, package)
    values_absent, values_present = _values_state(db, package)
    datapoint_ids = [
        row.id
        for row in db.query(StandardDatapoint.id)
        .join(
            MappingAssertionGroup,
            MappingAssertionGroup.source_datapoint_id == StandardDatapoint.id,
        )
        .filter(MappingAssertionGroup.mapping_profile == MAPPING_PROFILE)
        .all()
    ]
    pairs = (
        db.query(MaterializedPairwiseMapping.id)
        .filter(
            MaterializedPairwiseMapping.is_current.is_(True),
            MaterializedPairwiseMapping.source_datapoint_id.in_(datapoint_ids),
        )
        .count()
    )
    if (
        absent
        or inactive
        or values_absent
        or values_present != 9
        or _hierarchy_state(db, package) != "unchanged"
        or _contract_state(db, package) == "absent"
        or _mappings_state(db, package) != "unchanged"
        or pairs < 3
    ):
        raise DemoPackageError(500, "demo structural verification failed", "verify")


def _ledger(
    db: Session, actor: Actor, package_digest: str, **fields: Any
) -> AdminDemoPackageInstall:
    row = AdminDemoPackageInstall(
        package_id=PACKAGE_ID,
        package_digest=package_digest,
        actor_user_id=actor.user_id,
        auth_method=actor.auth_method,
        request_id=actor.request_id,
        **fields,
    )
    db.add(row)
    db.flush()
    return row


def install(
    db: Session, package: VerifiedPackage, *, actor: Actor, converter, graph
) -> dict[str, Any]:
    """Install the verified package in one transaction (commits on success)."""
    validate_approval(package)
    step = "lock"
    counts: dict[str, Any] = {}
    try:
        _lock(db)
        step = "indicators"
        counts["indicators"] = _install_indicators(db, package, actor)
        _after_step_hook(step)
        step = "contract"
        counts["contract"] = _install_contract(db, package, actor)
        _after_step_hook(step)
        step = "hierarchy"
        counts["hierarchy"] = _install_hierarchy(db, package, actor)
        _after_step_hook(step)
        step = "values"
        counts["values"] = _install_values(
            db, package, actor, converter=converter, graph=graph
        )
        _after_step_hook(step)
        step = "mappings"
        counts["mappings"] = _install_mappings(db, package)
        _after_step_hook(step)
        step = "materialize"
        counts["materialize"], materialization_hash = _install_materialization(db)
        _after_step_hook(step)
        step = "verify"
        db.flush()
        _structural_check(db, package)
        row = _ledger(
            db,
            actor,
            package.digest,
            state="installed",
            counts=counts,
            materialization_hash_after=materialization_hash,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        failed_step = (
            exc.step if isinstance(exc, DemoPackageError) and exc.step else step
        )
        try:
            _ledger(
                db,
                actor,
                package.digest,
                state="failed",
                failed_step=failed_step,
                error_class=type(exc).__name__,
            )
            db.commit()
        except Exception as ledger_exc:
            db.rollback()
            logger.warning(
                "demo_install_ledger_failed", error_type=type(ledger_exc).__name__
            )
        logger.warning(
            "demo_install_failed", step=failed_step, error_type=type(exc).__name__
        )
        raise
    logger.info("demo_installed", package_id=PACKAGE_ID, ledger_id=row.id)
    return {
        "state": "installed",
        "package_id": PACKAGE_ID,
        "package_digest": package.digest,
        "ledger_id": row.id,
        "counts": counts,
    }


def record_verification(
    db: Session, ledger_id: int, *, ok: bool, checks: dict[str, Any]
) -> str:
    """Store the post-commit functional outcome on the install's ledger row."""
    state = "verified" if ok else "installed_unverified"
    row = db.get(AdminDemoPackageInstall, ledger_id)
    if row is not None:
        row.state = state
        row.checks = {name: bool(item.get("ok")) for name, item in checks.items()}
        db.commit()
    return state


def ledger_history(db: Session, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = (
        db.query(AdminDemoPackageInstall)
        .order_by(AdminDemoPackageInstall.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "state": row.state,
            "failed_step": row.failed_step,
            "error_class": row.error_class,
            "request_id": row.request_id,
            "actor_user_id": row.actor_user_id,
            "counts": row.counts,
            "checks": row.checks,
        }
        for row in rows
    ]
