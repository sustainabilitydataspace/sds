#!/usr/bin/env python3
"""Create a machine-local public-demo environment without printing secrets."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


TEMPLATE = """POSTGRES_PASSWORD={postgres_password}
JWT_SECRET_KEY={jwt_secret_key}
DATABASE_URL=postgresql://sds:{postgres_password}@localhost:5432/sds
REQUIRE_DATABASE=true
SEED_REFERENCE_DATA_ON_STARTUP=true
SEED_DEFAULT_USERS=false
VALUE_REVISION_PRIMARY_READ_PATH=revision
POSTGRES_PORT=5432
ALLOWED_ORIGINS=[\"http://localhost:8090\"]
CORS_ALLOW_CREDENTIALS=false
PORTAL_MOUNT_ENABLED=false
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing {output}; use --force deliberately.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        TEMPLATE.format(
            postgres_password=secrets.token_urlsafe(32),
            jwt_secret_key=secrets.token_urlsafe(48),
        ),
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(f"Created {output}. Secrets were generated locally and not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
