#!/usr/bin/env python3
"""Validate the Wave 0 schema migration baseline."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(API_ROOT))

from src.database.session import engine  # noqa: E402

ALEMBIC_REQUIRED_FILES = [
    API_ROOT / "alembic.ini",
    API_ROOT / "alembic" / "env.py",
    API_ROOT / "alembic" / "versions" / "001_baseline_schema.py",
    API_ROOT / "alembic" / "versions" / "002_add_indicator_concept_state.py",
]


def main() -> int:
    missing_files = [str(path) for path in ALEMBIC_REQUIRED_FILES if not path.exists()]
    failures: list[str] = []

    if missing_files:
        failures.append("Missing Alembic files: " + ", ".join(missing_files))

    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())

        if "alembic_version" not in tables:
            failures.append("alembic_version table is missing")
            current_revision = None
        else:
            current_revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

        if "indicators" not in tables:
            failures.append("indicators table is missing")
            concept_state_column = False
            null_count = None
        else:
            columns = {column["name"] for column in inspector.get_columns("indicators")}
            concept_state_column = "concept_state" in columns
            if not concept_state_column:
                failures.append("indicators.concept_state column is missing")
                null_count = None
            else:
                null_count = conn.execute(
                    text("SELECT COUNT(*) FROM indicators WHERE concept_state IS NULL")
                ).scalar_one()
                if null_count:
                    failures.append(f"{null_count} indicator rows have concept_state = NULL")

    print("Wave 0 schema gate")
    print(f"  alembic revision: {current_revision or 'missing'}")
    print(f"  indicators.concept_state: {'present' if concept_state_column else 'missing'}")
    if null_count is not None:
        print(f"  NULL concept_state rows: {null_count}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
