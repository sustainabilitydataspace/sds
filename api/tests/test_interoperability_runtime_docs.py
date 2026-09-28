from __future__ import annotations

from pathlib import Path

from tests.legacy_guided_tokens import LEGACY_DOC

REPO_ROOT = Path(__file__).resolve().parents[2]

PUBLIC_DOCS = [
    REPO_ROOT / "api" / "docs" / "architecture.md",
    REPO_ROOT / "api" / "docs" / "api.md",
    REPO_ROOT / "api" / "docs" / "getting-started.md",
    REPO_ROOT / "api" / "docs" / "interoperability-runtime.md",
    REPO_ROOT / "api" / "docs" / "tests.md",
    REPO_ROOT / "api" / "docs" / "import-packages.md",
]

PRIVATE_MARKERS = (
    "D:/",
    "D:\\",
    "C:/",
    "C:\\",
    "OneDrive",
    ".local_artifacts",
    "workspace/consensus",
    "runbook/",
)


def test_runtime_interoperability_is_documented_on_public_surfaces():
    guide = REPO_ROOT / "api" / "docs" / "interoperability-runtime.md"
    assert guide.exists()

    expected_markers = {
        "README.md": "Runtime Interoperability",
        "api/docs/architecture.md": "Resolución runtime de valores interoperables",
        "api/docs/api.md": "Runtime Interoperability Guide",
        "api/docs/getting-started.md": "Probar interoperabilidad runtime en Swagger",
        "api/docs/interoperability-runtime.md": "POST /api/v1/values/resolve",
        "api/docs/tests.md": "Validación de interoperabilidad runtime",
    }

    for relative, marker in expected_markers.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert marker in text, f"{relative} must document {marker!r}"


def test_runtime_interoperability_docs_start_from_standard_codes():
    guide = (REPO_ROOT / "api" / "docs" / "interoperability-runtime.md").read_text(
        encoding="utf-8"
    )
    getting_started = (REPO_ROOT / "api" / "docs" / "getting-started.md").read_text(
        encoding="utf-8"
    )

    assert "Empieza por el código estándar" in guide
    assert "GET /api/v1/calculate/dependencies/urn:sds:disclosure:csrd:e3-5" in guide
    assert "No es un datapoint granular IG3" in guide
    assert "E3-5_01" in guide
    assert "Escenario A: la base ya tiene valores" in guide
    assert "Escenario B: la base no tiene valores" in guide
    assert "El usuario no tiene que conocer `syg:*` de antemano" in guide
    assert "Empieza por el código estándar que conoces de ESRS/GRI/GHG" in (
        getting_started
    )


def test_runtime_interoperability_docs_explain_certified_bridge_resolution():
    guide = (REPO_ROOT / "api" / "docs" / "interoperability-runtime.md").read_text(
        encoding="utf-8"
    )

    assert "GRI 302-1.e" in guide
    assert "resolve_gri_302_1e_from_certified_bridge" in guide
    assert "`execution_authority`" in guide
    assert "`bridge_id`" in guide
    assert "puente certificado" in guide


def test_public_runtime_docs_align_on_certified_bridge_resolution():
    api_reference = (REPO_ROOT / "api" / "docs" / "api.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "api" / "docs" / "architecture.md").read_text(
        encoding="utf-8"
    )
    tests_doc = (REPO_ROOT / "api" / "docs" / "tests.md").read_text(encoding="utf-8")

    for text in (api_reference, architecture, tests_doc):
        assert "puente certificado" in text
        assert "GRI 302-1.e" in text
    assert "`execution_authority`" in api_reference
    assert "`bridge_id`" in api_reference
    assert "mappings no equivalentes autorizados" in tests_doc


def test_removed_guided_guide_is_absent():
    assert not (REPO_ROOT / "api" / "docs" / LEGACY_DOC).exists()


def test_runtime_interoperability_public_docs_do_not_leak_local_markers():
    leaked = []
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        for marker in PRIVATE_MARKERS:
            if marker in text:
                leaked.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert leaked == []


def test_import_package_docs_describe_staged_recovery_and_payload_retention():
    guide = (REPO_ROOT / "api" / "docs" / "import-packages.md").read_text(
        encoding="utf-8"
    )

    normalized = guide.lower()

    assert "recuperacion por re-ejecucion idempotente" in normalized
    assert "rollback transaccional" in normalized
    assert "INDICATOR_IMPORT_VALIDATION_PAYLOAD_RETENTION_HOURS" in guide
    assert "Validation payload is no longer available" in guide
