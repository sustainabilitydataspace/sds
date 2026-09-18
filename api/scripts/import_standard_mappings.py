#!/usr/bin/env python3
"""Import E2 standard mappings from CSV into PostgreSQL and bundled JSON."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
REPO_ROOT = API_ROOT.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db_for_engine
from src.database.models import SustainabilityStandard
from src.database.repositories.dataset_snapshot_repository import (
    DatasetSnapshotRepository,
)
from src.database.repositories.standard_mapping_repository import (
    StandardMappingRepository,
)
from src.services.dataset_history import file_sha256, persist_dataset_snapshot
from src.services.dataset_manifest import build_dataset_manifest
from src.services.dataset_serialization import serialize_mapping
from src.services.canonical_mapping_relationship_policy import (
    OPERATIONAL_RELATIONSHIP_TYPES,
    normalize_relationship_type,
)

DEFAULT_CSV_CANDIDATES = [
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e2_crosswalks_esrs_gri_full_atomized.csv",
    REPO_ROOT / "data" / "extracted" / "analysis" / "e2_crosswalks_esrs_gri_full.csv",
]
DEFAULT_JSON_OUTPUT = API_ROOT / "src" / "data" / "mappings.json"
DEFAULT_RELATIONSHIP_OVERRIDES = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "canonical-mapping-legacy-relationship-overrides-v42-reviewed-2026-05-11.csv"
)
LEGACY_OPERATIONAL_RELATIONSHIP_TYPES = OPERATIONAL_RELATIONSHIP_TYPES | {"related"}

SUPPORTED_STANDARDS = [
    {
        "id": "ESRS",
        "name": "European Sustainability Reporting Standards",
        "organization": "EFRAG",
    },
    {
        "id": "GRI",
        "name": "Global Reporting Initiative Standards",
        "organization": "GRI",
    },
]


def safe_print(*args, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    """Print without failing on non-UTF-8 Windows code pages."""
    message = sep.join(str(arg) for arg in args)
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        print(message, end=end, flush=flush)
    except UnicodeEncodeError:
        safe_message = message.encode(encoding, errors="replace").decode(
            encoding, errors="replace"
        )
        stream.write(safe_message + end)
        if flush:
            stream.flush()


def get_default_csv_path() -> Path:
    for candidate in DEFAULT_CSV_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_CSV_CANDIDATES[0]


DEFAULT_CSV_PATH = get_default_csv_path()
ROMAN_SUFFIX_RE = re.compile(r"-(i|ii|iii|iv|v|vi|vii|viii|ix|x)$", re.IGNORECASE)
TRAILING_LETTER_HYPHEN_RE = re.compile(r"([A-Za-z])-$")
COMPOSITE_CODE_TOKENS = (";", "/", ",", " to ", "Guidance")


def get_default_database_url() -> str:
    env_database_url = os.getenv("DATABASE_URL")
    if env_database_url:
        return env_database_url

    postgres_user = os.getenv("POSTGRES_USER", "sds")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "password")
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "sds")
    return f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_non_empty(row: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return None


def _normalize_public_code(standard: str, code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    normalized = re.sub(r"\s+", " ", code.strip())
    if standard.upper() != "GRI":
        return normalized
    if any(token in normalized for token in COMPOSITE_CODE_TOKENS):
        return normalized
    normalized = re.sub(r"\s+\.", ".", normalized)
    normalized = re.sub(r"\.\s+", ".", normalized)
    normalized = ROMAN_SUFFIX_RE.sub(r".\1", normalized)
    normalized = TRAILING_LETTER_HYPHEN_RE.sub(r"\1", normalized)
    return normalized


def _mapping_key(record: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("source_standard") or ""),
        str(record.get("source_code") or ""),
        str(record.get("target_standard") or ""),
        str(record.get("target_code") or ""),
    )


def load_relationship_overrides(
    path: Optional[Path],
) -> Dict[tuple[str, str, str, str], Dict[str, Any]]:
    if path is None or not path.exists():
        return {}

    df = pd.read_csv(path, dtype=str).fillna("")
    required = {
        "source_standard",
        "source_code",
        "target_standard",
        "target_code",
        "relationship_type",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Relationship override file missing columns: {sorted(missing)}"
        )

    overrides: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in df.to_dict(orient="records"):
        target_standard = _first_non_empty(row, ["target_standard"]) or ""
        target_code = _normalize_public_code(
            target_standard,
            _first_non_empty(row, ["target_code"]),
        )
        override = {
            "source_standard": _first_non_empty(row, ["source_standard"]),
            "source_code": _first_non_empty(row, ["source_code"]),
            "target_standard": target_standard,
            "target_code": target_code,
            "relationship_type": _validate_legacy_relationship_type(
                _first_non_empty(row, ["relationship_type"])
            ),
        }
        confidence = _first_non_empty(row, ["confidence"])
        if confidence:
            override["confidence"] = float(confidence)
        if not all(override.get(key) for key in required):
            continue
        overrides[_mapping_key(override)] = override
    return overrides


def _validate_legacy_relationship_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    relationship_type = normalize_relationship_type(value)
    if relationship_type in LEGACY_OPERATIONAL_RELATIONSHIP_TYPES:
        return relationship_type
    allowed = ", ".join(sorted(LEGACY_OPERATIONAL_RELATIONSHIP_TYPES))
    raise ValueError(
        "Legacy standard_mappings overrides cannot use non-operational "
        f"relationship_type={relationship_type!r}; allowed values: {allowed}"
    )


def _as_bool(value: Any) -> Optional[bool]:
    text = _clean(value)
    if text is None:
        return None
    return text.lower() in {"true", "1", "yes", "y"}


def _as_int(value: Any) -> Optional[int]:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def load_csv(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str).fillna("")
    required = ["esrs", "gri", "esg_dimension"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    safe_print(f"Loaded {len(df)} rows from {csv_path}")
    return df


def map_row_to_mapping(
    row: Dict[str, Any],
    relationship_overrides: Optional[
        Dict[tuple[str, str, str, str], Dict[str, Any]]
    ] = None,
) -> Optional[Dict[str, Any]]:
    source_code = _first_non_empty(row, ["esrs"])
    target_code = _normalize_public_code("GRI", _first_non_empty(row, ["gri"]))
    if not source_code or not target_code:
        return None

    metadata: Dict[str, Any] = {}
    for key, output_key in (
        ("interop_topic", "interop_topic"),
        ("ESRS_Standard", "esrs_standard"),
        ("ESRS_XBRL", "esrs_xbrl"),
        ("GRI_XBRL", "gri_xbrl"),
        ("ESRS_AtomizedID", "esrs_atomized_id"),
        ("GRI_AtomizedID", "gri_atomized_id"),
    ):
        value = _clean(row.get(key))
        if value:
            metadata[output_key] = value

    flags = {
        "esrs_detected": _as_bool(row.get("ESRS_Detected")),
        "gri_detected": _as_bool(row.get("GRI_Detected")),
        "with_both": _as_bool(row.get("WithBoth")),
    }
    metadata.update({key: value for key, value in flags.items() if value is not None})

    source_label = _first_non_empty(row, ["ESRS_Label", "indicator"])
    target_label = _first_non_empty(row, ["GRI_Label", "gri_expanded"])
    with_both = bool(flags.get("with_both"))

    mapping = {
        "source_standard": "ESRS",
        "source_code": source_code,
        "source_label": source_label,
        "target_standard": "GRI",
        "target_code": target_code,
        "target_label": target_label,
        "esg_dimension": _first_non_empty(row, ["esg_dimension"]),
        "relationship_type": "equivalent" if with_both else "related",
        "confidence": 1.0 if with_both else 0.75,
        "dataset": _first_non_empty(row, ["dataset"]) or "e2_crosswalks_esrs_gri",
        "source_row": _as_int(row.get("SourceRow")),
        "mapping_metadata": metadata or None,
        "created_by": "import_standard_mappings.py",
        "is_active": True,
    }
    override = (relationship_overrides or {}).get(_mapping_key(mapping))
    if override:
        relationship_type = override.get("relationship_type")
        if relationship_type:
            mapping["relationship_type"] = relationship_type
        if "confidence" in override:
            mapping["confidence"] = override["confidence"]
        mapping["mapping_metadata"] = {
            **(mapping.get("mapping_metadata") or {}),
            "legacy_relationship_override": True,
        }
    return mapping


def build_mapping_records(
    df: pd.DataFrame,
    relationship_overrides: Optional[
        Dict[tuple[str, str, str, str], Dict[str, Any]]
    ] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for row in df.to_dict(orient="records"):
        mapping = map_row_to_mapping(row, relationship_overrides)
        if not mapping:
            continue

        key = (
            mapping["source_standard"],
            mapping["source_code"],
            mapping["target_standard"],
            mapping["target_code"] or "",
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(mapping)

    return records


def write_json_snapshot(records: List[Dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mappings": [
            {
                "source_standard": record["source_standard"],
                "source_code": record["source_code"],
                "source_label": record.get("source_label"),
                "target_standard": record["target_standard"],
                "target_code": record.get("target_code"),
                "target_label": record.get("target_label"),
                "esg_dimension": record.get("esg_dimension"),
                "relationship_type": record.get("relationship_type"),
                "confidence": record.get("confidence"),
                "dataset": record.get("dataset"),
                "source_row": record.get("source_row"),
                "mapping_metadata": record.get("mapping_metadata"),
            }
            for record in records
        ]
    }
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    safe_print(f"Wrote JSON snapshot to {output_path}")


def _seed_supported_standards(db) -> None:
    for standard in SUPPORTED_STANDARDS:
        existing = db.query(SustainabilityStandard).filter_by(id=standard["id"]).first()
        if existing:
            continue
        db.add(SustainabilityStandard(**standard))


def seed_mappings(
    csv_path: Path,
    db_url: Optional[str],
    *,
    json_output: Optional[Path],
    relationship_overrides_path: Optional[Path] = DEFAULT_RELATIONSHIP_OVERRIDES,
    dry_run: bool = False,
    json_only: bool = False,
) -> int:
    df = load_csv(csv_path)
    relationship_overrides = load_relationship_overrides(relationship_overrides_path)
    if relationship_overrides:
        safe_print(f"Loaded {len(relationship_overrides)} relationship overrides")
    records = build_mapping_records(df, relationship_overrides)
    safe_print(f"Prepared {len(records)} ESRS-GRI mappings")

    if dry_run:
        safe_print("DRY RUN: no database writes performed")
        if records:
            safe_print("Sample mapping:", json.dumps(records[0], ensure_ascii=True))
        return len(records)

    if json_output is not None:
        write_json_snapshot(records, json_output)

    if json_only:
        return len(records)

    if not db_url:
        raise ValueError("A database URL is required unless --json-only is used")

    engine = create_engine(
        db_url,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
    )
    init_db_for_engine(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        _seed_supported_standards(db)
        repo = StandardMappingRepository(db)
        count = repo.bulk_upsert(records, deactivate_missing=True)
        current_total = repo.count()
        current_mappings = repo.get_all(limit=max(current_total, 1), offset=0)
        snapshot_payload = [serialize_mapping(item) for item in current_mappings]
        manifest = build_dataset_manifest(
            dataset="mappings",
            items=snapshot_payload,
            last_modified=max(
                (
                    getattr(item, "updated_at", None)
                    or getattr(item, "created_at", None)
                    for item in current_mappings
                ),
                default=None,
            ),
        )
        snapshot = persist_dataset_snapshot(
            repo=DatasetSnapshotRepository(db),
            dataset="mappings",
            manifest=manifest,
            items=snapshot_payload,
            source_ref=str(csv_path),
            source_hash=file_sha256(csv_path),
            created_by="import_standard_mappings.py",
        )
        safe_print(f"Imported {count} mappings into the database")
        safe_print(
            f"Recorded mappings snapshot #{snapshot.id} ({manifest.manifest_hash[:12]})"
        )
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import E2 standard mappings")
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"CSV file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--db-url", type=str, default=get_default_database_url(), help="PostgreSQL URL"
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help=f"Bundled JSON snapshot path (default: {DEFAULT_JSON_OUTPUT})",
    )
    parser.add_argument(
        "--relationship-overrides",
        type=Path,
        default=DEFAULT_RELATIONSHIP_OVERRIDES,
        help=f"Reviewed relationship override CSV (default: {DEFAULT_RELATIONSHIP_OVERRIDES})",
    )
    parser.add_argument(
        "--skip-json",
        action="store_true",
        help="Do not write the bundled JSON snapshot",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Only refresh the bundled JSON snapshot",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate without writing"
    )
    args = parser.parse_args()

    try:
        count = seed_mappings(
            args.csv,
            None if args.json_only else args.db_url,
            json_output=None if args.skip_json else args.json_output,
            relationship_overrides_path=args.relationship_overrides,
            dry_run=args.dry_run,
            json_only=args.json_only,
        )
        safe_print(f"Done. Total prepared mappings: {count}")
        return 0
    except FileNotFoundError as exc:
        safe_print(f"File not found: {exc}")
        return 1
    except ValueError as exc:
        safe_print(f"Validation error: {exc}")
        return 1
    except Exception as exc:
        safe_print(f"Failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
