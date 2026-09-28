from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "import_atomizer_register_package.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "import_atomizer_register_package", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


register_import = _load_module()


def test_resolve_python_executable_prefers_service_venv_if_present(
    monkeypatch, tmp_path
):
    service_python = tmp_path / "api" / ".venv" / "bin" / "python"
    service_python.parent.mkdir(parents=True)
    service_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        register_import,
        "SERVICE_VENV_PYTHONS",
        (tmp_path / "missing" / "python.exe", service_python),
    )

    resolved = register_import.resolve_python_executable()
    assert resolved == str(service_python)


def test_resolve_python_executable_falls_back_to_current_interpreter(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        register_import,
        "SERVICE_VENV_PYTHONS",
        (tmp_path / "missing" / "python.exe", tmp_path / "missing" / "python"),
    )

    assert register_import.resolve_python_executable() == sys.executable


def test_resolve_register_csv_defaults_to_package_dir(tmp_path):
    package_dir = tmp_path / "atomizer_package"
    resolved = register_import.resolve_register_csv(package_dir, None)
    assert resolved == (package_dir / "sds_dataset_register.csv").resolve()


def test_main_requires_package_or_csv_when_env_is_unset(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.delenv(register_import.PACKAGE_DIR_ENV, raising=False)
    monkeypatch.setattr(
        register_import.subprocess,
        "call",
        lambda command, cwd: calls.append(command) or 0,
    )

    result = register_import.main(["--dry-run"])

    assert result == 1
    assert calls == []


def test_main_delegates_package_to_hardened_full_importer_without_resolving_path(
    monkeypatch, tmp_path
):
    package_dir = tmp_path / "package-link"
    calls: list[tuple[list[str], str, int]] = []
    monkeypatch.setattr(
        register_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append((command, cwd, timeout)) or 0,
    )

    result = register_import.main(
        ["--package-dir", str(package_dir), "--dry-run", "--batch-size", "25"]
    )

    assert result == 0
    assert len(calls) == 1
    command, cwd, timeout = calls[0]
    assert command[1] == str(register_import.FULL_PACKAGE_IMPORT_SCRIPT)
    assert command[command.index("--package-dir") + 1] == str(package_dir)
    assert "--skip-standard-versioning" in command
    assert "--skip-calculation-contract" in command
    assert "--skip-values" in command
    assert "--dry-run" in command
    assert command[command.index("--batch-size") + 1] == "25"
    assert cwd == str(register_import.REPO_ROOT)
    assert timeout == register_import.IMPORT_SUBPROCESS_TIMEOUT_SECONDS


def test_main_real_import_requires_package_dir(monkeypatch, tmp_path):
    csv_path = tmp_path / "sds_dataset_register.csv"
    csv_path.write_text("indicator_id\nsynthetic\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        register_import.subprocess,
        "call",
        lambda command, cwd, timeout: calls.append(command) or 0,
    )

    result = register_import.main(["--csv", str(csv_path)])

    assert result == 1
    assert calls == []


def test_main_timeout_fails_closed(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "sds_dataset_register.csv").write_text(
        "indicator_id\nsynthetic\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        register_import.subprocess,
        "call",
        lambda command, cwd, timeout: (_ for _ in ()).throw(
            register_import.subprocess.TimeoutExpired(command, timeout)
        ),
    )

    result = register_import.main(["--package-dir", str(package_dir), "--dry-run"])

    assert result == 1
