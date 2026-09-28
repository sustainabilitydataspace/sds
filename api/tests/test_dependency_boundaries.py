from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "check_dependency_boundaries.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_dependency_boundaries", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


boundary_check = _load_module()


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_required_boundary_files(repo_root: Path) -> None:
    _write_text(repo_root / "deliverables" / "README.md", "# Official deliverables\n")
    _write_text(
        repo_root / "deliverables" / "deliverables-register.csv",
        "deliverable_id,title,status,canonical_path,version,date,sha256,privacy_classification,private_source_ref\n",
    )
    _write_text(
        repo_root / "data" / "extracted" / "README.md",
        "This directory is non-canonical.\n",
    )


def _create_cli_entrypoint(repo_root: Path, body: str) -> None:
    _write_text(repo_root / "cli" / "sds_cli" / "src" / "sds_cli" / "__main__.py", body)


def test_collect_issues_accepts_clean_boundary_repo(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _write_text(repo_root / "scripts" / "clean.py", "print('ok')\n")

    issues = boundary_check.collect_issues(repo_root)

    assert issues == []


def test_collect_issues_flags_missing_cli_passthrough_target(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _create_cli_entrypoint(
        repo_root,
        "\n".join(
            [
                "from dataclasses import dataclass",
                "@dataclass(frozen=True)",
                "class PassthroughCommand:",
                "    name: str",
                "    help: str",
                "    module: str",
                "    script_path: str",
                "PASSTHROUGH_COMMANDS = {",
                "    'missing': PassthroughCommand(",
                "        'missing', 'Missing target',",
                "        'scripts.missing_target',",
                "        'scripts/missing_target.py',",
                "    ),",
                "}",
                "",
            ]
        ),
    )

    issues = boundary_check.collect_issues(repo_root)

    assert any(issue.rule == "cli_passthrough_target" for issue in issues)


def test_collect_issues_flags_unimportable_cli_passthrough_target(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _write_text(
        repo_root / "scripts" / "bad_target.py",
        "import missing_dependency_for_boundary_test\n\n"
        "def main():\n"
        "    return 0\n",
    )
    _create_cli_entrypoint(
        repo_root,
        "\n".join(
            [
                "PASSTHROUGH_COMMANDS = {",
                "    'bad': {",
                "        'module': 'scripts.bad_target',",
                "        'script_path': 'scripts/bad_target.py',",
                "    },",
                "}",
                "",
            ]
        ),
    )

    issues = boundary_check.collect_issues(repo_root)

    assert any(issue.rule == "cli_passthrough_import" for issue in issues)


def test_collect_issues_flags_dataspaces_reference(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _write_text(
        repo_root / "scripts" / "bad.py", "# dataspaces should not be referenced here\n"
    )

    issues = boundary_check.collect_issues(repo_root)

    assert any(issue.rule == "dataspaces_reference" for issue in issues)


def test_collect_issues_flags_api_runtime_offline_unit_helper_import(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _write_text(
        repo_root / "api" / "src" / "bad_runtime.py",
        "from sds_core.units import convert_unit\n",
    )

    issues = boundary_check.collect_issues(repo_root)

    assert any(issue.rule == "api_runtime_unit_helper_import" for issue in issues)


def test_collect_issues_allows_test_only_offline_unit_helper_import(tmp_path):
    repo_root = tmp_path / "repo"
    _create_required_boundary_files(repo_root)
    _write_text(
        repo_root / "api" / "tests" / "test_helper.py",
        "from sds_core.units import convert_unit\n",
    )

    issues = boundary_check.collect_issues(repo_root)

    assert issues == []
