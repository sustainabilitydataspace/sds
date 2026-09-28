"""Adversarial tests for privileged operational file intake."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "secure_operational_io.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("secure_operational_io", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tar_bytes(entries: list[tuple[str, bytes, int]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content, mode in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_extract_rejects_digest_mismatch_before_destination_creation(tmp_path):
    helper = _load_helper()
    archive = tmp_path / "release.tar"
    archive.write_bytes(_tar_bytes([("api/app.py", b"ok", 0o644)]))
    destination = tmp_path / "release"

    with pytest.raises(helper.OperationalIOError, match="digest"):
        helper.extract_authenticated_tar(
            archive,
            destination,
            expected_sha256="0" * 64,
            strip_components=1,
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "api/../../escape", "api/bad\\name"],
)
def test_extract_rejects_unsafe_member_before_writing(tmp_path, name):
    helper = _load_helper()
    archive = tmp_path / "release.tar"
    data = _tar_bytes([(name, b"unsafe", 0o644)])
    archive.write_bytes(data)

    with pytest.raises(helper.OperationalIOError, match="unsafe archive member"):
        helper.extract_authenticated_tar(
            archive,
            tmp_path / "release",
            expected_sha256=hashlib.sha256(data).hexdigest(),
            strip_components=1,
        )

    assert not (tmp_path / "release").exists()
    assert not (tmp_path / "escape").exists()


def test_extract_accepts_git_archive_layout_and_preserves_executable_bit(tmp_path):
    helper = _load_helper()
    archive = tmp_path / "release.tar"
    data = _tar_bytes(
        [
            ("api/src/app.py", b"print('ok')\n", 0o644),
            ("api/scripts/run.sh", b"#!/bin/sh\n", 0o755),
        ]
    )
    archive.write_bytes(data)
    destination = tmp_path / "release"

    receipt = helper.extract_authenticated_tar(
        archive,
        destination,
        expected_sha256=hashlib.sha256(data).hexdigest(),
        strip_components=1,
    )

    assert receipt.sha256 == hashlib.sha256(data).hexdigest()
    assert (destination / "src" / "app.py").read_bytes() == b"print('ok')\n"
    assert os.stat(destination / "scripts" / "run.sh").st_mode & 0o111


def test_env_parser_treats_values_as_literal_and_rejects_shell_grammar():
    helper = _load_helper()

    assert helper.parse_env_bytes(
        b"TOKEN=$(touch /tmp/nope)\nNAME=value with spaces\n"
    ) == {
        "TOKEN": "$(touch /tmp/nope)",
        "NAME": "value with spaces",
    }
    for payload in (
        b"export TOKEN=value\n",
        b" TOKEN=value\n",
        b"TOKEN=value\nTOKEN=other\n",
        b"PATH=/tmp\n",
        b"TOKEN=value\r\n",
    ):
        with pytest.raises(helper.OperationalIOError):
            helper.parse_env_bytes(payload)


def test_stream_authenticated_file_uses_verified_bytes(tmp_path):
    helper = _load_helper()
    backup = tmp_path / "backup.sql"
    content = b"select 1;\n"
    backup.write_bytes(content)
    output = io.BytesIO()

    receipt = helper.stream_authenticated_file(
        backup,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        output=output,
    )

    assert output.getvalue() == content
    assert receipt.size == len(content)


def test_stream_authenticated_file_rejects_manifest_size_mismatch(tmp_path):
    helper = _load_helper()
    backup = tmp_path / "backup.sql"
    content = b"select 1;\n"
    backup.write_bytes(content)
    output = io.BytesIO()

    with pytest.raises(helper.OperationalIOError, match="size"):
        helper.stream_authenticated_file(
            backup,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            expected_size=len(content) + 1,
            output=output,
        )

    assert output.getvalue() == b""


def test_compose_service_state_parser_fails_closed():
    helper = _load_helper()

    assert helper.compose_service_is_stopped(b"[]", service="api") is True
    assert (
        helper.compose_service_is_stopped(
            b'[{"Service":"api","State":"exited"}]', service="api"
        )
        is True
    )
    assert (
        helper.compose_service_is_stopped(
            b'[{"Service":"api","State":"running"}]', service="api"
        )
        is False
    )
    with pytest.raises(helper.OperationalIOError):
        helper.compose_service_is_stopped(b"not-json", service="api")


def test_capture_private_file_is_exclusive_and_mode_0600(tmp_path):
    helper = _load_helper()
    destination = tmp_path / "backup.sql"
    receipt = helper.capture_private_file(destination, io.BytesIO(b"backup\n"))

    assert destination.read_bytes() == b"backup\n"
    assert destination.stat().st_mode & 0o777 == 0o600
    assert receipt.sha256 == hashlib.sha256(b"backup\n").hexdigest()
    with pytest.raises(helper.OperationalIOError, match="create private output"):
        helper.capture_private_file(destination, io.BytesIO(b"replacement"))


def test_write_backup_manifest_escapes_values_and_is_exclusive(tmp_path):
    helper = _load_helper()
    path = tmp_path / "backup.manifest.json"
    helper.write_backup_manifest(
        path,
        created_at_utc="2026-09-25T00:00:00Z",
        backup_path='backup"name.sql',
        size_bytes=7,
        sha256=hashlib.sha256(b"backup\n").hexdigest(),
        compose_project="sds-test",
        env_file='config"name.env',
        compose_file="compose.yml",
        postgres_db="sds_api",
        postgres_user="postgres",
    )

    assert json.loads(path.read_text(encoding="utf-8"))["env_file"] == 'config"name.env'
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(helper.OperationalIOError, match="create private output"):
        helper.write_backup_manifest(
            path,
            created_at_utc="2026-09-25T00:00:00Z",
            backup_path="backup.sql",
            size_bytes=7,
            sha256=hashlib.sha256(b"backup\n").hexdigest(),
            compose_project="sds-test",
            env_file=".env",
            compose_file="compose.yml",
            postgres_db="sds_api",
            postgres_user="postgres",
        )


def test_read_backup_manifest_rejects_duplicate_keys(tmp_path):
    helper = _load_helper()
    path = tmp_path / "duplicate.manifest.json"
    path.write_text(
        '{"schema_version":1,"sha256":"'
        + "0" * 64
        + '","sha256":"'
        + "1" * 64
        + '","size_bytes":1,"created_at_utc":"x","backup_path":"x",'
        '"compose_project":"x","env_file":"x","compose_file":"x",'
        '"postgres_db":"x","postgres_user":"x"}',
        encoding="utf-8",
    )

    with pytest.raises(helper.OperationalIOError, match="duplicate"):
        helper.read_backup_manifest(path)
