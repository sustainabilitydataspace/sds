from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "edc_bundle_from_register.py"
spec = importlib.util.spec_from_file_location("edc_bundle_from_register", MODULE_PATH)
assert spec is not None and spec.loader is not None
edc_bundle = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = edc_bundle
spec.loader.exec_module(edc_bundle)

pytestmark = pytest.mark.docs_only


def test_load_csv_uses_nonblocking_nofollow_descriptor(monkeypatch, tmp_path):
    register_csv = tmp_path / "register.csv"
    register_csv.write_text("identifier,title\nurn:test:1,Test\n", encoding="utf-8")
    original_open = edc_bundle.os.open
    observed = False

    def assert_secure_open(path, flags, *args, **kwargs):
        nonlocal observed
        if Path(path) == register_csv:
            observed = True
            assert flags & edc_bundle.os.O_NONBLOCK
            assert flags & edc_bundle.os.O_NOFOLLOW
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(edc_bundle.os, "open", assert_secure_open)

    headers, rows = edc_bundle.load_csv(register_csv)

    assert headers == ["identifier", "title"]
    assert rows == [{"identifier": "urn:test:1", "title": "Test"}]
    assert observed


def test_load_csv_enforces_own_row_limit(monkeypatch, tmp_path):
    register_csv = tmp_path / "register.csv"
    register_csv.write_text(
        "identifier,title\nurn:test:1,One\nurn:test:2,Two\nurn:test:3,Three\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(edc_bundle, "MAX_REGISTER_ROWS", 2)

    with pytest.raises(edc_bundle.RegisterReadError, match="row count exceeds limit"):
        edc_bundle.load_csv(register_csv)


def test_load_csv_enforces_utf8_field_byte_limit(monkeypatch, tmp_path):
    register_csv = tmp_path / "register.csv"
    register_csv.write_text(
        "identifier,title\nurn:test:1," + ("😀" * 9) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(edc_bundle, "MAX_CSV_FIELD_BYTES", 32)

    with pytest.raises(edc_bundle.RegisterReadError, match="field exceeds byte limit"):
        edc_bundle.load_csv(register_csv)


def test_load_csv_rejects_oversized_input(monkeypatch, tmp_path):
    register_csv = tmp_path / "register.csv"
    register_csv.write_bytes(b"identifier\n" + (b"x" * 32) + b"\n")
    monkeypatch.setattr(edc_bundle, "MAX_REGISTER_CSV_BYTES", 16)

    with pytest.raises(edc_bundle.RegisterReadError, match="exceeds size limit"):
        edc_bundle.load_csv(register_csv)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_cli_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "register.csv"
    os.mkfifo(fifo)
    completed = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--input",
            str(fifo),
            "--outdir",
            str(tmp_path / "out"),
            "--policy-registry",
            str(REPO_ROOT / "docs" / "policies" / "policy_registry.json"),
            "--strict",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert completed.returncode == 1
    assert "not an independent regular file" in completed.stderr
