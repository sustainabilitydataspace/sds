#!/usr/bin/env python3
"""Create a machine-local full SDS environment without printing secrets."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECRETS = (
    "POSTGRES_PASSWORD",
    "JWT_SECRET_KEY",
    "EXPORT_SIGNING_SECRET",
    "BOOTSTRAP_ADMIN_PASSWORD",
)


def render_public_env(template: str, generated: dict[str, str]) -> str:
    """Fill only blank secret fields and the local database URL in the template."""
    if set(generated) != set(REQUIRED_SECRETS):
        raise ValueError("required local credential fields differ from the template contract")
    replacements = dict(generated)
    replacements["DATABASE_URL"] = (
        "postgresql://sds:"
        + generated["POSTGRES_PASSWORD"]
        + "@127.0.0.1:5432/sds"
    )
    counts = {key: 0 for key in replacements}
    output: list[str] = []
    for line in template.splitlines(keepends=True):
        name, separator, value = line.partition("=")
        if separator and name in replacements:
            if value.strip() or counts[name]:
                raise ValueError(f"expected exactly one blank {name} field")
            counts[name] += 1
            line = name + "=" + replacements[name] + "\n"
        output.append(line)
    if any(count != 1 for count in counts.values()):
        raise ValueError("required environment template fields are missing or duplicated")
    return "".join(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output
    if output.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing {output}; use --force deliberately.")
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = {key: secrets.token_urlsafe(48) for key in REQUIRED_SECRETS}
    template = (API_ROOT / ".env.example").read_text(encoding="utf-8")
    rendered = render_public_env(template, generated)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if args.force else os.O_EXCL)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(output, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(rendered)
    print(f"Created {output}. Secrets were generated locally and not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
