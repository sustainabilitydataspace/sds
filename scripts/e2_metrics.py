#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SOURCE_FILENAME = "e2_crosswalks_esrs_gri_full_atomized.csv"
DEFAULT_COMPLETENESS_FILENAME = "e2_completeness_by_topic.csv"
DEFAULT_DETECTION_FILENAME = "e2_detection_by_topic.csv"
SCOPED_TOPICS = ("energy", "ghg", "water")
REQUIRED_SOURCE_COLUMNS = {
    "interop_topic",
    "ESRS_Detected",
    "GRI_Detected",
    "WithBoth",
}


@dataclass(frozen=True)
class TopicMetrics:
    topic: str
    total: int
    with_esrs: int
    with_gri: int
    with_both: int
    with_any: int

    @property
    def coverage_pct(self) -> int:
        return _percentage(self.with_both, self.total)

    @property
    def detection_pct(self) -> int:
        return _percentage(self.with_any, self.total)


def default_analysis_dir() -> Path:
    return (
        Path(
            os.environ.get(
                "DATA_EXTRACTED",
                os.path.join(os.environ.get("DATA_ROOT", "data"), "extracted"),
            )
        )
        / "analysis"
    )


def default_source_path() -> Path:
    return default_analysis_dir() / DEFAULT_SOURCE_FILENAME


def default_completeness_path() -> Path:
    return default_analysis_dir() / DEFAULT_COMPLETENESS_FILENAME


def default_detection_path() -> Path:
    return default_analysis_dir() / DEFAULT_DETECTION_FILENAME


def derive_topic_metrics(
    source_path: Path,
    *,
    topics: tuple[str, ...] = SCOPED_TOPICS,
) -> list[TopicMetrics]:
    rows = _load_source_rows(source_path)
    aggregates = {
        topic: {
            "total": 0,
            "with_esrs": 0,
            "with_gri": 0,
            "with_both": 0,
            "with_any": 0,
        }
        for topic in topics
    }

    for row_index, row in enumerate(rows, start=2):
        topic = (row.get("interop_topic") or "").strip().lower()
        if topic not in aggregates:
            continue

        esrs = _parse_bool(row.get("ESRS_Detected"), row_index, "ESRS_Detected")
        gri = _parse_bool(row.get("GRI_Detected"), row_index, "GRI_Detected")
        both = _parse_bool(row.get("WithBoth"), row_index, "WithBoth")
        if both != (esrs and gri):
            raise ValueError(
                f"row {row_index}: WithBoth={both!r} does not match "
                "ESRS_Detected and GRI_Detected"
            )

        aggregate = aggregates[topic]
        aggregate["total"] += 1
        aggregate["with_esrs"] += int(esrs)
        aggregate["with_gri"] += int(gri)
        aggregate["with_both"] += int(both)
        aggregate["with_any"] += int(esrs or gri)

    metrics = [
        TopicMetrics(
            topic=topic,
            total=values["total"],
            with_esrs=values["with_esrs"],
            with_gri=values["with_gri"],
            with_both=values["with_both"],
            with_any=values["with_any"],
        )
        for topic, values in aggregates.items()
    ]
    missing = [metric.topic for metric in metrics if metric.total == 0]
    if missing:
        raise ValueError(f"missing scoped E2 topics in source: {', '.join(missing)}")
    return metrics


def completeness_rows(metrics: list[TopicMetrics]) -> list[dict[str, int | str]]:
    return [
        {
            "topic": metric.topic,
            "total": metric.total,
            "with_esrs": metric.with_esrs,
            "with_gri": metric.with_gri,
            "with_both": metric.with_both,
            "coverage_pct": metric.coverage_pct,
        }
        for metric in metrics
    ]


def detection_rows(metrics: list[TopicMetrics]) -> list[dict[str, int | str]]:
    return [
        {
            "topic": metric.topic,
            "total": metric.total,
            "with_any": metric.with_any,
            "with_esrs": metric.with_esrs,
            "with_esrs_family": 0,
            "with_gri": metric.with_gri,
            "detection_pct": metric.detection_pct,
        }
        for metric in metrics
    ]


def read_summary_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"E2 summary CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def compare_summary(
    *,
    expected_rows: list[dict[str, int | str]],
    actual_rows: list[dict[str, str]],
    checked_columns: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    expected_by_topic = {str(row["topic"]): row for row in expected_rows}
    actual_by_topic = {(row.get("topic") or "").strip(): row for row in actual_rows}

    missing = sorted(set(expected_by_topic) - set(actual_by_topic))
    extra = sorted(set(actual_by_topic) - set(expected_by_topic))
    if missing:
        errors.append(f"summary missing topics: {', '.join(missing)}")
    if extra:
        errors.append(f"summary has unexpected topics: {', '.join(extra)}")

    for topic, expected in expected_by_topic.items():
        actual = actual_by_topic.get(topic)
        if actual is None:
            continue
        for column in checked_columns:
            expected_value = str(expected[column])
            actual_value = (actual.get(column) or "").strip()
            if actual_value != expected_value:
                errors.append(
                    f"{topic} {column}: summary={actual_value!r}, "
                    f"source-derived={expected_value!r}"
                )
    return errors


def _load_source_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"E2 source crosswalk CSV not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_SOURCE_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing required source columns: {sorted(missing)}")
        return list(reader)


def _parse_bool(value: str | None, row_index: int, column: str) -> bool:
    normalized = (value or "").strip().upper()
    if normalized == "TRUE":
        return True
    if normalized == "FALSE":
        return False
    raise ValueError(f"row {row_index}: {column} must be TRUE or FALSE")


def _percentage(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return int(round((numerator / denominator) * 100))
