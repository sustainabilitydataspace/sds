#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e5_api_coverage_summary_v2026-06-23.json"
)
E5_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E05-technical-code"
    / "final"
    / "e05-codigo-tecnico-modelo-v2026-06-09.md"
)
ACCEPTANCE_GATES_PATH = REPO_ROOT / "docs" / "quality" / "acceptance_gates.md"
API_MAKEFILE_PATH = REPO_ROOT / "api" / "Makefile"
API_DEV_PS1_PATH = REPO_ROOT / "api" / "scripts" / "dev.ps1"


def _format_int(value: int) -> str:
    return f"{value:,}"


def _load_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing E5 coverage summary: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("E5 coverage summary must be a JSON object")
    return payload


def _required_fragments(summary: dict[str, Any]) -> list[str]:
    coverage = summary["coverage"]
    passed_tests = int(summary["passed_tests"])
    skipped_tests = int(summary.get("skipped_tests", 0))
    covered_lines = int(coverage["covered_lines"])
    num_statements = int(coverage["num_statements"])
    missing_lines = int(coverage["missing_lines"])
    percent_covered = str(coverage["percent_covered"])
    display = str(coverage["percent_covered_display"])
    fail_under = str(summary["coverage_fail_under"])

    fragments = [
        f"{_format_int(passed_tests)} passing tests",
        f"{display}% TOTAL coverage",
        f"{percent_covered}%",
        _format_int(covered_lines),
        _format_int(num_statements),
        _format_int(missing_lines),
        f"--cov-fail-under={fail_under}",
        ">=90% E5 acceptance threshold",
        f"{summary['quality_objective']}% quality objective",
    ]
    if skipped_tests:
        fragments.append(f"{_format_int(skipped_tests)} skipped")
    return fragments


def _threshold_errors(summary: dict[str, Any]) -> list[str]:
    coverage = summary["coverage"]
    percent_covered = float(coverage["percent_covered"])
    acceptance_threshold = float(summary["acceptance_threshold"])
    quality_objective = float(str(summary["quality_objective"]).lstrip(">= >"))
    errors: list[str] = []
    if percent_covered < acceptance_threshold:
        errors.append(
            f"coverage {percent_covered} below acceptance threshold {acceptance_threshold}"
        )
    if percent_covered < quality_objective:
        errors.append(
            f"coverage {percent_covered} below quality objective {quality_objective}"
        )
    return errors


def _makefile_threshold_errors(summary: dict[str, Any]) -> list[str]:
    expected = str(summary["coverage_fail_under"])
    makefile = API_MAKEFILE_PATH.read_text(encoding="utf-8")
    dev_ps1 = API_DEV_PS1_PATH.read_text(encoding="utf-8")
    errors: list[str] = []

    make_threshold = re.search(
        r"^COVERAGE_FAIL_UNDER\s*\?=\s*([0-9.]+)", makefile, flags=re.MULTILINE
    )
    ps_threshold = re.search(r'\$CoverageFailUnder\s*=\s*"([0-9.]+)"', dev_ps1)
    if make_threshold is None or make_threshold.group(1) != expected:
        errors.append("api/Makefile coverage fail-under does not match E5 evidence")
    if ps_threshold is None or ps_threshold.group(1) != expected:
        errors.append("api/scripts/dev.ps1 coverage fail-under does not match E5 evidence")
    if "--cov-report=json:$(COVERAGE_JSON)" not in makefile:
        errors.append("api/Makefile must emit coverage JSON")
    if "--cov-report=json:$CoverageJson" not in dev_ps1:
        errors.append("api/scripts/dev.ps1 must emit coverage JSON")
    return errors


def _doc_errors(summary: dict[str, Any]) -> list[str]:
    e5_text = E5_DOC_PATH.read_text(encoding="utf-8")
    gates_text = ACCEPTANCE_GATES_PATH.read_text(encoding="utf-8")
    errors: list[str] = []

    coverage = summary["coverage"]
    passed_tests = int(summary["passed_tests"])
    skipped_tests = int(summary.get("skipped_tests", 0))
    covered_lines = int(coverage["covered_lines"])
    num_statements = int(coverage["num_statements"])
    missing_lines = int(coverage["missing_lines"])
    percent_covered = str(coverage["percent_covered"])
    fail_under = str(summary["coverage_fail_under"])
    quality_objective = str(summary["quality_objective"])

    e5_required_fragments = [
        f"{_format_int(passed_tests)} pruebas aprobadas",
        f"{_format_int(skipped_tests)} pruebas omitidas",
        percent_covered,
        _format_int(covered_lines),
        _format_int(num_statements),
        _format_int(missing_lines),
        f"--cov-fail-under={fail_under}",
        "umbral de aceptación E5 de >=90%",
        f"objetivo de calidad actual de {quality_objective}%",
    ]
    for fragment in e5_required_fragments:
        if fragment not in e5_text:
            errors.append(f"E5 deliverable missing coverage evidence: {fragment}")

    for fragment in _required_fragments(summary):
        if fragment not in gates_text:
            errors.append(f"acceptance gates missing coverage evidence: {fragment}")

    stale_fragments = [
        "1,877 passing tests",
        "98.18715762127971988479132546%",
        "17,386",
        "17,707",
        "321 missed",
        "1,711",
        "99.8372864384505%",
        "15,953",
        "15,979",
        "1,915 passing tests",
        "1,915 pruebas aprobadas",
        "97.74461783802255%",
        "17,162",
        "17,558",
        "396",
        "--cov-fail-under=95",
        ">=95% quality objective",
        "objetivo de calidad actual de >=95%",
        "2,621 passing tests",
        "2,621 pruebas aprobadas",
        "2,680 passing tests",
        "2,680 pruebas aprobadas",
        "97.51163540850652%",
        "21,161",
        "21,701",
        "540",
    ]
    for fragment in stale_fragments:
        if fragment in e5_text:
            errors.append(f"E5 deliverable still contains stale evidence: {fragment}")
        if fragment in gates_text:
            errors.append(f"acceptance gates still contain stale evidence: {fragment}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate E5 coverage evidence against deliverable text."
    )
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        summary = _load_summary(args.summary)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"E5 coverage evidence error: {exc}")
        return 2

    errors = (
        _threshold_errors(summary)
        + _makefile_threshold_errors(summary)
        + _doc_errors(summary)
    )

    coverage = summary["coverage"]
    print("E5 coverage evidence:")
    print(f"  - command: {summary['command']}")
    print(f"  - passed tests: {summary['passed_tests']}")
    print(f"  - skipped tests: {summary.get('skipped_tests', 0)}")
    print(f"  - exact coverage: {coverage['percent_covered']}%")
    print(
        "  - covered statements: "
        f"{coverage['covered_lines']} / {coverage['num_statements']}"
    )

    if errors:
        print("E5 coverage evidence drift:")
        for error in errors:
            print(f"  - {error}")
        return 1 if args.strict else 0

    print("E5 coverage evidence is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
