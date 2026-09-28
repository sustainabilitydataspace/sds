#!/usr/bin/env python3
"""Fail closed on high-risk constructs in privileged repository surfaces."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: re.Pattern[str]
    message: str


RULES = (
    Rule(
        "PIPE_TO_SHELL",
        re.compile(
            r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b",
            re.IGNORECASE,
        ),
        "remote content must not be piped directly to a shell",
    ),
    Rule(
        "POWERSHELL_DYNAMIC_EXECUTION",
        re.compile(r"\b(?:Invoke-Expression|iex)\b", re.IGNORECASE),
        "PowerShell dynamic expression execution is forbidden",
    ),
    Rule(
        "WORLD_WRITABLE_MODE",
        re.compile(r"\bchmod\s+(?:-[A-Za-z]+\s+)*777\b", re.IGNORECASE),
        "world-writable executable or deployment files are forbidden",
    ),
    Rule(
        "PRIVILEGED_CONTAINER",
        re.compile(r"(?mi)^\s*privileged\s*:\s*true\s*(?:#.*)?$"),
        "privileged containers are forbidden",
    ),
    Rule(
        "HOST_NAMESPACE",
        re.compile(r"(?mi)^\s*(?:network_mode|pid)\s*:\s*[\"']?host[\"']?\s*(?:#.*)?$"),
        "host network or PID namespaces are forbidden",
    ),
    Rule(
        "DOCKER_SOCKET_MOUNT",
        re.compile(r"/var/run/docker\.sock", re.IGNORECASE),
        "mounting the Docker control socket is forbidden",
    ),
    Rule(
        "WORKFLOW_WRITE_ALL",
        re.compile(r"(?mi)^\s*permissions\s*:\s*write-all\s*(?:#.*)?$"),
        "GitHub workflow write-all permissions are forbidden",
    ),
)


def scan_text(path: Path, text: str) -> list[str]:
    """Return rule identifiers for high-risk constructs in one text surface."""
    del path  # Reserved for future path-specific policy without changing the API.
    return [rule.rule_id for rule in RULES if rule.pattern.search(text)]


def _candidate_paths(root: Path) -> list[Path]:
    patterns = (
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "Dockerfile*",
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "api/Dockerfile*",
        "api/docker-compose*.yml",
        "api/docker-compose*.yaml",
        "api/scripts/**/*.sh",
        "api/scripts/**/*.ps1",
        "scripts/**/*.sh",
        "scripts/**/*.ps1",
    )
    return sorted(
        {path for pattern in patterns for path in root.glob(pattern) if path.is_file()}
    )


def check_repository(root: Path) -> list[str]:
    """Scan privileged surfaces and validate Bash syntax without executing them."""
    issues: list[str] = []
    for path in _candidate_paths(root):
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            issues.append(
                f"{relative}: unreadable privileged surface ({type(exc).__name__})"
            )
            continue

        for rule_id in scan_text(path, text):
            issues.append(f"{relative}: {rule_id}")

        if path.suffix.casefold() == ".sh":
            result = subprocess.run(  # noqa: S603
                ["bash", "-n", str(path)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                issues.append(f"{relative}: BASH_SYNTAX_INVALID")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    root = args.root.resolve()

    issues = check_repository(root)
    if issues:
        print("Privileged surface security check: FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("Privileged surface security check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
