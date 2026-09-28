from __future__ import annotations

from pathlib import Path

from tests.legacy_guided_tokens import (
    LEGACY_DOCS_TOKEN_PATH,
    LEGACY_MANUAL_KEY_PREFIX,
    LEGACY_WORDS,
)


def test_smoke_stack_script_exists_and_mentions_key_endpoints():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "smoke_stack.sh"
    ps_script_path = root / "scripts" / "smoke_stack.ps1"
    expected_script_message = "Expected Bash smoke script"
    bash_missing_message = "Bash smoke script missing"

    assert script_path.exists(), expected_script_message
    assert ps_script_path.exists(), "Expected PowerShell smoke script"

    content = script_path.read_text(encoding="utf-8")
    ps_content = ps_script_path.read_text(encoding="utf-8")

    for needle in (
        "/healthz",
        "/docs",
        "/redoc",
        "/auth/login",
        "/api/v1/indicators",
        "/api/v1/values",
        "/api/v1/concepts",
        "/api/v1/values/import",
    ):
        assert needle in content, f"Smoke script must reference {needle}"

    assert "strip_cr" in content
    assert "%$'\\r'" in content
    assert 'NO_START="${NO_START:-0}"' in content
    assert "Reusing already started stack" in content

    for endpoint in ("/healthz", "/ready"):
        assert endpoint in content
        assert endpoint in ps_content

    for shared_token in (
        "BOOTSTRAP_ADMIN_PASSWORD",
        "smoke_facility",
        "smoke_company",
        "smoke_stack",
    ):
        assert shared_token in content, bash_missing_message
        assert (
            shared_token in ps_content
        ), f"PowerShell smoke script missing {shared_token}"

    assert "smoke:${hierarchy_id}:bulk-1" in content
    assert "smoke:${hierarchy_id}:bulk-2" in content
    assert "smoke:${hierarchyId}:bulk-1" in ps_content
    assert "smoke:${hierarchyId}:bulk-2" in ps_content

    for stale_token in (
        "qa_4a1d8346",
        "0.1200000000",
        LEGACY_DOCS_TOKEN_PATH,
        LEGACY_MANUAL_KEY_PREFIX,
        f"Swagger {LEGACY_WORDS}",
    ):
        assert stale_token not in content
        assert stale_token not in ps_content


def test_smoke_uses_runtime_tenant_bound_data_manager_for_value_writes():
    root = Path(__file__).resolve().parents[1]
    bash_content = (root / "scripts" / "smoke_stack.sh").read_text(encoding="utf-8")
    ps_content = (root / "scripts" / "smoke_stack.ps1").read_text(encoding="utf-8")

    for token in (
        "/auth/users",
        "smoke_data_manager",
        "smoke_company",
        "data_manager",
        "Disposable data-manager login failed",
    ):
        assert token in bash_content
        assert token in ps_content

    assert "import secrets" in bash_content
    assert "secrets.token_hex(15)" in bash_content
    assert "secrets.token_urlsafe(32)" in bash_content
    assert "data_manager_authz_header" in bash_content
    assert bash_content.count('-H "$data_manager_authz_header"') == 4
    assert 'authz_header="Authorization: Bearer $token"' in bash_content
    assert "Authorization: Bearer ***" not in bash_content

    assert "RandomNumberGenerator" in ps_content
    assert "New-SmokeRandomToken" in ps_content
    assert "New-SmokeRandomToken -ByteCount 15" in ps_content
    for endpoint in (
        '/api/v1/values" -Headers $dataManagerHeaders',
        '/api/v1/values?entity=$entityId&limit=50" -Headers $dataManagerHeaders',
        '/api/v1/calculate/dependencies/csrd:E3_5" -Headers $dataManagerHeaders',
        '/api/v1/values/import" -Headers $dataManagerHeaders',
    ):
        assert endpoint in ps_content

    assert len("smoke_data_manager_") + (15 * 2) <= 50
