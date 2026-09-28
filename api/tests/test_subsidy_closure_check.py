from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "subsidy_closure_check.py"
STATUS_SURFACE_PATH = (
    REPO_ROOT
    / "deliverables"
    / "evidence-public"
    / "dossier-closure-status-sds-v2026-09-18.md"
)


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


subsidy_closure_check = _load_script("subsidy_closure_check", SCRIPT_PATH)


def _fixture_copy(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    status = root / "deliverables" / "evidence-public" / STATUS_SURFACE_PATH.name
    register = root / "deliverables" / "deliverables-register.csv"
    status.parent.mkdir(parents=True)
    shutil.copyfile(STATUS_SURFACE_PATH, status)
    shutil.copyfile(REPO_ROOT / "deliverables" / "deliverables-register.csv", register)
    return root, status


def _replace_status(path: Path, payload: dict) -> None:
    original = path.read_text(encoding="utf-8")
    replacement = (
        "<!-- subsidy-closure-status:v2 -->\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n<!-- /subsidy-closure-status:v2 -->"
    )
    updated, count = subsidy_closure_check.STATUS_BLOCK_PATTERN.subn(
        lambda match: replacement, original, count=1
    )
    assert count == 1
    path.write_text(updated, encoding="utf-8")


def test_owner_accepted_closure_is_explicit_and_covers_every_requirement():
    status = subsidy_closure_check.load_status_surface(STATUS_SURFACE_PATH)
    assert status["schema_version"] == 2
    assert status["status_as_of"] == "2026-09-18"
    assert status["closure_decision"] == {
        "authority": "project_owner",
        "decision": "accepted_complete",
        "scope": "E01-E13 and R1-R23",
    }
    assert status["repo_publication_closure"] == "pass"
    assert status["full_subsidy_dossier_closure"] == "achieved"
    assert [item["id"] for item in status["work_packages"]] == [
        "PT1",
        "PT2",
        "PT3",
        "PT4",
        "PT5",
        "PT6",
    ]
    assert [item["id"] for item in status["requirements"]] == [
        f"R{index}" for index in range(1, 24)
    ]
    assert all(item["closure_state"] == "closed" for item in status["work_packages"])
    assert all(item["closure_state"] == "closed" for item in status["requirements"])
    requirements = {item["id"]: item for item in status["requirements"]}
    for rid, did in (("R3", "E02"), ("R20", "E10"), ("R21", "E11")):
        assert requirements[rid]["deliverable_id"] == did
    assert status["residuals"] == []
    assert subsidy_closure_check.collect_issues(REPO_ROOT) == []


def test_cli_reports_current_closed_status():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "repo publication closure: PASS" in result.stdout
    assert "full subsidy/dossier closure: CLOSED (achieved)" in result.stdout


def test_unknown_requirement_deliverable_is_rejected(tmp_path):
    root, path = _fixture_copy(tmp_path)
    status = subsidy_closure_check.load_status_surface(path)
    next(item for item in status["requirements"] if item["id"] == "R21")[
        "deliverable_id"
    ] = "E99"
    _replace_status(path, status)
    assert "R21: unknown deliverable E99" in subsidy_closure_check.collect_issues(root)


def test_unlisted_residual_and_owner_authority_drift_are_rejected(tmp_path):
    root, path = _fixture_copy(tmp_path)
    status = subsidy_closure_check.load_status_surface(path)
    status["residuals"].append({"deliverable_id": "E12", "requirement_ids": ["R22"]})
    status["closure_decision"]["decision"] = "not_accepted"
    _replace_status(path, status)
    issues = subsidy_closure_check.collect_issues(root)
    assert "residuals must be empty after accepted complete closure" in issues
    assert "closure_decision must record project-owner accepted completion" in issues
