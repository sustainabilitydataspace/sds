#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_repo_paths() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    sds_core_src = repo_root / "packages" / "sds_core" / "src"

    for candidate in (repo_root, sds_core_src):
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)

    return repo_root


REPO_ROOT = _bootstrap_repo_paths()

from sds_core.semantics.bundle import build_semantics_bundle_zip  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the SDS semantics bundle (ZIP + manifest)")
    parser.add_argument("--version", default="1.0.0", help="Bundle version (semantic versioning recommended)")
    parser.add_argument(
        "--outdir",
        default=str(REPO_ROOT / "data" / "extracted" / "analysis" / "semantics_bundle"),
        help="Output directory for the bundle and manifest.",
    )
    args = parser.parse_args(argv)

    version = str(args.version).strip().lstrip("v")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    zip_path = outdir / f"sds_semantics_bundle_v{version}.zip"
    manifest_path = outdir / f"sds_semantics_bundle_v{version}_manifest.json"

    manifest = build_semantics_bundle_zip(
        REPO_ROOT,
        zip_path,
        version=version,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Wrote bundle: {zip_path}")
    print(f"Wrote manifest: {manifest_path}")
    print(f"Bundle files: {len(manifest.get('files', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
