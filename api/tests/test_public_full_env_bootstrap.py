from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = API_ROOT / "scripts" / "bootstrap_public_env.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("public_env_bootstrap", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_runtime_template_preserves_settings_and_fills_only_required_fields():
    module = _load_module()
    template = (API_ROOT / ".env.example").read_text(encoding="utf-8")
    generated = {
        "POSTGRES_PASSWORD": "local-database-test-secret",
        "JWT_SECRET_KEY": "local-jwt-test-secret",
        "EXPORT_SIGNING_SECRET": "local-export-test-secret",
        "BOOTSTRAP_ADMIN_PASSWORD": "local-admin-test-secret",
    }
    rendered = module.render_public_env(template, generated)
    for key, value in generated.items():
        assert rendered.count(f"{key}={value}\n") == 1
    assert "REQUIRE_DATABASE=true\n" in rendered
    assert "SEED_DEFAULT_USERS=true\n" in rendered
    assert "PORTAL_MOUNT_ENABLED=true\n" in rendered
    expected_url = (
        "DATABASE_URL=postgresql://sds:"
        + generated["POSTGRES_PASSWORD"]
        + "@127.0.0.1:5432/sds\n"
    )
    assert expected_url in rendered
    assert "# Optional infra\n" in rendered


def test_bootstrap_creates_ignored_private_env_without_exposing_secrets(tmp_path):
    output = tmp_path / ".env"
    first = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert output.exists() and output.stat().st_mode & 0o077 == 0
    text = output.read_text(encoding="utf-8")
    for key in (
        "POSTGRES_PASSWORD",
        "JWT_SECRET_KEY",
        "EXPORT_SIGNING_SECRET",
        "BOOTSTRAP_ADMIN_PASSWORD",
    ):
        line = next(line for line in text.splitlines() if line.startswith(key + "="))
        assert len(line.partition("=")[2]) >= 20
        assert line.partition("=")[2] not in first.stdout + first.stderr
    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode != 0
    assert second.stdout + second.stderr
