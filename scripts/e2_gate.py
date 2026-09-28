#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from e2_metrics import (
    compare_summary,
    completeness_rows,
    default_completeness_path,
    default_source_path,
    derive_topic_metrics,
    read_summary_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate E2 completeness by topic.")
    parser.add_argument(
        "--source-csv",
        type=Path,
        default=None,
        help="Path to the full E2 crosswalk source CSV.",
    )
    parser.add_argument(
        "--csv",
        "--summary-csv",
        dest="summary_csv",
        type=Path,
        default=None,
        help="Path to e2_completeness_by_topic.csv.",
    )
    parser.add_argument(
        "--threshold", type=int, default=int(os.environ.get("E2_THRESHOLD", "75"))
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any topic is below threshold",
    )
    args = parser.parse_args()

    source_path = args.source_csv or default_source_path()
    summary_path = args.summary_csv or default_completeness_path()
    try:
        metrics = derive_topic_metrics(source_path)
        expected_rows = completeness_rows(metrics)
        summary_rows = read_summary_rows(summary_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2

    drift_errors = compare_summary(
        expected_rows=expected_rows,
        actual_rows=summary_rows,
        checked_columns=("total", "with_esrs", "with_gri", "with_both", "coverage_pct"),
    )

    below: list[tuple[str, int]] = []
    print("E2 completeness derived from source crosswalk:")
    print(f"  source: {source_path}")
    print(f"  summary: {summary_path}")
    for metric in metrics:
        print(
            f"  - {metric.topic}: {metric.with_both} / {metric.total} "
            f"({metric.coverage_pct}%)"
        )
        if metric.coverage_pct < args.threshold:
            below.append((metric.topic, metric.coverage_pct))

    if drift_errors:
        print("Summary CSV drift:")
        for error in drift_errors:
            print(f"  - {error}")
        return 1

    if below:
        print(f"Below threshold ({args.threshold}%):")
        for topic, coverage in below:
            print(f"  - {topic}: {coverage}%")
        return 1 if args.strict else 0

    print("All topics meet threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
