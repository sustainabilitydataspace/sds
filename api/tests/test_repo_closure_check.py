from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "repo_closure_check.py"


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repo_closure_check = _load_script("repo_closure_check", SCRIPT_PATH)


def test_repo_closure_requires_all_late_deliverables():
    required_paths = repo_closure_check.required_paths(REPO_ROOT)

    assert (
        required_paths["e7_deliverable"].name
        == "e07-actas-conclusiones-talleres-v1-0.md"
    )
    assert (
        required_paths["e12_deliverable"].name
        == "e12-reporte-final-difusion-impacto-v1-0.md"
    )
    assert required_paths["e13_deliverable"].name == (
        "e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md"
    )


def test_repo_closure_flags_register_checksum_drift(tmp_path):
    repo_root = tmp_path / "repo"
    deliverable = repo_root / "deliverables" / "E01-demo" / "final" / "e01.md"
    deliverable.parent.mkdir(parents=True)
    deliverable.write_text("# E01\n", encoding="utf-8")
    register = repo_root / "deliverables" / "deliverables-register.csv"
    with register.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "deliverable_id",
                "title",
                "status",
                "canonical_path",
                "version",
                "date",
                "sha256",
                "privacy_classification",
                "private_source_ref",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "deliverable_id": "E01",
                "title": "Demo",
                "status": "published-local",
                "canonical_path": "deliverables/E01-demo/final/e01.md",
                "version": "V1.0",
                "date": "2026-06-18",
                "sha256": "0" * 64,
                "privacy_classification": "public",
                "private_source_ref": "SOURCE_DEMO",
            }
        )

    issues = repo_closure_check.collect_register_issues(repo_root)

    assert any("checksum mismatch" in issue for issue in issues)
