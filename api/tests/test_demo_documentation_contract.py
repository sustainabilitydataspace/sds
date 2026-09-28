from __future__ import annotations

import subprocess
from pathlib import Path


def test_demo_docs_keep_single_worker_and_sync_import_contracts():
    api_root = Path(__file__).resolve().parents[1]
    for path in (api_root / "README.md", api_root / "docs" / "deployment.md"):
        text = path.read_text(encoding="utf-8")
        assert "one Uvicorn worker" in text or "un solo worker" in text
    assert (
        "Database-backed async indicator import jobs are unsupported pending H15"
        in (api_root / "README.md").read_text(encoding="utf-8")
    )
    assert "--workers 4" not in (api_root / "docs" / "deployment.md").read_text(
        encoding="utf-8"
    )
    assert "jobs persistentes para cargas masivas" not in (
        api_root / "README.md"
    ).read_text(encoding="utf-8")


def test_demo_commands_explicitly_scope_revision_writes_to_the_host_cli():
    api_root = Path(__file__).resolve().parents[1]
    makefile = (api_root / "Makefile").read_text(encoding="utf-8")
    for setting in (
        "VALUE_REVISION_API_ENABLED=true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED=true",
        "VALUE_REVISION_PRIMARY_READ_PATH=revision",
    ):
        assert setting in makefile
        assert setting in (api_root / "README.md").read_text(encoding="utf-8")
    assert (
        "$(DEMO_REVISION_WRITE_ENV) $(PYTHON) scripts/demo_runtime.py install"
        in makefile
    )


def test_demo_docs_make_the_api_principal_disposable_and_tenant_bound():
    api_root = Path(__file__).resolve().parents[1]
    text = (api_root / "README.md").read_text(encoding="utf-8")

    assert "ordinary `demo-verify` is db-backed only" in text.lower()
    assert "runtime-random `data_manager` bound to `value_import_gate`" in text
    assert "`demo-reset` refuses to remove demo rows once immutable" in text
    assert "it does not remove identities" in text


def test_demo_make_targets_do_not_interpolate_database_password_in_commands():
    api_root = Path(__file__).resolve().parents[1]
    synthetic = "test-only-not-real-password-123456"
    result = subprocess.run(
        [
            "make",
            "-n",
            "demo-install",
            "demo-verify",
            "demo-benchmark",
            "demo-reset",
            "DATABASE_URL=postgres" + f"ql://sds:{synthetic}@localhost:5432/sds",
        ],
        cwd=api_root,
        text=True,
        capture_output=True,
        check=True,
    )
    assert synthetic not in result.stdout + result.stderr
