from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CLASSIFICATION_POLICY = {
    "code_mismatch": {
        "classification": "legacy_code_normalization",
        "note": (
            "Canonical package uses normalized atomic GRI code punctuation; "
            "legacy row should be normalized before cutover review."
        ),
        "policy": (
            "legacy_code_normalization when canonical evidence uses an atomic "
            "standard datapoint code and the legacy baseline differs by "
            "conservative code-format drift"
        ),
    },
    "confidence_mismatch": {
        "classification": "legacy_mapping_defect",
        "note": (
            "Reviewed canonical evidence carries lower confidence than the "
            "legacy full-confidence claim; keep canonical confidence and fix "
            "or retire the legacy overclaim before cutover."
        ),
        "policy": (
            "legacy_mapping_defect when canonical reviewed evidence lowers "
            "confidence versus a legacy full-confidence equivalence claim"
        ),
    },
    "extra_in_canonical": {
        "classification": "expected_semantic_improvement",
        "note": (
            "Canonical package intentionally adds a reviewed semantic pair "
            "absent from the legacy exact-key baseline."
        ),
        "policy": (
            "expected_semantic_improvement when the canonical package creates "
            "a reviewed pair absent from the legacy exact-key baseline"
        ),
    },
    "missing_in_canonical": {
        "classification": "atomizer_coverage_gap",
        "note": (
            "Legacy row still lacks a reviewed canonical assertion in the "
            "Atomizer package; this remains a coverage gap unless later "
            "evidence proves a materialization bug."
        ),
        "policy": (
            "atomizer_coverage_gap because the canonical package remains an "
            "evidence-backed partial package; reclassify only with proof of "
            "SDS materialization failure"
        ),
    },
    "relationship_mismatch": {
        "classification": "legacy_mapping_defect",
        "note": (
            "Reviewed canonical evidence preserves a more precise "
            "non-equivalent relationship than the legacy baseline; keep "
            "canonical semantics and fix or retire the legacy overclaim "
            "before cutover."
        ),
        "policy": (
            "legacy_mapping_defect when canonical non-equivalent semantics "
            "are more precise than the legacy equivalent relationship"
        ),
    },
}


def source_prefix(source_code: str) -> str:
    return source_code.split("-", 1)[0].split(".", 1)[0]


def classify_rows(
    rows: list[dict[str, str]]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not rows:
        raise ValueError("source worklist has no rows")

    issue_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    source_prefix_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    classified_rows: list[dict[str, str]] = []

    for row in rows:
        item = dict(row)
        issue_type = item.get("issue_type", "")
        issue_counts[issue_type] += 1
        rule = CLASSIFICATION_POLICY.get(issue_type)
        if rule is None:
            item["status"] = "needs_manual_classification"
            item["classification"] = "unknown"
            item["classification_note"] = (
                "No automatic classification policy exists for this issue type."
            )
        else:
            item["status"] = "classified"
            item["classification"] = str(rule["classification"])
            item["classification_note"] = str(rule["note"])

        classification_counts[item["classification"]] += 1
        source_prefix_counts[source_prefix(item.get("source_code", ""))][
            item["classification"]
        ] += 1
        classified_rows.append(item)

    summary = {
        "row_count": len(rows),
        "status_counts": dict(
            sorted(Counter(row["status"] for row in classified_rows).items())
        ),
        "issue_counts": dict(sorted(issue_counts.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
        "classification_policy": {
            issue_type: str(rule["policy"])
            for issue_type, rule in sorted(CLASSIFICATION_POLICY.items())
        },
        "source_prefix_classification_counts": {
            prefix: dict(sorted(counts.items()))
            for prefix, counts in sorted(source_prefix_counts.items())
        },
    }
    return classified_rows, summary


def read_worklist(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no CSV header")
        required = {
            "issue_type",
            "status",
            "suggested_classification",
            "source_standard",
            "source_code",
            "target_standard",
            "target_code",
            "field",
            "legacy_value",
            "canonical_value",
        }
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        return list(reader)


def write_classified(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "issue_type",
        "status",
        "suggested_classification",
        "source_standard",
        "source_code",
        "target_standard",
        "target_code",
        "field",
        "legacy_value",
        "canonical_value",
        "classification",
        "classification_note",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def cutover_decision_text(summary: dict[str, Any]) -> str:
    blockers = ["coverage gaps", "legacy overclaims"]
    if summary.get("classification_counts", {}).get("legacy_code_normalization", 0):
        blockers.append("code-normalization cleanup")
    return (
        "blocked; do not cut over /api/v1/mappings while the classified "
        f"worklist has unresolved {', '.join(blockers)}"
    )


def next_action_text(summary: dict[str, Any]) -> str:
    if summary.get("classification_counts", {}).get("legacy_code_normalization", 0):
        return (
            "fix legacy code-normalization items first, then decide whether "
            "to expand Atomizer coverage or keep the package beta-only"
        )
    return (
        "review legacy mapping defects next, then decide whether to expand "
        "Atomizer coverage or keep the package beta-only"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a canonical mapping parity worklist for cutover review. "
            "This does not change /api/v1/mappings or canonical package data."
        )
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--classified", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    rows = read_worklist(args.source)
    classified_rows, summary = classify_rows(rows)
    summary.update(
        {
            "source_worklist": args.source.as_posix(),
            "classified_worklist": args.classified.as_posix(),
            "cutover_decision": cutover_decision_text(summary),
            "next_recommended_action": next_action_text(summary),
        }
    )
    if args.version:
        summary["version"] = args.version

    write_classified(args.classified, classified_rows)
    write_summary(args.summary, summary)
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
