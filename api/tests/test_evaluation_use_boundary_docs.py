"""Keep evaluation instructions separate from permission for operational use."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.docs_only


def test_root_quickstart_disclaims_productive_use_before_install_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    policy = readme.index("## Alcance de uso previsto")
    quickstart = readme.index("## Arranque de evaluación")
    assert policy < quickstart
    for required in (
        "código fuente disponible para evaluación",
        "código abierto",
        "LICENSE",
        "evaluación técnica",
        "actividad ordinaria propia",
        "prestar servicios",
        "SaaS",
        "acuerdo separado y por escrito",
        "componentes de terceros",
        "docs/license-and-publication.md",
        "docs/third-party-notices.md",
    ):
        assert required in readme


def test_evaluation_license_and_reporting_surfaces_exist():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "not an open-source license" in license_text
    assert "Production Use" in license_text
    assert "Commercial Use" in license_text
    assert "GitHub evaluation forks" in license_text
    assert "docs/third-party-notices.md" in license_text
    assert "SECURITY.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert (ROOT / "SECURITY.md").exists()
    assert (ROOT / "docs" / "license-and-publication.md").exists()
    assert (ROOT / "docs" / "third-party-notices.md").exists()
    assert (ROOT / "docs" / "security-reporting.md").exists()


def test_operator_guides_do_not_present_installation_as_use_permission():
    for relative_path in (
        "docs/public-profile.md",
        "api/README.md",
        "api/docs/getting-started.md",
        "api/docs/deployment.md",
        "api/docs/configuration.md",
        "api/docs/troubleshooting.md",
        "api/docs/import-packages.md",
        "api/deploy/aws-ec2-rds/README.md",
    ):
        guide = (ROOT / relative_path).read_text(encoding="utf-8")
        assert (
            "evaluación" in guide.lower() or "evaluation" in guide.lower()
        ), relative_path
        assert "README.md" in guide or "LICENSE" in guide, relative_path
        assert "terceros" in guide or "others" in guide, relative_path
        assert (
            "productiv" in guide.lower()
            or "explotación" in guide.lower()
            or "productive use" in guide.lower()
        ), relative_path


def test_unapproved_license_label_is_absent_from_api_metadata_and_guides():
    for relative_path in ("api/src/api/main.py", "api/README.md"):
        assert "Proprietary - Sygris" not in (ROOT / relative_path).read_text(
            encoding="utf-8"
        )
