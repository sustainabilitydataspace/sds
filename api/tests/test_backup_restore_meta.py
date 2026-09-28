"""Offline-safe checks for Postgres backup/restore docs and scripts."""

from __future__ import annotations

from pathlib import Path


def test_backup_restore_runbook_and_scripts_exist():
    api_root = Path(__file__).resolve().parents[1]

    runbook = api_root / "docs" / "backup_restore.md"
    assert runbook.is_file()
    runbook_text = runbook.read_text(encoding="utf-8")
    runbook_text_lower = runbook_text.lower()
    assert "postgres" in runbook_text_lower
    assert "pg_dump" in runbook_text_lower

    backup_script = api_root / "scripts" / "backup.sh"
    restore_script = api_root / "scripts" / "restore.sh"
    backup_ps_script = api_root / "scripts" / "backup.ps1"
    restore_ps_script = api_root / "scripts" / "restore.ps1"
    assert backup_script.is_file()
    assert restore_script.is_file()
    assert backup_ps_script.is_file()
    assert restore_ps_script.is_file()

    backup_text = backup_script.read_text(encoding="utf-8").lower()
    restore_text = restore_script.read_text(encoding="utf-8").lower()
    backup_ps_text = backup_ps_script.read_text(encoding="utf-8")
    restore_ps_text = restore_ps_script.read_text(encoding="utf-8")

    for text in (backup_text, restore_text):
        assert "docker compose" in text or "docker-compose" in text
        assert "postgres" in text

    assert "--yes" in restore_text
    restore_script_text = restore_script.read_text(encoding="utf-8")
    restore_yes_guard = "Refusing non-interactive restore without --yes"
    assert restore_yes_guard in restore_script_text
    assert "-Force" in restore_ps_text
    assert "Refusing non-interactive restore without -Force" in restore_ps_text
    assert (
        "Refusing restore while the compose api service is running"
        in restore_script_text
    )
    assert (
        "Refusing restore while the compose api service is running" in restore_ps_text
    )

    assert "Out-File" not in backup_ps_text
    assert "RedirectStandardOutput" in backup_ps_text
    assert "Get-Content -LiteralPath $resolvedBackup |" not in restore_ps_text
    assert "RedirectStandardInput" in restore_ps_text

    assert "Log de operación" not in runbook_text
    assert "Verificación de integridad" not in runbook_text
    assert "backup.sh --full" not in runbook_text
    assert "sds_api_postgres_data" not in runbook_text
    assert "com.docker.compose.project=sds-api" in runbook_text
    assert "com.docker.compose.volume=postgres_data" in runbook_text


def test_backup_restore_scripts_support_disposable_targets_and_manifests():
    api_root = Path(__file__).resolve().parents[1]

    backup_text = (api_root / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore_text = (api_root / "scripts" / "restore.sh").read_text(encoding="utf-8")
    backup_ps_text = (api_root / "scripts" / "backup.ps1").read_text(encoding="utf-8")
    restore_ps_text = (api_root / "scripts" / "restore.ps1").read_text(encoding="utf-8")
    dev_ps_text = (api_root / "scripts" / "dev.ps1").read_text(encoding="utf-8")

    for text in (backup_text, restore_text):
        assert "--project-name" in text
        assert "--env-file" in text
        assert "--compose-file" in text
        assert "compose_args" in text

    for text in (backup_ps_text, restore_ps_text):
        assert "$ProjectName" in text
        assert "$EnvFile" in text
        assert "$ComposeFile" in text
        assert "Build-ComposeArgs" in text

    assert "sha256sum" in backup_text or "shasum -a 256" in backup_text
    assert "manifest" in backup_text.lower()
    assert "Get-FileHash" in backup_ps_text
    assert "manifest" in backup_ps_text.lower()

    assert "--require-manifest" in restore_text
    assert "--skip-verify" in restore_text
    assert "alembic_version" in restore_text
    assert "indicators" in restore_text
    assert "concepts" in restore_text
    assert "-RequireManifest" in restore_ps_text
    assert "-SkipVerify" in restore_ps_text
    assert "alembic_version" in restore_ps_text
    assert "indicators" in restore_ps_text
    assert "concepts" in restore_ps_text
    assert '$backupParams["ProjectName"] = $ProjectName' in dev_ps_text
    assert '$restoreParams["ProjectName"] = $ProjectName' in dev_ps_text
    assert '$backupParams["ComposeFile"] = "compose.production.yml"' in dev_ps_text
    assert '$restoreParams["ComposeFile"] = "compose.production.yml"' in dev_ps_text


def test_backup_restore_timeout_and_wrapper_safety_flags_are_pinned():
    api_root = Path(__file__).resolve().parents[1]

    backup_text = (api_root / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore_text = (api_root / "scripts" / "restore.sh").read_text(encoding="utf-8")
    backup_ps_text = (api_root / "scripts" / "backup.ps1").read_text(encoding="utf-8")
    restore_ps_text = (api_root / "scripts" / "restore.ps1").read_text(encoding="utf-8")
    dev_ps_text = (api_root / "scripts" / "dev.ps1").read_text(encoding="utf-8")

    for text in (backup_text, restore_text):
        assert "--timeout-seconds" in text
        assert "TIMEOUT_SECONDS" in text
        assert "docker_with_timeout" in text
        assert "command -v timeout" in text

    assert "cleanup_incomplete_backup" in backup_text
    assert 'rm -f "$out" "$manifest"' in backup_text
    assert "backup_completed=1" in backup_text

    for text in (backup_ps_text, restore_ps_text):
        assert "[int]$TimeoutSeconds = 0" in text
        assert "AddSeconds($TimeoutSeconds)" in text
        assert "$process.Kill()" in text
        assert "Docker process timed out after $TimeoutSeconds seconds." in text

    assert "CopyToAsync($process.StandardInput.BaseStream)" in restore_ps_text
    assert "if (-not $backupCompleted)" in backup_ps_text
    assert "Remove-Item -LiteralPath $outputPath" in backup_ps_text
    assert "Remove-Item -LiteralPath $manifestPath" in backup_ps_text

    assert '$backupParams["OutDir"] = $OutDir' in dev_ps_text
    assert '$backupParams["TimeoutSeconds"] = $TimeoutSeconds' in dev_ps_text
    assert '$restoreParams["ManifestPath"] = $ManifestPath' in dev_ps_text
    assert '$restoreParams["RequireManifest"] = $true' in dev_ps_text
    assert '$restoreParams["SkipVerify"] = $true' in dev_ps_text
    assert '$restoreParams["TimeoutSeconds"] = $TimeoutSeconds' in dev_ps_text
    assert "Test-ComposeApiRunning" in restore_ps_text
    assert 'ps", "api", "--format", "json"' in restore_ps_text
    assert "ps api --format json" in restore_text


def test_backup_restore_docs_pin_fail_closed_disposable_drill():
    api_root = Path(__file__).resolve().parents[1]
    runbook_text = (api_root / "docs" / "backup_restore.md").read_text(encoding="utf-8")
    early_safety_text = runbook_text.split("## Backup", maxsplit=1)[0]

    assert "--project-name sds-restore-drill" in runbook_text
    assert "--require-manifest" in runbook_text
    assert "-RequireManifest" in runbook_text
    assert "--set ON_ERROR_STOP=on" in runbook_text
    assert "manifiesto" in runbook_text.lower()
    assert "Opcional fuera de los scripts" in runbook_text
    assert "operator-drills" not in runbook_text
    assert "<operator-drill-dir>" in runbook_text
    assert "--require-manifest" in early_safety_text
    assert "-RequireManifest" in early_safety_text
    assert "--yes" in early_safety_text
    assert "-Force" in early_safety_text
    assert "dev.ps1 Backup" in runbook_text
    assert "dev.ps1 Restore" in runbook_text
    assert "-BackupFile" in runbook_text
    assert "servicio `api` antes de restaurar" in runbook_text
    assert "fallan" in runbook_text and "servicio Compose `api`" in runbook_text
    assert "-TimeoutSeconds" in runbook_text
    assert "--timeout-seconds" in runbook_text
    assert "backup incompleto" in runbook_text.lower()
    assert "restore con timeout" in runbook_text.lower()


def test_backup_restore_docs_pin_cadence_rpo_rto_and_evidence():
    api_root = Path(__file__).resolve().parents[1]
    runbook_text = (api_root / "docs" / "backup_restore.md").read_text(encoding="utf-8")

    assert "## Cadencia y evidencia de drills" in runbook_text
    assert "mensual" in runbook_text.lower()
    assert "RPO" in runbook_text
    assert "RTO" in runbook_text
    assert "<operator-drill-dir>/a06-backup-restore/drill-log.md" in runbook_text
    assert "SHA-256" in runbook_text
    assert "operador" in runbook_text.lower()
    assert "aprobacion" in runbook_text.lower()


def test_restore_scripts_enforce_target_and_unverified_guards():
    """codex F13 M1/M2: both restore scripts must validate the manifest target before a
    destructive restore and refuse unverified non-interactive restores."""
    api_root = Path(__file__).resolve().parents[1]
    sh = (api_root / "scripts" / "restore.sh").read_text(encoding="utf-8")
    ps = (api_root / "scripts" / "restore.ps1").read_text(encoding="utf-8")

    # M2: unverified non-interactive restore refused unless explicit break-glass flag
    assert "--allow-unverified-restore" in sh
    assert "Refusing unverified non-interactive restore" in sh
    assert "-AllowUnverifiedRestore" in ps
    assert "Refusing unverified non-interactive restore" in ps

    # M1: target db/user validated against manifest, override flag required on mismatch
    assert "--allow-target-mismatch" in sh
    assert "Restore target mismatch" in sh
    assert "read-backup-manifest" in sh and "manifest_db" in sh and "POSTGRES_DB" in sh
    assert "-AllowTargetMismatch" in ps
    assert "Restore target mismatch" in ps
    assert "postgres_db" in ps and "POSTGRES_DB" in ps


def test_restore_scripts_use_a_single_transaction_for_the_destructive_restore():
    api_root = Path(__file__).resolve().parents[1]
    sh = (api_root / "scripts" / "restore.sh").read_text(encoding="utf-8")
    ps = (api_root / "scripts" / "restore.ps1").read_text(encoding="utf-8")

    assert "--single-transaction" in sh
    assert "--single-transaction" in ps


def test_native_release_backup_uses_option_safe_db_name_and_exclusive_output():
    api_root = Path(__file__).resolve().parents[1]
    script = (api_root / "scripts" / "deploy_native_release.sh").read_text(
        encoding="utf-8"
    )

    assert 'pg_dump -Fc --dbname="$DB_NAME"' in script
    assert 'capture-file --path "$DB_BACKUP"' in script
    assert '> "$DB_BACKUP"' not in script
