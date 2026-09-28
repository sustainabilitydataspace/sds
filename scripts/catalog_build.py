#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence


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

from sds_core.dcat.builder import build_dcat_catalog  # noqa: E402
from sds_core.policy.registry import load_policy_registry  # noqa: E402

DEFAULT_INPUT_CANDIDATES = [
    REPO_ROOT / "data" / "processed" / "e1_dataset_register.csv",
]
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "dcat_catalog.jsonld"
)
DEFAULT_POLICY_REGISTRY = REPO_ROOT / "governance" / "policies" / "policy_registry.json"


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def find_col(columns: Sequence[str], *candidates: str) -> str | None:
    lookup = {_normalize_key(col): col for col in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        normalized = _normalize_key(candidate)
        if normalized in lookup:
            return lookup[normalized]
    return None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    return columns, rows


def _candidate_input_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path

    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No input file found. Expected one of: "
        + ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in DEFAULT_INPUT_CANDIDATES
        )
    )


def _cell(row: dict[str, str], column: str | None) -> str:
    if column is None:
        return ""
    return (row.get(column) or "").strip()


def _catalog_rows(
    input_path: Path,
    *,
    base_url: str,
    default_purpose: str,
    default_region: str,
) -> list[dict[str, str]]:
    columns, raw_rows = _read_csv(input_path)
    c_identifier = find_col(columns, "identifier", "id", "dataset_id")
    c_title = find_col(columns, "title", "name", "dataset")
    c_description = find_col(columns, "description", "desc")
    c_dimension = find_col(columns, "dimension", "topic")
    c_owner = find_col(columns, "owner", "publisher")
    c_purpose = find_col(columns, "purpose")
    c_region = find_col(columns, "region", "spatial")
    c_policy = find_col(columns, "policyId", "policy_id")
    c_base_url = find_col(columns, "base_url", "baseUrl", "access_base_url")

    rows: list[dict[str, str]] = []
    for index, row in enumerate(raw_rows, start=1):
        identifier = _cell(row, c_identifier) or f"dataset-{index}"
        rows.append(
            {
                "identifier": identifier,
                "title": _cell(row, c_title) or identifier,
                "description": _cell(row, c_description),
                "dimension": _cell(row, c_dimension),
                "owner": _cell(row, c_owner) or "system",
                "purpose": _cell(row, c_purpose) or default_purpose,
                "region": _cell(row, c_region) or default_region,
                "policyId": _cell(row, c_policy),
                "base_url": _cell(row, c_base_url) or base_url,
            }
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a DCAT-AP catalog from the SDS E1 dataset register"
    )
    parser.add_argument(
        "--input",
        default="",
        help="Input CSV. Defaults to the first existing repo-local E1 register.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output JSON-LD path (default: repo-local analysis catalog).",
    )
    parser.add_argument(
        "--policy-registry",
        default=str(DEFAULT_POLICY_REGISTRY),
        help="Path to the local governance policy registry JSON.",
    )
    parser.add_argument(
        "--base-url",
        default="https://connector.example.org/data-products",
        help="Base URL used for generated dataset distribution access URLs.",
    )
    parser.add_argument(
        "--default-purpose",
        default="reporting",
        help="Fallback purpose when the input does not provide one.",
    )
    parser.add_argument(
        "--default-region",
        default="EU",
        help="Fallback region when the input does not provide one.",
    )
    args = parser.parse_args(argv)

    input_path = _candidate_input_path(args.input or None)
    rows = _catalog_rows(
        input_path,
        base_url=args.base_url,
        default_purpose=args.default_purpose,
        default_region=args.default_region,
    )
    policy_registry: dict[str, Any] = load_policy_registry(Path(args.policy_registry))
    catalog = build_dcat_catalog(
        rows,
        policy_registry=policy_registry,
        default_purpose=args.default_purpose,
        default_region=args.default_region,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote DCAT-AP catalog: {out_path}")
    print(f"Rows exported: {len(rows)}")
    print(f"Datasets written: {len(catalog.get('dcat:dataset', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
