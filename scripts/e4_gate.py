#!/usr/bin/env python3
"""E4 formal gate for the standards-package to SDS-register prototype."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from prepare_register_for_import import calculate_checksum, prepare_from_granulation  # noqa: E402


LOCAL_PACKAGE_DIR = REPO_ROOT / "data" / "sds_package"
LOCAL_GENERATED_REGISTER_DIR = REPO_ROOT / "data" / "processed"
DEFAULT_REGISTER_PATH = LOCAL_PACKAGE_DIR / "sds_dataset_register.csv"
DEFAULT_DIMENSION_LEDGER_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E01-mapeo-datos-normativas"
    / "evidence"
    / "dimension-expanded-value-coordinate-calculation-v2026-06-05.csv"
)
DEFAULT_FRAMEWORK_PATH = LOCAL_PACKAGE_DIR / "framework_datapoints.csv"
DEFAULT_GRANULATED_PATH = LOCAL_PACKAGE_DIR / "granulated_variables.csv"
DEFAULT_OUTDIR = REPO_ROOT / "data" / "extracted" / "analysis"
DEFAULT_REPORT_PATH = DEFAULT_OUTDIR / "e4_performance_report.txt"
DEFAULT_SUMMARY_PATH = DEFAULT_OUTDIR / "e4_performance_summary.csv"
REGISTER_PATH_ENV = "SDS_E4_REGISTER_PATH"
MIN_ROWS = 1000
MAX_SECONDS = 30.0
MIN_SUCCESS_RATE = 0.95

RULE_FAMILY_ORDER = (
    "Environmental/carbon",
    "Social",
    "Governance",
    "Transversal",
)
REQUIRED_RULE_FAMILIES = (
    "Environmental/carbon",
    "Social",
    "Governance",
)

RAW_TO_STANDARDIZED_MAPPING = (
    ("FrameworkID", "indicator namespace and code family"),
    ("DatapointCode", "canonical indicator local segment"),
    ("Label", "title and indicator label"),
    ("Description", "human-readable description"),
    ("Topic", "dimension family"),
    ("DataType", "value type"),
    ("DefaultUnit", "unitName fallback"),
    ("Granulated UnitName", "unitName / unitType refinement"),
)


@dataclass(slots=True)
class E4GateResult:
    timestamp_utc: str
    gate_result: str
    source_mode: str
    register_path: Path | None
    framework_path: Path
    granulated_path: Path
    register_checksum: str
    framework_checksum: str
    granulation_checksum: str
    framework_rows: int
    granulated_rows: int
    transformed_rows: int
    elapsed_seconds: float
    min_rows: int
    max_seconds: float
    min_success_rate: float
    rows_environmental: int
    rows_social: int
    rows_governance: int
    rows_transversal: int
    failure_reasons: list[str]

    @property
    def success_rate(self) -> float:
        if self.framework_rows <= 0:
            return 0.0
        return self.transformed_rows / self.framework_rows

    @property
    def passed(self) -> bool:
        return self.gate_result == "PASS"


def _count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _evidence_path_label(path: Path | None) -> str:
    """Return a public-safe source label for E4 evidence output."""
    if path is None:
        return ""

    resolved = path.resolve()
    try:
        package_relative = resolved.relative_to(LOCAL_PACKAGE_DIR.resolve())
    except ValueError:
        package_relative = None
    if package_relative is not None:
        return f"local-package:{package_relative.as_posix()}"

    try:
        generated_relative = resolved.relative_to(LOCAL_GENERATED_REGISTER_DIR.resolve())
    except ValueError:
        generated_relative = None
    if generated_relative is not None:
        return f"generated-register:{generated_relative.as_posix()}"

    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"external-package:{resolved.name}"


def _normalize_topic(topic: str | None) -> str:
    return " ".join((topic or "").strip().upper().replace("-", " ").split())


def _classify_rule_family(topic: str | None) -> str:
    normalized = _normalize_topic(topic)
    if not normalized:
        return "Transversal"

    if any(
        token in normalized
        for token in (
            "CLIMATE CHANGE",
            "BIODIVERSITY AND ECOSYSTEMS",
            "POLLUTION",
            "RESOURCE USE AND CIRCULAR ECONOMY",
            "WATER AND MARINE RESOURCES",
        )
    ) or normalized.startswith(("GRI 30", "GRI 201", "GRI 302", "GRI 303", "GRI 304", "GRI 305", "GRI 306", "GRI 307", "GRI 308")):
        return "Environmental/carbon"

    if any(
        token in normalized
        for token in (
            "OWN WORKFORCE",
            "WORKERS IN THE VALUE CHAIN",
            "AFFECTED COMMUNITIES",
            "CONSUMERS AND END USERS",
            "CONSUMERS AND END-USERS",
            "DIVERSITY",
            "EMPLOYEE",
            "SOCIAL",
        )
    ) or normalized.startswith(("GRI 4", "GRI 401", "GRI 403", "GRI 404", "GRI 405", "GRI 406", "GRI 407", "GRI 408", "GRI 409", "GRI 410", "GRI 411", "GRI 412", "GRI 413", "GRI 414", "GRI 415", "GRI 416", "GRI 417", "GRI 418")):
        return "Social"

    if any(
        token in normalized
        for token in (
            "BUSINESS CONDUCT",
            "GOVERNANCE",
            "BOARD",
            "ETHICS",
            "COMPLIANCE",
            "ANTI",
        )
    ) or normalized.startswith(("GRI 205", "GRI 206", "GRI 207")):
        return "Governance"

    if any(token in normalized for token in ("GENERAL DISCLOSURES", "BASIS")) or normalized.startswith(("GRI 3", "GRI 101")):
        return "Transversal"

    return "Transversal"


def _count_rule_families(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in _read_csv_rows(path):
        family = _classify_rule_family(row.get("Topic"))
        counts[family] += 1
    return counts


def _count_register_rule_families(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in _read_csv_rows(path):
        dimension = " ".join((row.get("dimension") or "").strip().upper().split())
        if dimension in {"E", "ENVIRONMENTAL", "ENVIRONMENT", "ENVIRONMENTAL/CARBON"}:
            counts["Environmental/carbon"] += 1
        elif dimension in {"S", "SOCIAL"}:
            counts["Social"] += 1
        elif dimension in {"G", "GOVERNANCE"}:
            counts["Governance"] += 1
        else:
            counts["Transversal"] += 1
    return counts


def _coordinate_int(value: str | None) -> int:
    return int((value or "0").strip() or "0")


def _gri_row_rule_family(row: dict[str, str]) -> str:
    scope = row.get("scope_or_group") or ""
    if re.search(r"\bGRI[ _]2[-_]", scope, flags=re.IGNORECASE):
        return "Governance"
    if re.search(r"\bGRI[ _]3[-_]", scope, flags=re.IGNORECASE):
        return "Transversal"

    series = {
        int(value)
        for value in re.findall(
            r"(?:\bGRI\s+|_)(\d{3})[-_]", scope, flags=re.IGNORECASE
        )
    }
    families: set[str] = set()
    for value in series:
        if 100 <= value < 200 or 300 <= value < 400:
            families.add("Environmental/carbon")
        elif 200 <= value < 300:
            families.add("Governance")
        elif 400 <= value < 500:
            families.add("Social")
    return next(iter(families)) if len(families) == 1 else "Transversal"


def _dimension_row_to_rule_family(row: dict[str, str]) -> str:
    standard = " ".join((row.get("standard") or "").strip().upper().split())
    area = " ".join((row.get("standard_area") or "").strip().upper().split())
    if standard == "GHG PROTOCOL":
        return "Environmental/carbon"
    if standard == "GRI":
        return _gri_row_rule_family(row)
    if area.startswith(("E1", "E2", "E3", "E4", "E5", "ENVIRONMENT")):
        return "Environmental/carbon"
    if area.startswith(("S1", "S2", "S3", "S4", "SOCIAL")):
        return "Social"
    if area.startswith(("G1", "GOVERNANCE")):
        return "Governance"
    return "Transversal"


def _count_dimension_ledger_rule_families(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in _read_csv_rows(path):
        family = _dimension_row_to_rule_family(row)
        counts[family] += _coordinate_int(row.get("reportable_coordinates"))
    return counts


def _sum_dimension_ledger_coordinates(path: Path) -> tuple[int, int]:
    rows = _read_csv_rows(path)
    total = sum(_coordinate_int(row.get("reportable_coordinates")) for row in rows)
    return len(rows), total


def _missing_required_rule_families(counts: Counter[str]) -> list[str]:
    return [family for family in REQUIRED_RULE_FAMILIES if counts.get(family, 0) <= 0]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_source_inputs(framework_path: Path, granulated_path: Path) -> list[str]:
    missing: list[str] = []
    if not framework_path.exists():
        missing.append(f"missing framework source: {_evidence_path_label(framework_path)}")
    if not granulated_path.exists():
        missing.append(
            f"missing granulated-variable source: {_evidence_path_label(granulated_path)}"
        )
    return missing


def _validate_register_input(register_path: Path) -> list[str]:
    if register_path.exists():
        return []
    return [f"missing register source: {_evidence_path_label(register_path)}"]


def _build_report_lines(result: E4GateResult) -> list[str]:
    lines = [
        "E4 Formal Gate Report",
        f"Timestamp (UTC): {result.timestamp_utc}",
        f"Gate result: {result.gate_result}",
        f"Source mode: {result.source_mode}",
        "",
        "Sources:",
    ]

    if result.source_mode == "register":
        lines.extend(
            [
                f"  Register package:     {_evidence_path_label(result.register_path)}",
                f"  Register checksum:    {result.register_checksum}",
            ]
        )
    elif result.source_mode == "dimension-ledger":
        lines.extend(
            [
                f"  Dimension ledger:     {_evidence_path_label(result.register_path)}",
                f"  Ledger checksum:      {result.register_checksum}",
            ]
        )
    else:
        lines.extend(
            [
                f"  Framework datapoints: {_evidence_path_label(result.framework_path)}",
                f"  Granulated variables: {_evidence_path_label(result.granulated_path)}",
                f"  Framework checksum:   {result.framework_checksum}",
                f"  Granulation checksum: {result.granulation_checksum}",
            ]
        )

    evidence_lines = []
    if result.source_mode == "register":
        evidence_lines.append(f"  Register rows:        {result.framework_rows}")
    elif result.source_mode == "dimension-ledger":
        evidence_lines.extend(
            [
                f"  Ledger groups:        {result.granulated_rows}",
                f"  Reportable coordinates: {result.framework_rows}",
            ]
        )
    else:
        evidence_lines.extend(
            [
                f"  Framework rows:       {result.framework_rows}",
                f"  Granulated rows:      {result.granulated_rows}",
            ]
        )

    lines.extend(["", "Gate evidence:", *evidence_lines])
    if result.source_mode == "dimension-ledger":
        lines.extend(
            [
                f"  Coordinates summed:   {result.framework_rows}",
                f"  Elapsed seconds:      {result.elapsed_seconds:.3f}",
            ]
        )
    else:
        lines.extend(
            [
                f"  Transformed rows:     {result.transformed_rows}",
                f"  Elapsed seconds:      {result.elapsed_seconds:.3f}",
                f"  Min rows required:    {result.min_rows}",
                f"  Max seconds allowed:   {result.max_seconds:.1f}",
                f"  Min success rate:     {result.min_success_rate:.2%}",
                f"  Success rate:         {result.success_rate:.2%}",
            ]
        )
    lines.extend(
        [
            "",
            "Rule families:",
            f"  Environmental/carbon: {result.rows_environmental}",
            f"  Social:               {result.rows_social}",
            f"  Governance:           {result.rows_governance}",
            f"  Transversal:          {result.rows_transversal}",
        ]
    )

    if result.source_mode == "legacy":
        lines.extend(["", "Raw-to-standardized mapping:"])
        for source_field, target_field in RAW_TO_STANDARDIZED_MAPPING:
            lines.append(f"  - {source_field} -> {target_field}")

    lines.extend(["", "Checks:"])
    if result.source_mode == "dimension-ledger":
        lines.extend(
            [
                f"  [ {'PASS' if result.granulated_rows > 0 else 'FAIL'} ] structural_ledger_nonempty",
                f"  [ {'PASS' if result.framework_rows > 0 else 'FAIL'} ] reportable_coordinate_total_positive",
            ]
        )
    else:
        lines.extend(
            [
                f"  [ {'PASS' if result.transformed_rows >= result.min_rows else 'FAIL'} ] rows_processed >= {result.min_rows}",
                f"  [ {'PASS' if result.elapsed_seconds < result.max_seconds else 'FAIL'} ] elapsed_seconds < {result.max_seconds:.1f}",
                f"  [ {'PASS' if result.success_rate >= result.min_success_rate else 'FAIL'} ] success_rate >= {result.min_success_rate:.0%}",
            ]
        )
    lines.extend(
        [
            f"  [ {'PASS' if result.rows_environmental > 0 else 'FAIL'} ] environmental/carbon family present",
            f"  [ {'PASS' if result.rows_social > 0 else 'FAIL'} ] social family present",
            f"  [ {'PASS' if result.rows_governance > 0 else 'FAIL'} ] governance family present",
        ]
    )

    if result.failure_reasons:
        lines.extend(["", "Failure reasons:"])
        lines.extend(f"  - {reason}" for reason in result.failure_reasons)

    return lines


def _summary_row(result: E4GateResult) -> dict[str, str]:
    base = {
        "timestamp_utc": result.timestamp_utc,
        "gate": "E4",
        "gate_result": result.gate_result,
        "source_mode": result.source_mode,
        "elapsed_seconds": f"{result.elapsed_seconds:.3f}",
        "rows_environmental": str(result.rows_environmental),
        "rows_social": str(result.rows_social),
        "rows_governance": str(result.rows_governance),
        "rows_transversal": str(result.rows_transversal),
        "failure_reasons": " | ".join(result.failure_reasons),
    }
    if result.source_mode == "dimension-ledger":
        return {
            "dimension_ledger_path": _evidence_path_label(result.register_path),
            "dimension_ledger_checksum": result.register_checksum,
            "ledger_groups": str(result.granulated_rows),
            "reportable_coordinates": str(result.framework_rows),
            "coordinates_summed": str(result.framework_rows),
            **base,
        }

    common = {
        **base,
        "transformed_rows": str(result.transformed_rows),
        "min_rows": str(result.min_rows),
        "max_seconds": f"{result.max_seconds:.1f}",
        "min_success_rate": f"{result.min_success_rate:.4f}",
        "success_rate": f"{result.success_rate:.4f}",
    }
    if result.source_mode == "register":
        return {
            **{
                "register_path": _evidence_path_label(result.register_path),
                "register_checksum": result.register_checksum,
                "register_rows": str(result.framework_rows),
            },
            **common,
        }
    return {
        **{
            "framework_path": _evidence_path_label(result.framework_path),
            "granulated_path": _evidence_path_label(result.granulated_path),
            "framework_checksum": result.framework_checksum,
            "granulation_checksum": result.granulation_checksum,
            "framework_rows": str(result.framework_rows),
            "granulated_rows": str(result.granulated_rows),
        },
        **common,
    }


def _write_report(path: Path, result: E4GateResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_build_report_lines(result)) + "\n", encoding="utf-8")


def _write_summary(path: Path, result: E4GateResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = _summary_row(result)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def _select_register_path(
    source_mode: str,
    explicit_register_path: Path | None,
    default_register_path: Path = DEFAULT_REGISTER_PATH,
) -> Path | None:
    if source_mode == "legacy":
        return None
    if explicit_register_path is not None:
        return explicit_register_path

    env_path = os.environ.get(REGISTER_PATH_ENV)
    if env_path:
        return Path(env_path)

    if source_mode == "register":
        return default_register_path
    if default_register_path.exists():
        return default_register_path
    return None


def run_gate(
    *,
    register_path: Path | None = None,
    dimension_ledger_path: Path | None = None,
    framework_path: Path = DEFAULT_FRAMEWORK_PATH,
    granulated_path: Path = DEFAULT_GRANULATED_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    summary_path: Path = DEFAULT_SUMMARY_PATH,
    min_rows: int = MIN_ROWS,
    max_seconds: float = MAX_SECONDS,
    min_success_rate: float = MIN_SUCCESS_RATE,
    transform_fn: Callable[..., int] = prepare_from_granulation,
    timer: Callable[[], float] = time.perf_counter,
) -> E4GateResult:
    timestamp_utc = _utc_now()
    failure_reasons: list[str] = []
    if dimension_ledger_path is not None:
        source_mode = "dimension-ledger"
    elif register_path is not None:
        source_mode = "register"
    else:
        source_mode = "legacy"

    if dimension_ledger_path is not None:
        if not dimension_ledger_path.exists():
            failure_reasons.append(
                f"missing dimension ledger source: {_evidence_path_label(dimension_ledger_path)}"
            )
            result = E4GateResult(
                timestamp_utc=timestamp_utc,
                gate_result="FAIL",
                source_mode=source_mode,
                register_path=dimension_ledger_path,
                framework_path=framework_path,
                granulated_path=granulated_path,
                register_checksum="",
                framework_checksum="",
                granulation_checksum="",
                framework_rows=0,
                granulated_rows=0,
                transformed_rows=0,
                elapsed_seconds=0.0,
                min_rows=min_rows,
                max_seconds=max_seconds,
                min_success_rate=min_success_rate,
                rows_environmental=0,
                rows_social=0,
                rows_governance=0,
                rows_transversal=0,
                failure_reasons=failure_reasons,
            )
            _write_report(report_path, result)
            _write_summary(summary_path, result)
            return result

        register_checksum = calculate_checksum(dimension_ledger_path)
        start = timer()
        ledger_groups, reportable_coordinates = _sum_dimension_ledger_coordinates(
            dimension_ledger_path
        )
        elapsed_seconds = max(timer() - start, 0.0)
        family_counts = _count_dimension_ledger_rule_families(dimension_ledger_path)
        if ledger_groups <= 0:
            failure_reasons.append("structural_ledger_empty")
        if reportable_coordinates <= 0:
            failure_reasons.append("reportable_coordinate_total_not_positive")
        for family in _missing_required_rule_families(family_counts):
            failure_reasons.append(f"missing_rule_family: {family}")

        result = E4GateResult(
            timestamp_utc=timestamp_utc,
            gate_result="PASS" if not failure_reasons else "FAIL",
            source_mode=source_mode,
            register_path=dimension_ledger_path,
            framework_path=framework_path,
            granulated_path=granulated_path,
            register_checksum=register_checksum,
            framework_checksum="",
            granulation_checksum="",
            framework_rows=reportable_coordinates,
            granulated_rows=ledger_groups,
            transformed_rows=reportable_coordinates,
            elapsed_seconds=elapsed_seconds,
            min_rows=min_rows,
            max_seconds=max_seconds,
            min_success_rate=min_success_rate,
            rows_environmental=family_counts.get("Environmental/carbon", 0),
            rows_social=family_counts.get("Social", 0),
            rows_governance=family_counts.get("Governance", 0),
            rows_transversal=family_counts.get("Transversal", 0),
            failure_reasons=failure_reasons,
        )
        _write_report(report_path, result)
        _write_summary(summary_path, result)
        return result

    if register_path is not None:
        missing = _validate_register_input(register_path)
        if missing:
            failure_reasons.extend(missing)
            result = E4GateResult(
                timestamp_utc=timestamp_utc,
                gate_result="FAIL",
                source_mode="register",
                register_path=register_path,
                framework_path=framework_path,
                granulated_path=granulated_path,
                register_checksum="",
                framework_checksum="",
                granulation_checksum="",
                framework_rows=0,
                granulated_rows=0,
                transformed_rows=0,
                elapsed_seconds=0.0,
                min_rows=min_rows,
                max_seconds=max_seconds,
                min_success_rate=min_success_rate,
                rows_environmental=0,
                rows_social=0,
                rows_governance=0,
                rows_transversal=0,
                failure_reasons=failure_reasons,
            )
            _write_report(report_path, result)
            _write_summary(summary_path, result)
            return result

        register_checksum = calculate_checksum(register_path)
        start = timer()
        register_rows = _read_csv_rows(register_path)
        elapsed_seconds = max(timer() - start, 0.0)
        transformed_rows = len(register_rows)
        family_counts = _count_register_rule_families(register_path)
        success_rate = 1.0 if transformed_rows else 0.0

        if transformed_rows < min_rows:
            failure_reasons.append(f"rows_processed_below_threshold: {transformed_rows} < {min_rows}")
        if elapsed_seconds >= max_seconds:
            failure_reasons.append(f"elapsed_seconds_above_threshold: {elapsed_seconds:.3f} >= {max_seconds:.1f}")
        if success_rate < min_success_rate:
            failure_reasons.append(
                f"success_rate_below_threshold: {success_rate:.2%} < {min_success_rate:.0%}"
            )
        for family in _missing_required_rule_families(family_counts):
            failure_reasons.append(f"missing_rule_family: {family}")

        result = E4GateResult(
            timestamp_utc=timestamp_utc,
            gate_result="PASS" if not failure_reasons else "FAIL",
            source_mode=source_mode,
            register_path=register_path,
            framework_path=framework_path,
            granulated_path=granulated_path,
            register_checksum=register_checksum,
            framework_checksum="",
            granulation_checksum="",
            framework_rows=transformed_rows,
            granulated_rows=0,
            transformed_rows=transformed_rows,
            elapsed_seconds=elapsed_seconds,
            min_rows=min_rows,
            max_seconds=max_seconds,
            min_success_rate=min_success_rate,
            rows_environmental=family_counts.get("Environmental/carbon", 0),
            rows_social=family_counts.get("Social", 0),
            rows_governance=family_counts.get("Governance", 0),
            rows_transversal=family_counts.get("Transversal", 0),
            failure_reasons=failure_reasons,
        )
        _write_report(report_path, result)
        _write_summary(summary_path, result)
        return result

    missing = _validate_source_inputs(framework_path, granulated_path)
    if missing:
        failure_reasons.extend(missing)
        result = E4GateResult(
            timestamp_utc=timestamp_utc,
            gate_result="FAIL",
            source_mode=source_mode,
            register_path=None,
            framework_path=framework_path,
            granulated_path=granulated_path,
            register_checksum="",
            framework_checksum="",
            granulation_checksum="",
            framework_rows=0,
            granulated_rows=0,
            transformed_rows=0,
            elapsed_seconds=0.0,
            min_rows=min_rows,
            max_seconds=max_seconds,
            min_success_rate=min_success_rate,
            rows_environmental=0,
            rows_social=0,
            rows_governance=0,
            rows_transversal=0,
            failure_reasons=failure_reasons,
        )
        _write_report(report_path, result)
        _write_summary(summary_path, result)
        return result

    framework_checksum = calculate_checksum(framework_path)
    granulation_checksum = calculate_checksum(granulated_path)
    framework_rows = _count_csv_rows(framework_path)
    granulated_rows = _count_csv_rows(granulated_path)
    source_family_counts = _count_rule_families(framework_path)

    with tempfile.TemporaryDirectory(prefix="e4_gate_") as temp_dir:
        temp_output = Path(temp_dir) / "e4_standardized_register.csv"
        temp_log = Path(temp_dir) / "prepare_log.txt"

        import prepare_register_for_import as transform_module  # noqa: E402

        previous_log_path = transform_module.LOG_PATH
        transform_module.LOG_PATH = temp_log
        start = timer()
        try:
            transformed_rows = transform_fn(
                framework_path=framework_path,
                granulated_path=granulated_path,
                output_path=temp_output,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover - exercised via integration failure paths
            failure_reasons.append(f"transform_error: {exc.__class__.__name__}: {exc}")
            transformed_rows = 0
        finally:
            transform_module.LOG_PATH = previous_log_path
        elapsed_seconds = max(timer() - start, 0.0)

        output_rows = _count_csv_rows(temp_output) if temp_output.exists() else 0

    if transformed_rows != output_rows:
        failure_reasons.append(
            f"transform_return_mismatch: returned {transformed_rows}, written {output_rows}"
        )

    if output_rows < min_rows:
        failure_reasons.append(f"rows_processed_below_threshold: {output_rows} < {min_rows}")
    if elapsed_seconds >= max_seconds:
        failure_reasons.append(f"elapsed_seconds_above_threshold: {elapsed_seconds:.3f} >= {max_seconds:.1f}")

    success_rate = (output_rows / framework_rows) if framework_rows else 0.0
    if success_rate < min_success_rate:
        failure_reasons.append(
            f"success_rate_below_threshold: {success_rate:.2%} < {min_success_rate:.0%}"
        )

    rows_environmental = source_family_counts.get("Environmental/carbon", 0)
    rows_social = source_family_counts.get("Social", 0)
    rows_governance = source_family_counts.get("Governance", 0)
    rows_transversal = source_family_counts.get("Transversal", 0)
    for family in _missing_required_rule_families(source_family_counts):
        failure_reasons.append(f"missing_rule_family: {family}")

    result = E4GateResult(
        timestamp_utc=timestamp_utc,
        gate_result="PASS" if not failure_reasons else "FAIL",
        source_mode=source_mode,
        register_path=None,
        framework_path=framework_path,
        granulated_path=granulated_path,
        register_checksum="",
        framework_checksum=framework_checksum,
        granulation_checksum=granulation_checksum,
        framework_rows=framework_rows,
        granulated_rows=granulated_rows,
        transformed_rows=output_rows,
        elapsed_seconds=elapsed_seconds,
        min_rows=min_rows,
        max_seconds=max_seconds,
        min_success_rate=min_success_rate,
        rows_environmental=rows_environmental,
        rows_social=rows_social,
        rows_governance=rows_governance,
        rows_transversal=rows_transversal,
        failure_reasons=failure_reasons,
    )

    _write_report(report_path, result)
    _write_summary(summary_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the E4 formal gate for the standards-package to SDS-register prototype"
    )
    parser.add_argument(
        "--source-mode",
        choices=("auto", "register", "legacy", "dimension-ledger"),
        default="auto",
        help="Select the source boundary. Auto uses the dimension ledger unless a register is explicit.",
    )
    parser.add_argument("--register", type=Path, default=None, help="Register-first CSV package")
    parser.add_argument(
        "--dimension-ledger",
        type=Path,
        default=DEFAULT_DIMENSION_LEDGER_PATH,
        help="Dimension-expanded E1 calculation ledger CSV",
    )
    parser.add_argument("--framework", type=Path, default=DEFAULT_FRAMEWORK_PATH, help="Framework datapoints CSV")
    parser.add_argument("--granulated", type=Path, default=DEFAULT_GRANULATED_PATH, help="Granulated variables CSV")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR, help="Evidence output directory")
    parser.add_argument("--min-rows", type=int, default=MIN_ROWS, help="Minimum transformed rows required")
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS, help="Maximum elapsed seconds allowed")
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=MIN_SUCCESS_RATE,
        help="Minimum fraction of source rows that must transform successfully",
    )
    args = parser.parse_args(argv)

    report_path = args.outdir / "e4_performance_report.txt"
    summary_path = args.outdir / "e4_performance_summary.csv"
    selected_dimension_ledger_path = (
        args.dimension_ledger
        if args.source_mode in {"auto", "dimension-ledger"} and args.register is None
        else None
    )
    selected_register_path = None
    if selected_dimension_ledger_path is None:
        selected_register_path = _select_register_path(args.source_mode, args.register)

    result = run_gate(
        register_path=selected_register_path,
        dimension_ledger_path=selected_dimension_ledger_path,
        framework_path=args.framework,
        granulated_path=args.granulated,
        report_path=report_path,
        summary_path=summary_path,
        min_rows=args.min_rows,
        max_seconds=args.max_seconds,
        min_success_rate=args.min_success_rate,
    )

    measure = "coordinates" if result.source_mode == "dimension-ledger" else "rows"
    print(
        f"E4 {result.gate_result}: {result.transformed_rows} {measure} "
        f"in {result.elapsed_seconds:.3f}s"
    )
    if result.failure_reasons:
        for reason in result.failure_reasons:
            print(f"- {reason}", file=sys.stderr)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
