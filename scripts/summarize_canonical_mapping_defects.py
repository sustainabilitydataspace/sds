from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = {
    "issue_type",
    "source_code",
    "target_code",
    "field",
    "legacy_value",
    "canonical_value",
    "classification",
}


def source_prefix(source_code: str) -> str:
    return source_code.split("-", 1)[0].split(".", 1)[0]


def target_family(target_code: str) -> str:
    parts = (target_code or "").strip().split()
    if len(parts) < 2:
        return target_code or ""
    numeric = parts[1].split(".", 1)[0]
    return f"{parts[0]} {numeric}"


def review_class(row: dict[str, str]) -> str:
    issue_type = row.get("issue_type", "")
    legacy_value = row.get("legacy_value", "")
    canonical_value = row.get("canonical_value", "")
    if issue_type == "confidence_mismatch":
        return "legacy_confidence_overclaim"
    if issue_type == "relationship_mismatch":
        if legacy_value == "equivalent" and canonical_value in {
            "narrower",
            "partial",
            "broader",
        }:
            return f"legacy_equivalence_overclaim_{canonical_value}"
        return "legacy_relationship_mismatch"
    return "other_legacy_mapping_defect"


def proposed_action(row: dict[str, str]) -> str:
    klass = review_class(row)
    if klass == "legacy_confidence_overclaim":
        return (
            "lower legacy confidence to canonical evidence level or record an "
            "accepted drift rationale before cutover"
        )
    if klass.startswith("legacy_equivalence_overclaim_"):
        canonical = row.get("canonical_value", "")
        return (
            f"change legacy relationship away from equivalent toward {canonical} "
            "or keep legacy-backed until this semantic drift is explicitly accepted"
        )
    return "manual semantic review required before cutover"


def load_defects(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no CSV header")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        return [
            row
            for row in reader
            if row.get("classification") == "legacy_mapping_defect"
        ]


def summarize(
    rows: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    enriched: list[dict[str, str]] = []
    class_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    source_prefix_counts: Counter[str] = Counter()
    target_family_counts: Counter[str] = Counter()

    for row in rows:
        item = dict(row)
        item["review_class"] = review_class(item)
        item["proposed_action"] = proposed_action(item)
        item["source_prefix"] = source_prefix(item.get("source_code", ""))
        item["target_family"] = target_family(item.get("target_code", ""))
        enriched.append(item)
        class_counts[item["review_class"]] += 1
        issue_counts[item.get("issue_type", "")] += 1
        source_prefix_counts[item["source_prefix"]] += 1
        target_family_counts[item["target_family"]] += 1

    summary = {
        "defect_count": len(enriched),
        "review_class_counts": dict(sorted(class_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "source_prefix_counts": dict(sorted(source_prefix_counts.items())),
        "target_family_counts": dict(
            sorted(target_family_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "next_recommended_action": (
            "review legacy_equivalence_overclaim_narrower first because it is "
            "the largest repeatable class, then process partial/broader and "
            "confidence overclaims"
        ),
    }
    return enriched, summary


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_fields = [
        "review_class",
        "proposed_action",
        "source_prefix",
        "target_family",
        "issue_type",
        "source_standard",
        "source_code",
        "target_standard",
        "target_code",
        "field",
        "legacy_value",
        "canonical_value",
        "classification_note",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in base_fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize legacy mapping defects from a classified parity worklist."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--csv-output", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    rows = load_defects(args.source)
    enriched, summary = summarize(rows)
    summary.update(
        {
            "source_worklist": args.source.as_posix(),
            "csv_output": args.csv_output.as_posix(),
            "json_output": args.json_output.as_posix(),
        }
    )
    if args.version:
        summary["version"] = args.version

    write_csv(args.csv_output, enriched)
    write_json(args.json_output, summary)
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
