"""Offline regression checks for the public API development bootstrap."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.docs_only


def test_dev_requirements_require_wheels_and_include_runtime_pins():
    lines = {
        line.split("#", 1)[0].strip()
        for line in (API_ROOT / "requirements-dev.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    }
    assert "--only-binary=:all:" in lines
    assert not any(line.startswith("--no-binary") for line in lines)
    assert "-r requirements.txt" in lines
    assert "setuptools==83.0.0" in lines


@pytest.mark.parametrize("local_venv", [None, "bin/python", "Scripts/python.exe"])
@pytest.mark.parametrize("pip_override", [None, "custom-pip"])
@pytest.mark.parametrize("makelevel", ["0", "2"])
def test_install_dev_uses_selected_python_unless_pip_is_explicit(
    tmp_path, local_venv, pip_override, makelevel
):
    # Exercise Make's variable precedence without installing into any environment.
    (tmp_path / "Makefile").write_bytes((API_ROOT / "Makefile").read_bytes())
    if local_venv:
        executable = tmp_path / ".venv" / local_venv
        executable.parent.mkdir(parents=True)
        executable.touch()
    env = os.environ.copy()
    for name in ("PIP", "PYTHON", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "OS"):
        env.pop(name, None)
    # Recursive gates inherit MAKELEVEL and otherwise add directory banners.
    env["MAKELEVEL"] = makelevel
    args = [
        "make",
        "--no-print-directory",
        "--dry-run",
        "install-dev",
        "PYTHON=/external/venv/python",
    ]
    if local_venv == "Scripts/python.exe":
        args.append("OS=Windows_NT")
    if pip_override:
        args.append(f"PIP={pip_override}")
    result = subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, check=True
    )
    expected_pip = pip_override or "/external/venv/python -m pip"
    assert result.stdout.splitlines()[-1] == (
        f"{expected_pip} install -r requirements-dev.txt"
    )


@pytest.mark.parametrize("selection", ["environment", "command-line"])
def test_demo_install_never_overrides_a_selected_python_with_local_venv(
    tmp_path, selection
):
    """The demo qualification must honor its explicitly selected interpreter."""
    (tmp_path / "Makefile").write_bytes((API_ROOT / "Makefile").read_bytes())
    local_venv = tmp_path / ".venv" / "bin" / "python"
    local_venv.parent.mkdir(parents=True)
    local_venv.touch()

    env = os.environ.copy()
    for name in ("PYTHON", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "OS"):
        env.pop(name, None)
    args = ["make", "--no-print-directory", "--dry-run", "demo-install"]
    if selection == "environment":
        env["PYTHON"] = "/external/venv/bin/python"
    else:
        args.append("PYTHON=/external/venv/bin/python")

    result = subprocess.run(
        args, cwd=tmp_path, env=env, capture_output=True, text=True, check=True
    )
    command = shlex.split(result.stdout)
    assert command[:3] == [
        "VALUE_REVISION_API_ENABLED=true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED=true",
        "VALUE_REVISION_PRIMARY_READ_PATH=revision",
    ]
    assert command[3:] == [
        "/external/venv/bin/python",
        "scripts/demo_runtime.py",
        "install",
    ]


@pytest.mark.parametrize(
    ("override", "selected_python", "windows_venv"),
    [
        ("PYTHON=/selected/venv/bin/python", "/selected/venv/bin/python", False),
        ("ROOT_PY=/selected/root/bin/python", "/selected/root/bin/python", False),
        (None, "api/.venv/Scripts/python.exe", True),
    ],
)
def test_root_deliverables_check_honors_caller_selected_python(
    tmp_path, override, selected_python, windows_venv
):
    """Use caller selection on Linux and retain the established Windows default."""
    repo_root = API_ROOT.parent
    (tmp_path / "Makefile").write_bytes((repo_root / "Makefile").read_bytes())
    if windows_venv:
        executable = tmp_path / "api" / ".venv" / "Scripts" / "python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()

    env = os.environ.copy()
    for name in ("PYTHON", "ROOT_PY", "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
        env.pop(name, None)
    args = ["make", "--no-print-directory", "--dry-run", "deliverables-check"]
    if override:
        args.append(override)

    result = subprocess.run(
        args,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert shlex.split(result.stdout) == [
        selected_python,
        "scripts/check_deliverables.py",
    ]
