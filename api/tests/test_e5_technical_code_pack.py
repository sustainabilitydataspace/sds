from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_GATES_PATH = REPO_ROOT / "docs" / "quality" / "acceptance_gates.md"
API_MAKEFILE_PATH = REPO_ROOT / "api" / "Makefile"
API_DEV_PS1_PATH = REPO_ROOT / "api" / "scripts" / "dev.ps1"
E5_DOC_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E05-technical-code"
    / "final"
    / "e05-codigo-tecnico-modelo-v2026-06-09.md"
)
E5_COVERAGE_SUMMARY_PATH = (
    REPO_ROOT
    / "data"
    / "extracted"
    / "analysis"
    / "e5_api_coverage_summary_v2026-06-23.json"
)


def _coverage_summary() -> dict:
    return json.loads(E5_COVERAGE_SUMMARY_PATH.read_text(encoding="utf-8"))


def _format_int(value: int) -> str:
    return f"{value:,}"


def test_e5_doc_exists_and_has_required_sections():
    assert E5_DOC_PATH.exists(), f"Missing E5 deliverable: {E5_DOC_PATH}"

    text = E5_DOC_PATH.read_text(encoding="utf-8")

    assert text.startswith("# E5"), "E5 deliverable must start with an E5 heading."
    assert "| Versión | V1.0 |" in text
    assert "## Control de validación de cierre requerido para E5" in text
    assert "## Anclajes del código técnico" in text
    assert "## Prueba funcional" in text
    assert "## Superficie de verificación" in text
    assert "## Capacidades SDS adicionales más allá del alcance mínimo de E5" in text
    assert "## Estado de cumplimiento" in text
    assert "## Referencias" in text


def test_e5_doc_references_core_runtime_and_validation_evidence():
    text = E5_DOC_PATH.read_text(encoding="utf-8")
    coverage_summary = _coverage_summary()
    coverage = coverage_summary["coverage"]
    current_coverage_refs = [
        f"{_format_int(coverage_summary['passed_tests'])} pruebas aprobadas",
        f"{_format_int(coverage_summary['skipped_tests'])} pruebas omitidas",
        f"{coverage['percent_covered']}%",
        _format_int(coverage["covered_lines"]),
        _format_int(coverage["num_statements"]),
        _format_int(coverage["missing_lines"]),
        f"--cov-fail-under={coverage_summary['coverage_fail_under']}",
    ]
    current_acceptance_refs = [
        f"{_format_int(coverage_summary['passed_tests'])} passing tests",
        f"{_format_int(coverage_summary['skipped_tests'])} skipped tests",
        f"{coverage['percent_covered']}%",
        _format_int(coverage["covered_lines"]),
        _format_int(coverage["num_statements"]),
        _format_int(coverage["missing_lines"]),
        f"--cov-fail-under={coverage_summary['coverage_fail_under']}",
    ]

    required_refs = [
        "api/src/api/main.py",
        "api/src/services/standard_versioning_import.py",
        "api/src/calculation/conversion/",
        "api/src/services/value_resolution.py",
        "api/src/services/runtime_readiness.py",
        "api/src/api/routers/interoperability.py",
        "api/src/services/value_revision_store.py",
        "make service-coverage",
        "make api-ci-local",
        "--cov-fail-under=97.01",
        "umbral de aceptación E5 de >=90%",
        "objetivo de calidad actual de >97%",
        "La aceptación formal del control de validación E5 está cerrada",
        "no afirma una cobertura del 100%",
        "DB-first",
        "resolución de valores",
        "Punto de conexión de preparación de interoperabilidad",
    ] + current_coverage_refs
    for ref in required_refs:
        assert ref in text, f"E5 must reference: {ref}"

    acceptance_gates = ACCEPTANCE_GATES_PATH.read_text(encoding="utf-8")
    stale_coverage_refs = [
        "1,877 passing tests",
        "98.18715762127971988479132546%",
        "17,386",
        "17,707",
        "321 missed",
        "1,711",
        "99.8372864384505%",
        "15,953",
        "15,979",
        "1,874",
        "remaining 26 lines",
        "1,915 passing tests",
        "97.74461783802255%",
        "17,162",
        "17,558",
        "396 missed",
        "2,607 passing tests",
        "97.0167834747326%",
        "21,041",
        "647 missed",
        "--cov-fail-under=95",
        ">=95% quality objective",
    ]
    for stale_ref in stale_coverage_refs:
        assert stale_ref not in text, f"E5 coverage evidence is stale: {stale_ref}"
        assert (
            stale_ref not in acceptance_gates
        ), f"Acceptance-gate coverage evidence is stale: {stale_ref}"

    for current_ref in current_coverage_refs:
        assert current_ref in text, f"E5 missing current coverage ref: {current_ref}"
    for current_ref in current_acceptance_refs:
        assert (
            current_ref in acceptance_gates
        ), f"Acceptance gates missing current coverage ref: {current_ref}"

    api_makefile = API_MAKEFILE_PATH.read_text(encoding="utf-8")
    api_dev_ps1 = API_DEV_PS1_PATH.read_text(encoding="utf-8")
    make_threshold = re.search(
        r"^COVERAGE_FAIL_UNDER\s*\?=\s*([0-9.]+)",
        api_makefile,
        flags=re.MULTILINE,
    )
    ps_threshold = re.search(r'\$CoverageFailUnder\s*=\s*"([0-9.]+)"', api_dev_ps1)
    assert make_threshold is not None
    assert ps_threshold is not None
    assert make_threshold.group(1) == ps_threshold.group(1) == "97.01"
    assert "--cov-fail-under=$(COVERAGE_FAIL_UNDER)" in api_makefile
    assert "--cov-fail-under=$CoverageFailUnder" in api_dev_ps1
    assert "--cov-report=json:$(COVERAGE_JSON)" in api_makefile
    assert "--cov-report=json:$CoverageJson" in api_dev_ps1

    assert re.search(
        r"cargar.*transformar.*exportar", text, flags=re.IGNORECASE | re.DOTALL
    ), "E5 must explicitly describe the load/transform/export capability."


def test_e5_doc_public_boundary_and_citations_are_clean():
    text = E5_DOC_PATH.read_text(encoding="utf-8")

    forbidden_patterns = [
        r"\bAtomizer\b",
        r"\batomizer\b",
        r"\bD:\\",
        r"OneDrive",
        r"workspace",
        r"repo-local",
        r"historical",
        r"development-history",
        r"Truth note",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text), f"E5 public deliverable leaks: {pattern}"

    required_citations = [
        "[@ETSI_NGSI_LD_CIM_009]",
        "[@W3C_JSON_LD_1_1]",
        "[@JSON_SCHEMA_2020_12]",
        "[@W3C_SHACL_2017]",
        "[@SEMIC_DCAT_AP]",
    ]
    for citation in required_citations:
        assert citation in text, f"E5 must include citation marker: {citation}"
