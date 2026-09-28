from __future__ import annotations

import importlib.util
import re
import sys
import zipfile
from pathlib import Path

from tests.legacy_guided_tokens import LEGACY_DOC

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_DELIVERABLES_PATH = REPO_ROOT / "scripts" / "check_deliverables.py"
E7_GATE_PATH = REPO_ROOT / "scripts" / "e7_gate.py"


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_deliverables = _load_script("check_deliverables", CHECK_DELIVERABLES_PATH)
e7_gate = _load_script("e7_gate", E7_GATE_PATH)

CURRENT_OPERATOR_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "api" / "docs" / "backup_restore.md",
    REPO_ROOT / "api" / "docs" / "configuration.md",
    REPO_ROOT / "api" / "docs" / "deployment.md",
]
E8_USE_CASES_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E08-use-cases"
    / "final"
    / "e08-casos-de-uso-y-prioridades-sectoriales.md"
)
E11_REPORT_PATH = (
    REPO_ROOT
    / "deliverables"
    / "E11-website"
    / "final"
    / "e11-website-publication-report.md"
)
E11_URL_PATH = (
    REPO_ROOT / "deliverables" / "E11-website" / "evidence" / "official-url.md"
)
TRACEABILITY_MATRIX_PATH = (
    REPO_ROOT
    / "deliverables"
    / "evidence-public"
    / "pdf-first-traceability-matrix-sds-v2026-02-02.md"
)
ACCEPTANCE_GATES_PATH = REPO_ROOT / "docs" / "quality" / "acceptance_gates.md"
LOCAL_OPERATOR_MARKERS = (
    ".local_artifacts",
    "workspace/",
    "workspace\\",
    "runbook/",
    "runbook\\",
    "operator-drills",
)
FENCED_CODE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fenced_code_blocks(text: str) -> list[str]:
    return FENCED_CODE_RE.findall(text)


def test_acceptance_gate_make_command_parser_handles_root_and_scoped_targets():
    text = (
        "- `make e7-gate` validates E7.\n"
        "- Run `make -C api generate-projection` followed by `make -C api gate-w2`.\n"
    )

    commands = check_deliverables.find_documented_make_commands(text)

    assert [command.display for command in commands] == [
        "make e7-gate",
        "make -C api generate-projection",
        "make -C api gate-w2",
    ]


def test_acceptance_gate_make_target_validator_flags_missing_targets(tmp_path):
    repo_root = tmp_path / "repo"
    doc_path = repo_root / "docs" / "quality" / "acceptance_gates.md"
    _write_text(repo_root / "Makefile", "e7-gate:\n\t@echo ok\n")
    _write_text(repo_root / "api" / "Makefile", "gate-w2:\n\t@echo ok\n")
    _write_text(
        doc_path,
        "\n".join(
            [
                "- `make e7-gate`",
                "- `make -C api gate-w2`",
                "- `make missing-gate`",
            ]
        ),
    )

    issues = check_deliverables.validate_documented_make_targets(doc_path, repo_root)

    assert len(issues) == 1
    assert "references make missing-gate" in issues[0]
    assert "does not define target 'missing-gate'" in issues[0]


def test_deliverable_checksum_is_stable_across_crlf_checkout(tmp_path):
    path = tmp_path / "deliverable.md"
    path.write_bytes(b"# Title\n\nLine one.\nLine two.\n")
    expected = check_deliverables.sha256_file(path)

    path.write_bytes(b"# Title\r\n\r\nLine one.\r\nLine two.\r\n")

    assert check_deliverables.sha256_file(path) == expected


def test_current_acceptance_gate_make_targets_resolve():
    assert check_deliverables.validate_documented_make_targets() == []


def test_acceptance_gates_document_is_in_public_privacy_scan_scope():
    assert (
        check_deliverables.scan_public_markdown(
            check_deliverables.ACCEPTANCE_GATES_PATH
        )
        == []
    )


def test_quality_markdown_documents_are_in_public_privacy_scan_scope(tmp_path):
    repo_root = tmp_path / "repo"
    public_doc = repo_root / "docs" / "quality" / "sample-quality-note.md"
    _write_text(public_doc, "This public note points to workspace/private.csv.\n")

    paths = list(check_deliverables.iter_public_markdown_scan_paths(repo_root))

    assert public_doc in paths
    issues = []
    for path in paths:
        issues.extend(
            check_deliverables.scan_public_markdown(path, repo_root=repo_root)
        )
    assert any("workspace/" in issue for issue in issues)


def test_public_privacy_scan_includes_root_docs_and_machine_evidence(tmp_path):
    repo_root = tmp_path / "repo"
    root_readme = repo_root / "README.md"
    changelog = repo_root / "CHANGELOG.md"
    evidence_csv = repo_root / "deliverables" / "E01" / "evidence" / "sample.csv"
    evidence_json = repo_root / "deliverables" / "E01" / "evidence" / "sample.json"
    _write_text(root_readme, "# Public README\n")
    _write_text(changelog, "# Changelog\n")
    _write_text(evidence_csv, "id,contact\n1,person@example.com\n")
    _write_text(evidence_json, '{"source": "workspace/private.json"}\n')

    paths = set(check_deliverables.iter_public_markdown_scan_paths(repo_root))

    assert {root_readme, changelog, evidence_csv, evidence_json} <= paths
    issues = []
    for path in paths:
        issues.extend(
            check_deliverables.scan_public_markdown(path, repo_root=repo_root)
        )
    assert any("email address" in issue for issue in issues)
    assert any("workspace/" in issue for issue in issues)


def test_public_privacy_scan_flags_unlisted_video_access_identifier(tmp_path):
    repo_root = tmp_path / "repo"
    public_doc = repo_root / "deliverables" / "E07" / "final" / "minutes.md"
    _write_text(public_doc, "https://vimeo.com/123456789/abcdef1234\n")

    issues = check_deliverables.scan_public_markdown(public_doc, repo_root=repo_root)

    assert any("private URL marker" in issue for issue in issues)


def test_public_docx_privacy_scan_detects_authors_and_local_paths(tmp_path):
    package = tmp_path / "deliverables" / "E01" / "final" / "report.docx"
    package.parent.mkdir(parents=True)
    with zipfile.ZipFile(package, "w") as docx:
        docx.writestr(
            "docProps/core.xml",
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:creator>Private Author</dc:creator></cp:coreProperties>",
        )
        docx.writestr(
            "docProps/custom.xml",
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
            "<property><lpwstr>D:/Private/Workspace/source.docx</lpwstr></property></Properties>",
        )

    issues = check_deliverables.scan_public_docx(package, repo_root=tmp_path)

    assert any("author metadata" in issue for issue in issues)
    assert any("private path marker" in issue for issue in issues)


def test_public_docx_artifacts_are_in_privacy_scan_scope(tmp_path):
    package = tmp_path / "deliverables" / "E01" / "final" / "report.docx"
    package.parent.mkdir(parents=True)
    package.touch()

    assert list(check_deliverables.iter_public_docx_scan_paths(tmp_path)) == [package]


def test_public_markdown_scan_flags_stale_e11_404_without_current_200(tmp_path):
    repo_root = tmp_path / "repo"
    stale_doc = repo_root / "deliverables" / "evidence-public" / "traceability.md"
    _write_text(
        stale_doc,
        "\n".join(
            [
                "# Traceability",
                "",
                "This reflects the current public repository state.",
                "",
                "| Deliverable | External evidence |",
                "|---|---|",
                (
                    "| `E11` | `https://sustainabilitydataspace.com/` returned "
                    "HTTP 404 on 2026-06-01 |"
                ),
            ]
        ),
    )

    issues = check_deliverables.scan_public_markdown(stale_doc, repo_root=repo_root)

    assert any("stale E11 availability evidence" in issue for issue in issues)


def test_e7_gate_accepts_current_sanitized_published_boundary():
    assert e7_gate.collect_issues() == []


def test_e7_gate_flags_published_status_without_required_evidence(tmp_path):
    repo_root = tmp_path / "repo"
    _write_text(
        repo_root / "deliverables" / "deliverables-register.csv",
        "\n".join(
            [
                (
                    "deliverable_id,title,status,canonical_path,version,date,sha256,"
                    "privacy_classification,private_source_ref"
                ),
                (
                    "E07,Workshops,published-local,"
                    "deliverables/E07-workshops/final/e07-actas-conclusiones-talleres-v1-0.md,"
                    "V1.0,2026-06-18,abc,public,"
                    "SOURCE_E07_CONTROLLED_DOCX_20260710"
                ),
            ]
        ),
    )
    _write_text(
        repo_root / "deliverables" / "E07-workshops" / "README.md",
        "\n".join(
            [
                "# E07 - Workshops",
                "",
                "Status: `published-local`",
                "",
                "No final or feedback evidence exists.",
            ]
        ),
    )
    issues = e7_gate.collect_issues(repo_root)

    assert any("missing deliverables/E07-workshops/final" in issue for issue in issues)
    assert any(
        "missing deliverables/E07-workshops/evidence" in issue for issue in issues
    )


def test_current_operator_code_examples_do_not_reference_ignored_local_workspaces():
    leaks = []
    for path in CURRENT_OPERATOR_DOCS:
        text = path.read_text(encoding="utf-8")
        for block in _fenced_code_blocks(text):
            for marker in LOCAL_OPERATOR_MARKERS:
                if marker in block:
                    leaks.append(f"{path.relative_to(REPO_ROOT)}: {marker}")

    assert leaks == []


def test_removed_guided_doc_stays_out_of_public_boundary():
    assert not (REPO_ROOT / "api" / "docs" / LEGACY_DOC).exists()


def test_e8_traces_sanitized_e7_feedback_without_overclaiming_coverage():
    text = E8_USE_CASES_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "completada para fines" not in normalized
    assert "e7" in normalized
    assert "e07-feedback-traceability-v1-0.csv" in normalized
    assert "f01" in normalized and "f10" in normalized
    assert "no demuestra participación directa de reguladores" in normalized


def test_e11_records_url_and_dated_200_without_claiming_impact_closure():
    report = E11_REPORT_PATH.read_text(encoding="utf-8")
    evidence = E11_URL_PATH.read_text(encoding="utf-8")
    combined = f"{report}\n{evidence}"
    normalized = " ".join(combined.lower().split())

    assert "official-url-recorded" in combined
    assert "2026-05-30" in combined
    assert "2026-05-31" in combined
    assert "2026-06-01" in combined
    assert "http 404" in normalized
    assert "2026-06-23" in combined
    assert "http 200" in normalized
    assert "does not by itself" in normalized
    assert "full subsidy closure" in normalized


def test_public_traceability_matrix_records_e11_200_observation():
    text = TRACEABILITY_MATRIX_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "e11" in normalized
    assert "2026-06-23" in text
    assert "http 200" in normalized
    assert check_deliverables.scan_public_markdown(TRACEABILITY_MATRIX_PATH) == []


def test_public_status_surfaces_match_the_deliverables_register():
    rows = check_deliverables.read_register()

    assert check_deliverables.validate_public_status_surfaces(rows) == []


def test_canonical_document_metadata_matches_the_deliverables_register():
    rows = check_deliverables.read_register()

    assert check_deliverables.validate_register_document_metadata(rows) == []


def test_document_metadata_validator_flags_version_date_and_status_drift(tmp_path):
    repo_root = tmp_path / "repo"
    document = repo_root / "deliverables" / "E01-demo" / "final" / "e01.md"
    _write_text(
        document,
        "\n".join(
            [
                "# E01",
                "",
                "| Campo | Valor |",
                "|---|---|",
                "| Versión | V0.9 |",
                "| Fecha | 2026-01-01 |",
                "| Estado | Borrador |",
            ]
        ),
    )
    rows = [
        {
            "deliverable_id": "E01",
            "status": "published-local",
            "canonical_path": "deliverables/E01-demo/final/e01.md",
            "version": "V1.0",
            "date": "2026-06-18",
        }
    ]

    issues = check_deliverables.validate_register_document_metadata(
        rows, repo_root=repo_root
    )

    assert any("E01 version" in issue for issue in issues)
    assert any("E01 date" in issue for issue in issues)
    assert any("E01 status" in issue for issue in issues)


def test_public_status_surface_validator_flags_stale_matrix_status(tmp_path):
    repo_root = tmp_path / "repo"
    _write_text(
        repo_root / "deliverables" / "README.md",
        "\n".join(
            [
                "# Official Deliverables",
                "",
                "- Published locally: `E07`, `E12`, `E13`",
                "- Official URL evidence: `E11`",
            ]
        ),
    )
    _write_text(
        repo_root
        / "deliverables"
        / "evidence-public"
        / "pdf-first-traceability-matrix-sds-v2026-02-02.md",
        "\n".join(
            [
                "| Deliverable | Register status |",
                "|---|---|",
                "| `E07` | `external-not-imported` |",
                "| `E11` | `official-url-recorded` |",
                "| `E12` | `published-local` |",
                "| `E13` | `published-local` |",
            ]
        ),
    )
    rows = [
        {"deliverable_id": "E07", "status": "published-local"},
        {"deliverable_id": "E11", "status": "official-url-recorded"},
        {"deliverable_id": "E12", "status": "published-local"},
        {"deliverable_id": "E13", "status": "published-local"},
    ]

    issues = check_deliverables.validate_public_status_surfaces(
        rows, repo_root=repo_root
    )

    assert any("E07" in issue and "external-not-imported" in issue for issue in issues)


def test_acceptance_gate_docs_distinguish_structure_history_and_runtime_evidence():
    text = ACCEPTANCE_GATES_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.lower().split())

    assert "make e4-gate` is a structural" in normalized
    assert "does not prove postgresql runtime performance" in normalized
    assert "make e7-gate` validates the published package structure" in normalized
    assert "does not prove the dossier timing or coverage thresholds" in normalized
    assert "retained historical snapshot" in normalized
    assert "does not rerun the current checkout" in normalized
    assert "repo-closure-check` is a publication-presence boundary" in normalized
    assert "not a semantic or subsidy-closure certificate" in normalized


def test_current_changelog_preserves_authoritative_owner_accepted_status():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "### Current deliverable publication status (authoritative)" in text
    assert "E01–E13 y R1–R23 están cerrados" in normalized
    assert "dossier-closure-status-sds-v2026-09-18.md" in text
    assert "Una observación técnica posterior no revoca ni sustituye" in normalized
    statuses = {
        row["deliverable_id"]: row["status"]
        for row in check_deliverables.read_register()
    }
    assert all(
        statuses[identifier] == "published-local"
        for identifier in ("E07", "E12", "E13")
    )
    assert (
        "e01-inventario-datos-clave-variables-brutas-v2026-06-09-diseno-claude.docx"
        not in text
    )
    assert (
        REPO_ROOT
        / "deliverables/E01-mapeo-datos-normativas/final/e01-inventario-datos-clave-variables-brutas-v2026-06-09.md"
    ).is_file()


def test_orphan_final_candidate_is_flagged_unless_registered(tmp_path):
    """codex F11 M1: an unpromoted candidate in a public final/ tree must be flagged
    when absent from the register, and clean once a register row tracks it."""
    deliverables_root = tmp_path / "deliverables"
    final_dir = deliverables_root / "E99-demo" / "final"
    final_dir.mkdir(parents=True)
    candidate = final_dir / "e99-informe-candidato-v2026-06-19.md"
    candidate.write_text(
        "# E99\n\nCandidato final V2026-06-19; no promovido aun.\n",
        encoding="utf-8",
    )
    # an auxiliary (non-candidate) final artifact must NOT be flagged
    (final_dir / "e99-english-source.md").write_text("# E99 EN\n", encoding="utf-8")

    rel = "deliverables/E99-demo/final/e99-informe-candidato-v2026-06-19.md"

    unregistered = check_deliverables.orphan_final_candidate_issues(
        [], deliverables_root=deliverables_root, repo_root=tmp_path
    )
    assert any("e99-informe-candidato" in issue for issue in unregistered)
    assert not any("english-source" in issue for issue in unregistered)

    registered = check_deliverables.orphan_final_candidate_issues(
        [{"canonical_path": rel}],
        deliverables_root=deliverables_root,
        repo_root=tmp_path,
    )
    assert registered == []
