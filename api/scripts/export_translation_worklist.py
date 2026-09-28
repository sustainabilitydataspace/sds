"""Export the SDS->Atomizer translation worklist (LOC-4).

Emits, per public subject and human-facing field, the canonical source text + the locked
``source_hash`` (SHA-256/NFC) that SDS will validate translations against. Atomizer consumes this to
produce ``sds_translations.csv`` (see api/docs/localization-translation-package.md); SDS re-imports
the filled package via localization_service.import_translations, which re-checks every hash + gate.

Usage:
  python scripts/export_translation_worklist.py --subject-kind concept --target-language es \
      --format csv --output-dir artifacts/translations
  python scripts/export_translation_worklist.py --subject-kind all --target-language es --format json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.localization_worklist import (  # noqa: E402
    WORKLIST_FIELDS,
    build_translation_worklist,
)

PACKAGE_CONTRACT_VERSION = "sds-translations-v1"
#: Source tables a kind's worklist reads — used only for the read-only migrated-DB guard.
_REQUIRED_TABLES = {"concept": "concepts", "indicator": "indicators"}
_WORKLIST_COLUMNS = [
    "subject_kind",
    "subject_uri",
    "field",
    "source_language",
    "source_text",
    "source_hash",
    "target_language",
    "status",
]


def _resolve_db_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    for env in ("DATABASE_URL", "SDS_DATABASE_URL"):
        if os.environ.get(env):
            return os.environ[env]
    raise SystemExit("No DB URL: pass --db-url or set DATABASE_URL.")


def _source_catalog_hash(worklist: List[Dict[str, Any]]) -> str:
    """Deterministic digest over (subject_uri, field, source_hash) — the catalog snapshot id."""
    digest = hashlib.sha256()
    for row in sorted(
        worklist, key=lambda r: (r["subject_kind"], r["subject_uri"], r["field"])
    ):
        digest.update(
            f"{row['subject_kind']}|{row['subject_uri']}|{row['field']}|{row['source_hash']}".encode(
                "utf-8"
            )
        )
    return digest.hexdigest()


def _status_breakdown(worklist: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in worklist:
        status = row.get("status")
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _assert_schema_ready(engine, subject_kinds: List[str]) -> None:
    """Read-only guard: the DB must already be migrated. This exporter NEVER upgrades schema.

    (init_db_for_engine would run ``alembic upgrade head`` as a side effect — wrong for a
    read-only export. We only inspect that the tables we read already exist.)
    """
    tables = set(inspect(engine).get_table_names())
    needed = {"localized_text"} | {_REQUIRED_TABLES[k] for k in subject_kinds}
    missing = sorted(needed - tables)
    if missing:
        raise SystemExit(
            "Database is not migrated to the localization schema (missing tables: "
            + ", ".join(missing)
            + "). Run `alembic upgrade head` first; this exporter is read-only."
        )


def _write_csv(path: Path, worklist: List[Dict[str, Any]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_WORKLIST_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in worklist:
        writer.writerow(row)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def run_export(
    *,
    db_url: str,
    subject_kinds: List[str],
    target_language: str | None,
    fmt: str,
    output_dir: Path,
) -> Dict[str, Any]:
    engine = create_engine(
        db_url, connect_args={"connect_timeout": 10}, pool_pre_ping=True
    )
    _assert_schema_ready(engine, subject_kinds)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    manifest: Dict[str, Any] = {
        "package_contract_version": PACKAGE_CONTRACT_VERSION,
        "source_language": "en",
        "target_language": target_language,
        "hash_algorithm": "sha256/nfc",
        "subjects": {},
    }
    try:
        for kind in subject_kinds:
            worklist = build_translation_worklist(
                session,
                subject_kind=kind,
                target_language=target_language,
            )
            stem = f"sds_translation_worklist.{kind}"
            if fmt == "json":
                (output_dir / f"{stem}.json").write_text(
                    json.dumps(worklist, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                _write_csv(output_dir / f"{stem}.csv", worklist)
            manifest["subjects"][kind] = {
                "fields": list(WORKLIST_FIELDS[kind]),
                "row_count": len(worklist),
                "source_catalog_hash": _source_catalog_hash(worklist),
                "status_breakdown": _status_breakdown(worklist),
            }
    finally:
        session.close()

    (output_dir / "worklist_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the SDS->Atomizer translation worklist."
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--subject-kind",
        default="all",
        choices=["all", *WORKLIST_FIELDS.keys()],
    )
    parser.add_argument("--target-language", default=None)
    parser.add_argument("--format", default="csv", choices=["csv", "json"])
    parser.add_argument("--output-dir", default="artifacts/translations")
    args = parser.parse_args()

    kinds = (
        list(WORKLIST_FIELDS.keys())
        if args.subject_kind == "all"
        else [args.subject_kind]
    )
    manifest = run_export(
        db_url=_resolve_db_url(args.db_url),
        subject_kinds=kinds,
        target_language=args.target_language,
        fmt=args.format,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
