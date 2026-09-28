"""VARCH-10 contract tests — progressive gate manifest + runner.

Self-verifying registry: every gate's verify token must appear in its module/test source (so the
manifest can never drift from the code), every referenced module/test path exists, every VARCH
test module is represented by at least one gate (no orphan slice), and the runner lists/verifies
cleanly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.semantic.gate_manifest import SLICE_ORDER, VARCH_GATES, gates_by_slice

_API_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_tokens_present_in_source() -> None:
    """Every verify token appears in the gate's module/test source (no drift)."""
    problems = []
    for gate in VARCH_GATES:
        module = _API_ROOT / gate.module
        test = _API_ROOT / gate.test
        assert module.exists(), f"{gate.name}: missing module {gate.module}"
        assert test.exists(), f"{gate.name}: missing test {gate.test}"
        # verify token is MANDATORY (no empty/structural escape) and must appear in source.
        assert gate.verify, f"{gate.name}: empty verify token (mandatory)"
        src = module.read_text(encoding="utf-8", errors="ignore") + test.read_text(
            encoding="utf-8", errors="ignore"
        )
        if gate.verify not in src:
            problems.append(f"{gate.name}: {gate.verify!r} not in source")
    assert not problems, problems


def test_every_slice_in_order() -> None:
    slices = {g.slice for g in VARCH_GATES}
    assert slices <= set(SLICE_ORDER), slices - set(SLICE_ORDER)
    # every ordered slice that has gates is reachable
    grouped = gates_by_slice()
    assert any(grouped[s] for s in SLICE_ORDER)


def test_every_varch_test_module_is_gated() -> None:
    """Each tests/test_varch*.py (except this meta-test) is referenced by >=1 gate."""
    gated = {g.test for g in VARCH_GATES}
    on_disk = {
        f"tests/{p.name}"
        for p in (_API_ROOT / "tests").glob("test_varch*.py")
        if p.name != "test_varch10_gate_manifest.py"
    }
    missing = on_disk - gated
    assert not missing, f"un-gated VARCH test modules: {sorted(missing)}"


def test_gate_count_is_substantial() -> None:
    assert len(VARCH_GATES) >= 60, f"expected the full gate set, got {len(VARCH_GATES)}"
    # names are unique.
    names = [g.name for g in VARCH_GATES]
    assert len(names) == len(set(names)), "duplicate gate names"


def test_runner_verify_only_and_list_exit_zero() -> None:
    for flag in ("--verify-only", "--list"):
        result = subprocess.run(
            [sys.executable, "scripts/gate_varch.py", flag],
            cwd=str(_API_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{flag}: {result.stdout}\n{result.stderr}"
