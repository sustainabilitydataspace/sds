#!/usr/bin/env python3
"""Safe schema migration wrapper for pre-Alembic and Alembic-tracked databases."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import inspect, text

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parent
os.chdir(API_ROOT)
sys.path.insert(0, str(API_ROOT))

from src.database.init_db import init_db, ping_db  # noqa: E402
from src.database.session import engine  # noqa: E402


def current_revision() -> str | None:
    """Return the current Alembic revision if tracking is enabled."""
    with engine.connect() as conn:
        if not inspect(conn).has_table("alembic_version"):
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply or inspect DB schema migrations safely.")
    parser.add_argument("--current", action="store_true", help="Print the current Alembic revision and exit.")
    args = parser.parse_args()

    ping_db()

    if args.current:
        revision = current_revision()
        print(revision or "missing")
        return 0

    init_db()
    revision = current_revision()
    print(f"Migration state ready at revision: {revision or 'missing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
