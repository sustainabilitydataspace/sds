from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_public_secrets.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "public-secret-scan.yml"


def _load_script(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_secret_scanner_rejects_high_confidence_tokens(tmp_path):
    scanner = _load_script("check_public_secrets", SCRIPT_PATH)
    token = "AK" + "IA" + "A" * 16
    candidate = tmp_path / "docs" / "leak.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(f"credential: {token}\n", encoding="utf-8")

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert any("AWS access key" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


@pytest.mark.parametrize(
    ("synthetic_credential", "label"),
    [
        ("AS" + "IA" + "Z" * 16, "AWS temporary access key"),
        (
            "DATABASE_URL=postgres"
            + "ql://fixture:test-only-not-real-424242@db.invalid/sds",
            "database URL with embedded password",
        ),
        (
            "Authorization: Bearer "
            + ".".join(
                ["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiJmaXh0dXJlIn0", "x" * 24]
            ),
            "Bearer JWT",
        ),
    ],
)
def test_secret_scanner_covers_temporary_aws_db_password_and_jwt(
    tmp_path, synthetic_credential, label
):
    scanner = _load_script("check_public_secrets_new_classes", SCRIPT_PATH)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text(synthetic_credential, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any(label in issue for issue in issues)
    assert all(synthetic_credential not in issue for issue in issues)


@pytest.mark.parametrize(
    ("synthetic_credential", "label"),
    [
        ("github_pat_" + "A" * 22 + "_" + "B" * 59, "GitHub token"),
        ("sk-proj-" + "A" * 40, "OpenAI-style token"),
        (
            "SQLALCHEMY_DATABASE_URL=postgres"
            + "ql://fixture:test-only-not-real-424242@db.invalid/sds",
            "database URL with embedded password",
        ),
    ],
)
def test_secret_scanner_rejects_additional_standalone_tokens(
    tmp_path, synthetic_credential, label
):
    scanner = _load_script("check_public_secrets_more_tokens", SCRIPT_PATH)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text(synthetic_credential, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any(label in issue for issue in issues)
    assert all(synthetic_credential not in issue for issue in issues)


def test_database_url_placeholders_are_not_secrets_but_later_real_value_is(tmp_path):
    scanner = _load_script("check_public_secrets_placeholders", SCRIPT_PATH)
    candidate = tmp_path / "example.txt"
    dynamic = "DATABASE_URL=postgres" + "ql://u:${DB_PASSWORD}@db.invalid/sds"
    redacted = "DATABASE_URL=postgres" + "ql://u:[REDACTED]@db.invalid/sds"
    actual = "DATABASE_URL=postgres" + "ql://u:test-only-not-real-424242@db.invalid/sds"
    candidate.write_text(dynamic + "\n" + redacted, encoding="utf-8")
    assert scanner.collect_issues(tmp_path, paths=[candidate]) == []
    candidate.write_text(dynamic + "\n" + actual, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert len(issues) == 1
    assert "example.txt:2" in issues[0]


def test_standalone_postgres_uri_detected_without_assignment(tmp_path):
    scanner = _load_script("check_public_secrets_standalone_uri", SCRIPT_PATH)
    candidate = tmp_path / "example.txt"
    prefix = "postgres" + "ql://tester:"
    suffix = "@db.invalid/sds"
    candidate.write_text(
        prefix
        + "[REDACTED]"
        + suffix
        + "\n"
        + prefix
        + "${DB_PASSWORD}"
        + suffix
        + "\n",
        encoding="utf-8",
    )
    assert scanner.collect_issues(tmp_path, paths=[candidate]) == []
    credential = prefix + "synthetic-test-not-real" + suffix
    candidate.write_text(credential, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any("database URL with embedded password" in issue for issue in issues)
    assert all(credential not in issue for issue in issues)


@pytest.mark.parametrize("weak_password", ["pass", "password"])
def test_standalone_postgres_uri_does_not_exempt_usable_weak_password(
    tmp_path, weak_password
):
    scanner = _load_script("check_public_secrets_weak_password", SCRIPT_PATH)
    candidate = tmp_path / "example.txt"
    credential = "postgres" + f"ql://tester:{weak_password}@db.invalid/sds"
    candidate.write_text(credential, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any("database URL with embedded password" in issue for issue in issues)
    assert all(credential not in issue for issue in issues)


def test_secret_scanner_rejects_private_key_material(tmp_path):
    scanner = _load_script("check_public_secrets_private_key", SCRIPT_PATH)
    candidate = tmp_path / "docs" / "private-key.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "-----BEGIN " + "PRIVATE KEY-----\nredacted\n",
        encoding="utf-8",
    )

    assert scanner.collect_issues(tmp_path, paths=[candidate])


@pytest.mark.parametrize(
    ("synthetic_credential", "label"),
    [
        ("-----BEGIN ENCRYPTED " + "PRIVATE KEY-----", "private key material"),
        ("AWS_SECRET_ACCESS_KEY=" + "A" * 40, "AWS secret access key"),
        (
            '"DATABASE_URL": "postgres'
            + 'ql://fixture:synthetic-test-value@db.invalid/sds"',
            "database URL with embedded password",
        ),
    ],
)
def test_secret_scanner_detects_additional_credential_carriers(
    tmp_path, synthetic_credential, label
):
    scanner = _load_script("check_public_secrets_carriers", SCRIPT_PATH)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text(synthetic_credential, encoding="utf-8")
    assert any(
        label in issue for issue in scanner.collect_issues(tmp_path, paths=[candidate])
    )


def test_secret_scanner_does_not_flag_opaque_build_identifier(tmp_path):
    scanner = _load_script("check_public_secrets_build_id", SCRIPT_PATH)
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("sk-" + "Z" * 40, encoding="utf-8")
    assert scanner.collect_issues(tmp_path, paths=[candidate]) == []


def test_large_tracked_text_file_is_scanned(tmp_path):
    scanner = _load_script("check_public_secrets_large", SCRIPT_PATH)
    token = "AK" + "IA" + "B" * 16
    candidate = tmp_path / "large.txt"
    candidate.write_bytes(b"x" * (5 * 1024 * 1024 + 1) + b"\n" + token.encode("ascii"))

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert issues
    assert all(token not in issue for issue in issues)


def test_binary_tracked_file_is_scanned(tmp_path):
    scanner = _load_script("check_public_secrets_binary", SCRIPT_PATH)
    token = "AK" + "IA" + "C" * 16
    candidate = tmp_path / "payload.bin"
    candidate.write_bytes(b"\x00prefix\x00" + token.encode("ascii") + b"\x00suffix")

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert issues
    assert all(token not in issue for issue in issues)


def test_oversized_file_blocks_instead_of_skipping(tmp_path):
    scanner = _load_script("check_public_secrets_oversized", SCRIPT_PATH)
    candidate = tmp_path / "oversized.bin"
    candidate.write_bytes(b"x" * (scanner.MAX_FILE_BYTES + 1))

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert issues == ["oversized.bin:0: unscanned oversized file"]


def test_zip_members_are_scanned(tmp_path):
    scanner = _load_script("check_public_secrets_zip", SCRIPT_PATH)
    token = "AK" + "IA" + "D" * 16
    candidate = tmp_path / "candidate.docx"
    with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", f"<w:t>{token}</w:t>")

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert issues
    assert any("candidate.docx!word/document.xml" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


@pytest.mark.parametrize(
    "member_name",
    [
        "config/.env",
        "config/credentials.json",
        "nested/id_ed25519",
        "keys\\id_rsa",
        "tls/cert.p12",
    ],
)
def test_archive_member_sensitive_filename_blocks_publication(tmp_path, member_name):
    scanner = _load_script("check_public_secrets_archive_names", SCRIPT_PATH)
    candidate = tmp_path / "bundle.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        archive.writestr(member_name, "nothing matching token signatures")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any("sensitive filename" in issue for issue in issues)
    assert all("nothing matching token signatures" not in issue for issue in issues)


def test_aws_credentials_path_is_sensitive_in_archive_and_tracked_tree(tmp_path):
    scanner = _load_script("check_public_secrets_aws_credentials_path", SCRIPT_PATH)
    content = "[default]\nregion = eu-west-1\n"
    candidate = tmp_path / "bundle.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        archive.writestr("backup/.aws/credentials", content)
    assert any(
        "sensitive filename" in issue
        for issue in scanner.collect_issues(tmp_path, paths=[candidate])
    )
    tracked = tmp_path / ".aws" / "credentials"
    tracked.parent.mkdir()
    tracked.write_text(content, encoding="utf-8")
    assert any(
        "sensitive filename" in issue
        for issue in scanner.collect_issues(tmp_path, paths=[tracked])
    )


def test_lowercase_aws_secret_assignment_detected_without_leaking_value(tmp_path):
    scanner = _load_script("check_public_secrets_aws_lowercase", SCRIPT_PATH)
    synthetic = "A" * 40
    candidate = tmp_path / "settings.txt"
    candidate.write_text("aws_secret_" + "access_key = " + synthetic, encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any("AWS secret access key" in issue for issue in issues)
    assert all(synthetic not in issue for issue in issues)


def test_archive_member_token_in_name_blocks_without_echoing_token(tmp_path):
    scanner = _load_script("check_public_secrets_archive_name_token", SCRIPT_PATH)
    token = "AK" + "IA" + "Z" * 16
    candidate = tmp_path / "bundle.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        archive.writestr(f"documents/{token}.txt", "ordinary text")
    issues = scanner._scan_zip(candidate.read_bytes(), "bundle.zip")
    assert any("AWS access key" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


def test_tracked_filename_token_blocks_without_echoing_token(tmp_path):
    scanner = _load_script("check_public_secrets_outer_name_token", SCRIPT_PATH)
    token = "AK" + "IA" + "Z" * 16
    candidate = tmp_path / f"{token}.txt"
    candidate.write_text("ordinary text", encoding="utf-8")
    issues = scanner.collect_issues(tmp_path, paths=[candidate])
    assert any("AWS access key" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


def test_nested_zip_members_are_scanned(tmp_path):
    scanner = _load_script("check_public_secrets_nested_zip", SCRIPT_PATH)
    token = "AK" + "IA" + "N" * 16
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("secret.txt", token)
    candidate = tmp_path / "candidate.zip"
    with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested.zip", nested.getvalue())

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert any("candidate.zip!nested.zip!secret.txt" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


def test_prefixed_zip_is_detected_and_scanned(tmp_path):
    scanner = _load_script("check_public_secrets_prefixed_zip", SCRIPT_PATH)
    token = "AK" + "IA" + "P" * 16
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(
        archive_bytes, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("secret.txt", token)
    candidate = tmp_path / "self-extracting.bin"
    candidate.write_bytes(b"safe-prefix\n" + archive_bytes.getvalue())

    issues = scanner.collect_issues(tmp_path, paths=[candidate])

    assert any("self-extracting.bin!secret.txt" in issue for issue in issues)
    assert all(token not in issue for issue in issues)


def test_current_tracked_tree_is_clean_for_high_confidence_secrets():
    scanner = _load_script("check_public_secrets_current", SCRIPT_PATH)

    assert scanner.collect_issues(REPO_ROOT) == []


def test_current_tree_scan_ignores_vanished_untracked_files(monkeypatch, tmp_path):
    scanner = _load_script("check_public_secrets_vanished_untracked", SCRIPT_PATH)
    candidate = tmp_path / "ephemeral.coverage"
    candidate.write_text("temporary", encoding="utf-8")
    original_repository_paths = scanner.repository_paths

    def disappearing_paths(repo_root):
        paths = original_repository_paths(repo_root)
        candidate.unlink()
        return [candidate, *paths]

    monkeypatch.setattr(scanner, "repository_paths", disappearing_paths)

    issues = scanner.collect_issues(REPO_ROOT)

    assert all("ephemeral.coverage" not in issue for issue in issues)


def test_public_secret_scan_workflow_covers_all_repository_changes():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "python scripts/check_public_secrets.py" in normalized
    assert "pull_request:" in text
    assert "push:" in text
    assert "paths:" not in text
    assert "permissions:" in text
    assert "contents: read" in text
