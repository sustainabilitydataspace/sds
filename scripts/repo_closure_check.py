from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DELIVERABLE_IDS = {f"E{index:02d}" for index in range(1, 14)}
TEXT_HASH_EXTENSIONS = {
    ".adoc",
    ".csv",
    ".html",
    ".json",
    ".md",
    ".rst",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def required_paths(repo_root: Path = REPO_ROOT) -> dict[str, Path]:
    return {
        "deliverables_index": repo_root / "deliverables" / "README.md",
        "deliverables_register": repo_root
        / "deliverables"
        / "deliverables-register.csv",
        "e1_deliverable": repo_root
        / "deliverables"
        / "E01-mapeo-datos-normativas"
        / "final"
        / "e01-inventario-datos-clave-variables-brutas-v2026-06-09.md",
        "e2_deliverable": repo_root
        / "deliverables"
        / "E02-interoperabilidad-crosswalks"
        / "final"
        / "e02-informe-interoperabilidad-correspondencias-v2026-06-09.md",
        "e3_deliverable": repo_root
        / "deliverables"
        / "E03-modelo-ngsi-ld"
        / "final"
        / "e03-modelo-comun-ngsi-ld-paquete-semantico-v2026-06-09.md",
        "e4_deliverable": repo_root
        / "deliverables"
        / "E04-api-prototype"
        / "final"
        / "e04-prototipo-api-transformacion-datos-v2026-06-09.md",
        "e5_deliverable": repo_root
        / "deliverables"
        / "E05-technical-code"
        / "final"
        / "e05-codigo-tecnico-modelo-v2026-06-09.md",
        "e6_deliverable": repo_root
        / "deliverables"
        / "E06-governance"
        / "final"
        / "e06-gobernanza-politica-datos-v2026-06-09.md",
        "e7_deliverable": repo_root
        / "deliverables"
        / "E07-workshops"
        / "final"
        / "e07-actas-conclusiones-talleres-v1-0.md",
        "e8_deliverable": repo_root
        / "deliverables"
        / "E08-use-cases"
        / "final"
        / "e08-casos-de-uso-y-prioridades-sectoriales.md",
        "e9_deliverable": repo_root
        / "deliverables"
        / "E09-adjustments"
        / "final"
        / "e09-adjustments-report-v2026-02-03.md",
        "e10_deliverable": repo_root
        / "deliverables"
        / "E10-communications-plan"
        / "final"
        / "e10-communications-plan-v2026-02-03.md",
        "e11_deliverable": repo_root
        / "deliverables"
        / "E11-website"
        / "final"
        / "e11-website-publication-report.md",
        "e11_official_url": repo_root
        / "deliverables"
        / "E11-website"
        / "evidence"
        / "official-url.md",
        "e12_deliverable": repo_root
        / "deliverables"
        / "E12-communications-impact"
        / "final"
        / "e12-reporte-final-difusion-impacto-v1-0.md",
        "e13_deliverable": repo_root
        / "deliverables"
        / "E13-roadmap-scalability"
        / "final"
        / "e13-informe-final-recomendaciones-futuro-espacio-datos-v1-0.md",
        "traceability_matrix": repo_root
        / "deliverables"
        / "evidence-public"
        / "pdf-first-traceability-matrix-sds-v2026-02-02.md",
        "deliverables_check_script": repo_root / "scripts" / "check_deliverables.py",
        "deliverables_ci_workflow": repo_root
        / ".github"
        / "workflows"
        / "deliverables-check.yml",
        "api_root": repo_root / "api",
        "api_makefile": repo_root / "api" / "Makefile",
        "api_ci_workflow": repo_root / ".github" / "workflows" / "api-ci.yml",
        "root_makefile": repo_root / "Makefile",
        "windows_wrapper": repo_root / "scripts" / "gates.ps1",
        "atomizer_sync_script": repo_root / "scripts" / "sync_atomizer_exports.py",
        "atomizer_boundary_script": repo_root
        / "scripts"
        / "check_dependency_boundaries.py",
        "extracted_readme": repo_root / "data" / "extracted" / "README.md",
        "e1_check_script": repo_root / "scripts" / "e1_check_inventory.py",
        "e2_gate_script": repo_root / "scripts" / "e2_gate.py",
        "e2_detection_script": repo_root / "scripts" / "e2_gate_detection.py",
        "e3_export_script": repo_root / "scripts" / "e3_export_ngsi_ld.py",
        "e3_check_script": repo_root / "scripts" / "e3_check_model.py",
        "semantics_bundle_script": repo_root / "scripts" / "semantics_bundle_build.py",
        "e4_gate_script": repo_root / "scripts" / "e4_gate.py",
        "e5_check_script": repo_root / "scripts" / "e5_check_coverage_evidence.py",
        "semantics_gate_script": repo_root / "scripts" / "run_semantics_gate.py",
        "e6_check_script": repo_root / "scripts" / "e6_check_governance.py",
        "e6_gate_script": repo_root / "scripts" / "run_e6_gate.py",
        "e6_evidence_script": repo_root / "scripts" / "e6_generate_evidence.py",
        "e7_gate_script": repo_root / "scripts" / "e7_gate.py",
        "e1_dimension_annex": repo_root
        / "deliverables"
        / "E01-mapeo-datos-normativas"
        / "evidence"
        / "dimension-expanded-value-coordinate-annex-v2026-06-05.md",
        "e1_dimension_ledger": repo_root
        / "deliverables"
        / "E01-mapeo-datos-normativas"
        / "evidence"
        / "dimension-expanded-value-coordinate-calculation-v2026-06-05.csv",
        "e2_completeness_snapshot": repo_root
        / "data"
        / "extracted"
        / "analysis"
        / "e2_completeness_by_topic.csv",
        "e2_detection_snapshot": repo_root
        / "data"
        / "extracted"
        / "analysis"
        / "e2_detection_by_topic.csv",
        "e2_atomized_crosswalk_source": repo_root
        / "data"
        / "extracted"
        / "analysis"
        / "e2_crosswalks_esrs_gri_full_atomized.csv",
        "e4_performance_report": repo_root
        / "data"
        / "extracted"
        / "analysis"
        / "e4_performance_report.txt",
        "e4_performance_summary": repo_root
        / "data"
        / "extracted"
        / "analysis"
        / "e4_performance_summary.csv",
    }


REQUIRED_PATHS = required_paths()

BANNED_MARKERS = [
    "[TBD]",
    "placeholder",
    "Deployment status: TBD",
]


def sha256_file(path: Path) -> str:
    content = path.read_bytes()
    if path.suffix.lower() in TEXT_HASH_EXTENSIONS:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def collect_register_issues(repo_root: Path = REPO_ROOT) -> list[str]:
    register_path = repo_root / "deliverables" / "deliverables-register.csv"
    if not register_path.exists():
        return ["missing deliverables/deliverables-register.csv"]

    with register_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows_by_id = {
        row.get("deliverable_id", "").strip(): row
        for row in rows
        if row.get("deliverable_id", "").strip() in REQUIRED_DELIVERABLE_IDS
    }
    issues: list[str] = []
    for deliverable_id in sorted(REQUIRED_DELIVERABLE_IDS):
        row = rows_by_id.get(deliverable_id)
        if row is None:
            issues.append(f"missing register row for {deliverable_id}")
            continue
        relative_path = row.get("canonical_path", "").strip()
        if not relative_path:
            issues.append(f"{deliverable_id}: empty canonical_path")
            continue
        canonical_path = repo_root / relative_path
        if not canonical_path.exists():
            issues.append(f"{deliverable_id}: missing canonical_path {relative_path}")
            continue
        expected_checksum = row.get("sha256", "").strip().lower()
        actual_checksum = sha256_file(canonical_path)
        if expected_checksum != actual_checksum:
            issues.append(f"{deliverable_id}: checksum mismatch for {relative_path}")
    return issues


def main() -> int:
    ok = True
    paths = required_paths()

    print("Repo closure check")
    print("==================")

    for name, path in paths.items():
        if path.exists():
            print(f"[OK] {name}: {path.relative_to(REPO_ROOT)}")
        else:
            print(f"[MISSING] {name}: {path.relative_to(REPO_ROOT)}")
            ok = False

    for name in ("e11_deliverable", "e11_official_url"):
        page = paths[name]
        text = page.read_text(encoding="utf-8")
        for marker in BANNED_MARKERS:
            if marker in text:
                print(
                    f"[FAIL] marker '{marker}' still present in {page.relative_to(REPO_ROOT)}"
                )
                ok = False

    for issue in collect_register_issues():
        print(f"[FAIL] {issue}")
        ok = False

    print("")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
