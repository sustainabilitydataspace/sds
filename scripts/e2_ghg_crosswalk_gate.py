#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path

DEFAULT_FILENAME = "e2_crosswalks_ghg_protocol_esrs_gri_strict_exact_v2026-05-14.csv"
REQUIRED_COLUMNS = {
    "mapping_id",
    "source_standard",
    "source_release",
    "source_code",
    "source_label",
    "target_standard",
    "target_release",
    "target_code",
    "target_label",
    "relationship_type",
    "confidence",
    "approval_status",
    "publication_status",
    "sds_cutover_status",
    "source_package_id",
    "source_assertion_id",
    "rationale",
}
ALLOWED_TARGET_STANDARDS = {"ESRS", "GRI"}
DISALLOWED_SCOPE3_TERMS = ("scope3", "scope 3", "305_3", "305-3")


def default_csv_path() -> Path:
    extracted = Path(
        os.environ.get(
            "DATA_EXTRACTED",
            os.path.join(os.environ.get("DATA_ROOT", "data"), "extracted"),
        )
    )
    return extracted / "analysis" / DEFAULT_FILENAME


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"GHG exact crosswalk CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")
        return list(reader)


def validate_rows(
    rows: list[dict[str, str]],
    *,
    expected_count: int,
) -> list[str]:
    errors: list[str] = []
    if len(rows) != expected_count:
        errors.append(f"expected {expected_count} rows, found {len(rows)}")

    seen_ids: set[str] = set()
    target_counts: Counter[str] = Counter()
    for index, row in enumerate(rows, start=2):
        mapping_id = row["mapping_id"].strip()
        if not mapping_id:
            errors.append(f"row {index}: mapping_id is required")
        elif mapping_id in seen_ids:
            errors.append(f"row {index}: duplicate mapping_id {mapping_id}")
        seen_ids.add(mapping_id)

        source_standard = row["source_standard"].strip()
        target_standard = row["target_standard"].strip()
        target_counts[target_standard] += 1

        if source_standard != "GHG":
            errors.append(f"row {index}: source_standard must be GHG")
        if target_standard not in ALLOWED_TARGET_STANDARDS:
            errors.append(
                f"row {index}: target_standard must be one of "
                f"{sorted(ALLOWED_TARGET_STANDARDS)}"
            )
        if row["relationship_type"].strip() != "equivalent":
            errors.append(f"row {index}: relationship_type must be equivalent")
        if row["approval_status"].strip() != "approved_for_local_beta_package":
            errors.append(
                "row "
                f"{index}: approval_status must be approved_for_local_beta_package"
            )
        if row["publication_status"].strip() != "beta_evidence_only":
            errors.append(f"row {index}: publication_status must be beta_evidence_only")
        if row["sds_cutover_status"].strip() != "no_api_change":
            errors.append(f"row {index}: sds_cutover_status must be no_api_change")

        searchable = " ".join(
            [
                row["source_code"],
                row["source_label"],
                row["target_code"],
                row["target_label"],
                row["rationale"],
            ]
        ).lower()
        if any(term in searchable for term in DISALLOWED_SCOPE3_TERMS):
            errors.append(
                f"row {index}: Scope 3/non-exact candidate is not allowed here"
            )

    if target_counts.get("ESRS") != 3 or target_counts.get("GRI") != 3:
        errors.append(
            "expected target distribution ESRS=3 and GRI=3, found "
            + ", ".join(
                f"{standard}={count}" for standard, count in sorted(target_counts.items())
            )
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gate the exact-only GHG Protocol ESRS/GRI crosswalk snapshot."
    )
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--expected-count", type=int, default=6)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if the exact-only checks fail",
    )
    args = parser.parse_args()

    path = args.csv or default_csv_path()
    try:
        rows = load_rows(path)
        errors = validate_rows(rows, expected_count=args.expected_count)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2 if args.strict else 0

    target_counts = Counter(row["target_standard"].strip() for row in rows)
    print("GHG exact crosswalk snapshot:")
    print(f"  path: {path}")
    print(f"  rows: {len(rows)}")
    for standard, count in sorted(target_counts.items()):
        print(f"  - {standard}: {count}")

    if errors:
        print("Exact-only gate errors:")
        for error in errors:
            print(f"  - {error}")
        return 1 if args.strict else 0

    print("GHG exact crosswalk gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
