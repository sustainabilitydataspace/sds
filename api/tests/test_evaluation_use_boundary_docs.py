"""Keep evaluation instructions separate from permission for operational use."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_root_quickstart_disclaims_productive_use_before_install_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    policy = readme.index("## Alcance de uso previsto")
    quickstart = readme.index("## Arranque de evaluación")
    assert policy < quickstart
    for required in (
        "descarga, instalación y ejecución de pruebas",
        "evaluación técnica en un entorno de pruebas",
        "actividad ordinaria propia",
        "prestar servicios",
        "acuerdo separado y por escrito",
        "componentes de terceros",
        "No se presenta esta nota\ncomo una licencia definitiva",
    ):
        assert required in readme


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
        assert "README.md" in guide, relative_path
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
