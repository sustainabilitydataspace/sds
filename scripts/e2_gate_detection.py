#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from e2_metrics import (
    compare_summary,
    default_detection_path,
    default_source_path,
    derive_topic_metrics,
    detection_rows,
    read_summary_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate E2 detection coverage by topic.")
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
        help="Path to e2_detection_by_topic.csv.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=int(os.environ.get("E2_DETECTION_THRESHOLD", "90")),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any topic is below threshold",
    )
    args = parser.parse_args()

    source_path = args.source_csv or default_source_path()
    summary_path = args.summary_csv or default_detection_path()
    try:
        metrics = derive_topic_metrics(source_path)
        expected_rows = detection_rows(metrics)
        summary_rows = read_summary_rows(summary_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2

    drift_errors = compare_summary(
        expected_rows=expected_rows,
        actual_rows=summary_rows,
        checked_columns=(
            "total",
            "with_any",
            "with_esrs",
            "with_esrs_family",
            "with_gri",
            "detection_pct",
        ),
    )

    below: list[tuple[str, int]] = []
    print("E2 detection coverage derived from source crosswalk:")
    print(f"  source: {source_path}")
    print(f"  summary: {summary_path}")
    for metric in metrics:
        print(
            f"  - {metric.topic}: {metric.with_any} / {metric.total} "
            f"({metric.detection_pct}%)"
        )
        if metric.detection_pct < args.threshold:
            below.append((metric.topic, metric.detection_pct))

    if drift_errors:
        print("Summary CSV drift:")
        for error in drift_errors:
            print(f"  - {error}")
        return 1

    if below:
        print(f"Below threshold ({args.threshold}%):")
        for topic, detection in below:
            print(f"  - {topic}: {detection}%")
        return 1 if args.strict else 0

    print("All topics meet threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
