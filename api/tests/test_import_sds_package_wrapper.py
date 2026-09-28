from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "import_sds_package.py"


def _load_module():
    scripts_root = str(REPO_ROOT / "scripts")
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    spec = importlib.util.spec_from_file_location("import_sds_package", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_import_sds_package_delegates_to_legacy_importer(monkeypatch):
    module = _load_module()
    seen: list[list[str] | None] = []

    def fake_main(argv):
        seen.append(argv)
        return 23

    monkeypatch.setattr(module, "_legacy_main", fake_main)

    assert module.main(["--dry-run"]) == 23
    assert seen == [["--dry-run"]]
