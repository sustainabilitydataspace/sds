#!/usr/bin/env python3
"""
E6 evidence package generator for the Windows repo.

This is a repo-local, Windows-friendly adaptation of the source E6
evidence workflow. It collects the imported governance/policy baseline,
runs the governance checker, and writes a local evidence bundle outside
the public deliverables tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(cmd: List[str], cwd: Path) -> Tuple[int, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def collect_artifacts(project_root: Path, artifacts: Iterable[Path]) -> List[Dict[str, str]]:
    manifest: List[Dict[str, str]] = []
    for rel_path in artifacts:
        path = project_root / rel_path
        if path.exists():
            manifest.append(
                {
                    "file": str(rel_path).replace("\\", "/"),
                    "sha256": sha256_file(path),
                    "size": str(path.stat().st_size),
                }
            )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a repo-local E6 evidence bundle")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory",
    )
    parser.add_argument(
        "--output-dir",
        default=".local_artifacts/e6_evidence",
        help="Directory where the evidence files will be written",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"E6_{timestamp}"

    artifacts = [
        Path("docs/quality/acceptance_gates.md"),
        Path("docs/policies/policy_registry.json"),
        Path("docs/policies/policy_registry_schema.json"),
        Path("docs/policies/policy_registry.md"),
        Path("docs/policies/purpose_binding_profile.md"),
        Path("docs/policies/retention_enforcement.md"),
        Path("docs/policies/data_product_terms.md"),
        Path("docs/policies/usage_control_examples.md"),
        Path("docs/policies/legal_binding_matrix.csv"),
        Path("docs/governance/trust_issuers.json"),
        Path("docs/governance/trust_issuers_schema.json"),
        Path("docs/governance/trust_issuers.md"),
        Path("docs/governance/onboarding_flow.md"),
        Path("docs/governance/operating_model_raci.md"),
        Path("docs/governance/audit_event_schema.json"),
        Path("configs/edc/policies.json"),
        Path("configs/edc/assets.json"),
        Path("configs/edc/contract-definitions.json"),
        Path("configs/edc/README.md"),
        Path("deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md"),
        Path("scripts/edc_bundle_from_register.py"),
        Path("scripts/e6_check_governance.py"),
        Path("scripts/run_e6_gate.py"),
        Path("scripts/e6_audit_to_ngsi_ld.py"),
    ]

    manifest = collect_artifacts(project_root, artifacts)

    check_cmd = [sys.executable, "scripts/e6_check_governance.py"]
    check_code, check_output = run_command(check_cmd, project_root)
    check_file = output_dir / f"{prefix}_check.txt"
    check_file.write_text(check_output, encoding="utf-8")

    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectRoot": str(project_root),
        "artifactCount": len(manifest),
        "governanceCheck": {
            "command": " ".join(check_cmd),
            "exitCode": check_code,
        },
        "artifacts": manifest,
    }

    summary_file = output_dir / f"{prefix}_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    report_lines = [
        "# E6 Evidence Bundle",
        "",
        f"- Generated: {summary['generatedAt']}",
        f"- Project root: `{project_root}`",
        f"- Artifact count: {summary['artifactCount']}",
        f"- Governance check command: `{check_cmd[0]} {' '.join(check_cmd[1:])}`",
        f"- Governance check exit code: `{check_code}`",
        "",
        "## Included files",
    ]
    for item in manifest:
        report_lines.append(f"- `{item['file']}`")
    report_lines.extend(
        [
            "",
            "## Governance check output",
            "",
            "```text",
            check_output.rstrip() or "(no output)",
            "```",
            "",
            "## Manifest",
            f"- `{summary_file.name}`",
            f"- `{check_file.name}`",
        ]
    )

    report_file = output_dir / f"{prefix}_report.md"
    report_file.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Wrote evidence report: {report_file}")
    print(f"Wrote summary: {summary_file}")
    print(f"Wrote governance check output: {check_file}")
    return 0 if check_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
