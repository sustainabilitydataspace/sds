from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_privileged_surfaces.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_privileged_surfaces", CHECKER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_static_security_gate_covers_privileged_surfaces() -> None:
    makefile = (REPO_ROOT / "api" / "Makefile").read_text(encoding="utf-8")
    static_security = makefile.split("static-security:", 1)[1].split("\n\n", 1)[0]

    assert "bandit -r src/ scripts/ ../scripts/" in static_security
    assert "../scripts/check_privileged_surfaces.py --root .." in static_security


def test_privileged_surface_rules_reject_high_risk_constructs() -> None:
    checker = _load_checker()

    assert checker.scan_text(Path("deploy.sh"), "curl https://example.test/x | sh\n")
    assert checker.scan_text(Path("deploy.ps1"), "Invoke-Expression $payload\n")
    assert checker.scan_text(
        Path("compose.yml"), "services:\n  api:\n    privileged: true\n"
    )
    assert checker.scan_text(Path("workflow.yml"), "permissions: write-all\n")


def test_privileged_surface_rules_allow_bounded_constructs() -> None:
    checker = _load_checker()

    assert (
        checker.scan_text(
            Path("deploy.sh"),
            "curl --fail --output package.tgz https://example.test/x\n",
        )
        == []
    )
    assert (
        checker.scan_text(
            Path("compose.yml"), "services:\n  api:\n    read_only: true\n"
        )
        == []
    )
