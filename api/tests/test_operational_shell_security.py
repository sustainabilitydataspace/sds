"""Security contracts for privileged operational shell entry points."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def test_native_deploy_uses_authenticated_helper_for_tar_and_literal_env():
    script = (API_ROOT / "scripts" / "deploy_native_release.sh").read_text(
        encoding="utf-8"
    )

    assert "--tarball-sha256" in script
    assert '"$SECURE_IO_HELPER" extract-tar' in script
    assert '"$SECURE_IO_HELPER" env-digest' in script
    assert '"$SECURE_IO_HELPER" env-exec' in script
    assert 'source "$ENV_FILE"' not in script
    assert 'tar -xf "$TARBALL"' not in script
    assert "umask 077" in script


def test_native_deploy_uses_private_unpredictable_health_file():
    script = (API_ROOT / "scripts" / "deploy_native_release.sh").read_text(
        encoding="utf-8"
    )

    assert 'mktemp "${TMPDIR:-/tmp}/sds-api-health.XXXXXX"' in script
    assert "/tmp/sds-api-health-native-release.json" not in script
    assert "/tmp/sds-api-health-native-rollback.json" not in script


def test_backup_outputs_are_private_from_creation():
    script = (API_ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")

    assert "umask 077" in script
    assert '"$SECURE_IO_HELPER" capture-file' in script
    assert '"$SECURE_IO_HELPER" write-backup-manifest' in script
    assert '>"$out"' not in script
    assert 'cat >"$manifest"' not in script


def test_restore_streams_authenticated_snapshot_and_fails_closed_on_service_state():
    script = (API_ROOT / "scripts" / "restore.sh").read_text(encoding="utf-8")

    assert "stream_args=(stream-file" in script
    assert 'python "$SECURE_IO_HELPER" "${stream_args[@]}"' in script
    assert '"$SECURE_IO_HELPER" compose-service-stopped' in script
    assert '"$SECURE_IO_HELPER" read-backup-manifest' in script
    assert 'cat "$backup_file"' not in script
    assert "grep -Eq" not in script
    assert "|| true" not in script


def test_restore_timeout_bounds_initial_compose_service_inspection(tmp_path):
    backup = tmp_path / "backup.sql"
    backup.write_text("SELECT 1;\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    fake_docker.chmod(0o755)

    started = time.monotonic()
    completed = subprocess.run(
        [
            "bash",
            str(API_ROOT / "scripts" / "restore.sh"),
            "--yes",
            "--allow-unverified-restore",
            "--skip-verify",
            "--timeout-seconds",
            "1",
            str(backup),
        ],
        cwd=API_ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert time.monotonic() - started < 4
    assert completed.returncode == 2
    assert "Unable to determine whether the compose api service is stopped" in (
        completed.stderr
    )
