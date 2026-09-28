#!/usr/bin/env python3
"""Progressive VARCH gate runner with per-gate observability (VARCH-10).

Consumes ``src/semantic/gate_manifest.py`` and runs the VARCH contract gates in progressive slice
order (foundations -> write-validation -> binding -> resolver -> legacy -> API). Reports per-gate
status, and on failure surfaces WHICH gate, its slice, the contract it enforces, and the test that
failed (gate-failure observability). Exits non-zero if any gate's test suite fails.

Usage:
    python scripts/gate_varch.py --list            # print the gate registry (no run)
    python scripts/gate_varch.py                   # run all gates progressively
    python scripts/gate_varch.py --slice VARCH-4a  # run one slice's gates
    python scripts/gate_varch.py --verify-only     # only check manifest <-> source consistency

The disposable-PG DB-smoke gates are skipped automatically unless SDS_MIGRATION_TEST_DATABASE_URL
+ SDS_MIGRATION_TEST_ALLOW_RESET=true are set (the established convention); pure-core gates always
run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_API_ROOT))

from src.semantic.gate_manifest import (  # noqa: E402
    SLICE_ORDER,
    VARCH_GATES,
    gates_by_slice,
)


def _verify_manifest() -> list[str]:
    """Return a list of manifest<->source inconsistencies (empty == consistent)."""
    problems: list[str] = []
    for gate in VARCH_GATES:
        module = _API_ROOT / gate.module
        test = _API_ROOT / gate.test
        if not module.exists():
            problems.append(f"{gate.name}: module missing {gate.module}")
        if not test.exists():
            problems.append(f"{gate.name}: test missing {gate.test}")
        if not gate.verify:
            problems.append(f"{gate.name}: empty verify token (mandatory)")
            continue
        sources = ""
        for p in (module, test):
            if p.exists():
                sources += p.read_text(encoding="utf-8", errors="ignore")
        if gate.verify not in sources:
            problems.append(
                f"{gate.name}: verify token {gate.verify!r} not found in module/test source"
            )
    return problems


def _print_list() -> None:
    grouped = gates_by_slice()
    total = 0
    for slc in SLICE_ORDER:
        gates = grouped.get(slc, [])
        if not gates:
            continue
        print(f"\n{slc}  ({len(gates)} gate(s))")
        for g in gates:
            print(f"  - {g.name:<42} {g.contract}")
        total += len(gates)
    print(f"\nTotal VARCH gates: {total}")


def _run_slice(slc: str, gates: list) -> bool:
    """Run a slice's gate tests; return True on pass. Per-gate observability on failure."""
    test_paths = sorted({g.test for g in gates})
    print(f"\n=== {slc}: {len(gates)} gate(s) over {len(test_paths)} test module(s) ===")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *test_paths],
        cwd=str(_API_ROOT),
    )
    if result.returncode == 0:
        for g in gates:
            print(f"  [PASS] {g.name}")
        return True
    # gate-failure observability: name the gates + contracts in the failing slice.
    print(f"  [FAIL] {slc} gate suite failed (pytest exit {result.returncode}). Gates at risk:")
    for g in gates:
        print(f"    - {g.name}: {g.contract}  (enforced by {g.test})")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Progressive VARCH gate runner.")
    parser.add_argument("--list", action="store_true", help="print the gate registry and exit")
    parser.add_argument("--slice", help="run only this slice (e.g. VARCH-4a)")
    parser.add_argument(
        "--verify-only", action="store_true", help="only check manifest<->source consistency"
    )
    args = parser.parse_args()

    problems = _verify_manifest()
    if problems:
        print("MANIFEST INCONSISTENT:")
        for p in problems:
            print(f"  - {p}")
        return 2
    if args.verify_only:
        print(f"Manifest consistent: {len(VARCH_GATES)} gates verified against source.")
        return 0
    if args.list:
        _print_list()
        return 0

    grouped = gates_by_slice()
    order = [args.slice] if args.slice else list(SLICE_ORDER)
    failed: list[str] = []
    for slc in order:
        gates = grouped.get(slc, [])
        if not gates:
            if args.slice:
                print(f"unknown slice {slc!r}")
                return 2
            continue
        if not _run_slice(slc, gates):
            failed.append(slc)

    print("\n" + "=" * 60)
    if failed:
        print(f"VARCH GATES FAILED in slices: {', '.join(failed)}")
        return 1
    print(f"ALL VARCH GATES PASSED ({len(VARCH_GATES)} gates across {len(order)} slices).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
