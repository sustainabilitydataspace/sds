"""Exercise Make's audit wiring without network access or package resolution."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docs_only
AUDITS = [
    ("dependency-audit", "json", "pip-audit-requirements.json"),
    ("dependency-audit-dev", "json", "pip-audit-requirements-dev.json"),
    ("dependency-sbom", "cyclonedx-json", "runtime-sbom.cdx.json"),
]


def dry_run(target, *, root=False):
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-n",
            target,
            "PYTHON=/selected/venv/bin/python",
        ],
        cwd=REPO_ROOT if root else REPO_ROOT / "api",
        check=True,
        capture_output=True,
        text=True,
    )
    return [shlex.split(line) for line in result.stdout.splitlines() if line]


@pytest.mark.parametrize("target,output_format,filename", AUDITS)
def test_audit_uses_selected_environment_and_fails_closed(
    target, output_format, filename
):
    commands = dry_run(target)
    audits = [command for command in commands if "pip_audit" in command]
    # Exact argv rejects requirement resolution, dependency omissions, ignored
    # vulnerabilities, alternate interpreters, and shell failure suppression.
    assert audits == [
        [
            "/selected/venv/bin/python",
            "-m",
            "pip_audit",
            "--strict",
            "-f",
            output_format,
            "-o",
            f"artifacts/security/{filename}",
        ]
    ]


def test_security_scan_keeps_all_audits_bandit_and_license_gate():
    commands = dry_run("security-scan")
    assert [command for command in commands if "pip_audit" in command] == [
        command
        for target, _, _ in AUDITS
        for command in dry_run(target)
        if "pip_audit" in command
    ]
    bandit = next(command for command in commands if "bandit" in command)
    assert bandit[:3] == ["/selected/venv/bin/python", "-m", "bandit"]
    assert bandit[bandit.index("--severity-level") + 1] == "medium"
    assert bandit[bandit.index("--confidence-level") + 1] == "medium"
    assert commands[-1] == [
        "/selected/venv/bin/python",
        "scripts/export_dependency_license_inventory.py",
        "--sbom",
        "artifacts/security/runtime-sbom.cdx.json",
        "--output",
        "artifacts/security/runtime-license-inventory.json",
    ]


def test_local_ci_installs_dev_in_selected_environment_before_auditing():
    commands = dry_run("api-ci-local", root=True)
    install = [
        "/selected/venv/bin/python",
        "-m",
        "pip",
        "install",
        "-r",
        "requirements-dev.txt",
    ]
    install_index = commands.index(install)
    audit_indexes = [i for i, command in enumerate(commands) if "pip_audit" in command]
    assert len(audit_indexes) == 3
    assert all(install_index < i for i in audit_indexes)
    pytest_index = next(i for i, command in enumerate(commands) if "pytest" in command)
    assert max(audit_indexes) < pytest_index


@pytest.mark.parametrize("target,output_format,filename", AUDITS)
def test_make_propagates_audit_failure(tmp_path, target, output_format, filename):
    (tmp_path / "Makefile").write_text(
        (REPO_ROOT / "api" / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    stub = tmp_path / "python_stub.py"
    stub.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:3] == ['-m', 'pip_audit']:\n"
        "    Path('audit-argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "    sys.exit(7)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["make", target, f"PYTHON={sys.executable} {stub}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Error 7" in result.stderr
    assert json.loads((tmp_path / "audit-argv.json").read_text()) == [
        "-m",
        "pip_audit",
        "--strict",
        "-f",
        output_format,
        "-o",
        f"artifacts/security/{filename}",
    ]
