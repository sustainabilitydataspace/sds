from __future__ import annotations

import io
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2048
MAX_ARCHIVE_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_ARCHIVE_DEPTH = 3

SECRET_PATTERNS = (
    (
        "private key material",
        re.compile(rb"-----BEGIN (?:ENCRYPTED |RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("AWS access key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS temporary access key", re.compile(rb"\bASIA[0-9A-Z]{16}\b")),
    (
        "AWS secret access key",
        re.compile(
            rb"\bAWS_SECRET_ACCESS_KEY[\"']?[ \t]*[=:][ \t]*[\"']?[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])",
            re.IGNORECASE,
        ),
    ),
    (
        "database URL with embedded password",
        re.compile(
            rb"\b(?:[A-Z0-9_]*DATABASE_URL)[\"']?[ \t]*[=:][ \t]*[\"']?postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^:/@\s\"']+:[^@/\s\"']+@[^/\s\"']+",
            re.IGNORECASE,
        ),
    ),
    (
        "database URL with embedded password",
        re.compile(
            rb"\bpostgres(?:ql)?(?:\+[a-z0-9_]+)?://[^:/@\s\"']+:[^@/\s\"']+@[^/\s\"']+",
            re.IGNORECASE,
        ),
    ),
    (
        "Bearer JWT",
        re.compile(rb"\bBearer[ \t]+eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("GitHub token", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,255}\b")),
    ("OpenAI-style token", re.compile(rb"\bsk-[A-Za-z0-9]{48,}\b")),
    ("OpenAI-style token", re.compile(rb"\bsk-proj-[A-Za-z0-9_-]{32,}\b")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", re.compile(rb"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
)

SENSITIVE_FILENAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SENSITIVE_SUFFIXES = {".p12", ".pfx"}


def _sensitive_path(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return (
        any(part in SENSITIVE_FILENAMES for part in parts)
        or path.suffix.casefold() in SENSITIVE_SUFFIXES
        or parts[-2:] == (".aws", "credentials")
    )


def repository_paths(repo_root: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return [
        repo_root / item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _tracked_path(repo_root: Path, display_path: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--error-unmatch",
            "--",
            display_path,
        ],
        capture_output=True,
    )
    return completed.returncode == 0


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_display_path(display_path: str) -> str:
    redacted = display_path.encode("utf-8", errors="surrogateescape")
    for _, pattern in SECRET_PATTERNS:
        redacted = pattern.sub(b"[REDACTED]", redacted)
    text = redacted.decode("utf-8", errors="replace")
    return re.sub(
        r"[\x00-\x1f\x7f]", lambda match: f"\\x{ord(match.group()):02x}", text
    )


def _line_number(content: bytes, match_start: int) -> int:
    return content.count(b"\n", 0, match_start) + 1


def _is_placeholder_database_url(match: bytes) -> bool:
    authority = match.split(b"://", 1)[1].split(b"@", 1)[0]
    password = authority.split(b":", 1)[1]
    if password in {b"[REDACTED]", b"***"}:
        return True
    return any(
        re.fullmatch(pattern, password)
        for pattern in (
            rb"\$\{[A-Za-z_][A-Za-z0-9_]*\}",
            rb"\$[A-Za-z_][A-Za-z0-9_]*",
            rb"\{[A-Za-z_][A-Za-z0-9_]*\}",
            rb"<[A-Za-z_][A-Za-z0-9_-]*>",
        )
    )


def _scan_content(content: bytes, display_path: str) -> list[str]:
    issues: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            if label == "database URL with embedded password" and _is_placeholder_database_url(
                match.group(0)
            ):
                continue
            issue = f"{label} detected at {display_path}:{_line_number(content, match.start())}"
            if issue not in issues:
                issues.append(issue)
            break
    return issues


def _scan_zip(
    content: bytes,
    display_path: str,
    *,
    depth: int = 0,
    budget: dict[str, int] | None = None,
) -> list[str]:
    issues: list[str] = []
    if depth > MAX_ARCHIVE_DEPTH:
        return [f"{display_path}:0: nested archive depth exceeded"]
    if budget is None:
        budget = {"members": 0, "expanded_bytes": 0}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            budget["members"] += len(members)
            if budget["members"] > MAX_ARCHIVE_MEMBERS:
                return [f"{display_path}:0: archive member budget exceeded"]
            names: set[str] = set()
            for member in members:
                normalized_name = member.filename.replace("\\", "/")
                member_path = Path(normalized_name)
                member_display = _safe_display_path(f"{display_path}!{member.filename}")
                if (
                    normalized_name in names
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.flag_bits & 0x1
                ):
                    return [f"{display_path}:0: unsafe archive member inventory"]
                names.add(normalized_name)
                mode = (member.external_attr >> 16) & 0o170000
                if mode and mode not in {stat.S_IFREG, stat.S_IFDIR}:
                    return [f"{display_path}:0: unsafe archive member type"]
                if _sensitive_path(member_path):
                    issues.append(f"sensitive filename is public: {member_display}")
                issues.extend(
                    _scan_content(
                        normalized_name.encode("utf-8", errors="surrogateescape"),
                        member_display,
                    )
                )
                if member.is_dir():
                    continue
                budget["expanded_bytes"] += member.file_size
                if (
                    member.file_size > MAX_FILE_BYTES
                    or budget["expanded_bytes"] > MAX_ARCHIVE_EXPANDED_BYTES
                ):
                    return [f"{display_path}:0: archive expansion budget exceeded"]
                if member.compress_size == 0:
                    ratio = member.file_size if member.file_size else 1
                else:
                    ratio = member.file_size / member.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    return [f"{display_path}:0: archive compression ratio exceeded"]
                with archive.open(member) as member_file:
                    content = member_file.read(MAX_FILE_BYTES + 1)
                if len(content) != member.file_size:
                    return [f"{display_path}:0: archive member size mismatch"]
                issues.extend(_scan_content(content, member_display))
                if zipfile.is_zipfile(io.BytesIO(content)):
                    issues.extend(
                        _scan_zip(
                            content,
                            member_display,
                            depth=depth + 1,
                            budget=budget,
                        )
                    )
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return [f"cannot inspect archive {display_path}: {type(exc).__name__}"]
    return issues


def collect_issues(
    repo_root: Path = REPO_ROOT,
    *,
    paths: Iterable[Path] | None = None,
) -> list[str]:
    issues: list[str] = []
    candidates = list(paths) if paths is not None else repository_paths(repo_root)

    for path in candidates:
        raw_display_path = _display_path(path, repo_root)
        display_path = _safe_display_path(raw_display_path)
        issues.extend(
            _scan_content(
                raw_display_path.encode("utf-8", errors="surrogateescape"),
                display_path,
            )
        )
        if _sensitive_path(path):
            issues.append(f"sensitive filename is public: {display_path}")
            continue
        try:
            metadata = path.lstat()
        except OSError as exc:
            if paths is None and not _tracked_path(repo_root, raw_display_path):
                continue
            issues.append(f"cannot inspect {display_path}: {type(exc).__name__}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            issues.append(f"{display_path}:0: tracked path is not a regular file")
            continue
        if metadata.st_nlink != 1:
            issues.append(f"{display_path}:0: tracked file has multiple hard links")
            continue
        if metadata.st_size > MAX_FILE_BYTES:
            issues.append(f"{display_path}:0: unscanned oversized file")
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            if paths is None and not _tracked_path(repo_root, raw_display_path):
                continue
            issues.append(f"cannot inspect {display_path}: {type(exc).__name__}")
            continue
        issues.extend(_scan_content(content, display_path))
        if zipfile.is_zipfile(io.BytesIO(content)):
            issues.extend(_scan_zip(content, display_path))
    return issues


def main() -> int:
    issues = collect_issues()
    print("Public secret scan")
    print("==================")
    if issues:
        for issue in issues:
            print(f"[FAIL] {issue}")
        print("\nFAIL")
        return 1
    print("[OK] no high-confidence secrets or private credential files detected")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
