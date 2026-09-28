"""Local pre-push guard for the API CI workflow."""

from __future__ import annotations

import subprocess
import sys
from typing import Iterable, NamedTuple

ZERO_SHA = "0" * 40
API_CI_EXACT_PATHS = {
    ".github/workflows/api-ci.yml",
    ".github/workflows/docker-quickstart-smoke.yml",
    "Makefile",
    "README.md",
    "docs/quality/acceptance_gates.md",
    "scripts/api_ci_pre_push.py",
    "scripts/install_api_ci_pre_push_hook.py",
}
API_CI_PREFIXES = (
    "api/",
    "packages/",
    "semantics/",
    "governance/",
    "configs/",
    "deliverables/",
    "scripts/",
)


class RefUpdate(NamedTuple):
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def api_ci_relevant_path(path: str) -> bool:
    normalized = normalize_path(path)
    return normalized in API_CI_EXACT_PATHS or normalized.startswith(API_CI_PREFIXES)


def parse_pre_push_updates(stdin_text: str) -> list[RefUpdate]:
    updates: list[RefUpdate] = []
    for line in stdin_text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"Unexpected pre-push input line: {line!r}")
        updates.append(RefUpdate(*fields))
    return updates


def is_main_branch_push(update: RefUpdate) -> bool:
    return update.remote_ref == "refs/heads/main" and update.local_sha != ZERO_SHA


def requires_api_ci_for_changed_files(
    update: RefUpdate, changed_files: Iterable[str]
) -> bool:
    return is_main_branch_push(update) and any(
        api_ci_relevant_path(path) for path in changed_files
    )


def run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def rev_exists(rev: str) -> bool:
    return (
        run_git(["rev-parse", "--verify", "--quiet", rev], check=False).returncode == 0
    )


def branch_name(remote_ref: str) -> str | None:
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix):
        return None
    return remote_ref[len(prefix) :]


def diff_names(base_rev: str, head_rev: str) -> list[str]:
    result = run_git(["diff", "--name-only", base_rev, head_rev])
    return [line for line in result.stdout.splitlines() if line.strip()]


def last_commit_names(head_rev: str) -> list[str]:
    result = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", head_rev])
    return [line for line in result.stdout.splitlines() if line.strip()]


def changed_files_for_update(update: RefUpdate, remote_name: str) -> list[str]:
    if update.local_sha == ZERO_SHA:
        return []
    if update.remote_sha != ZERO_SHA:
        return diff_names(update.remote_sha, update.local_sha)

    pushed_branch = branch_name(update.remote_ref)
    if pushed_branch:
        remote_tracking_ref = f"refs/remotes/{remote_name}/{pushed_branch}"
        if rev_exists(remote_tracking_ref):
            return diff_names(remote_tracking_ref, update.local_sha)

    return last_commit_names(update.local_sha)


def requires_api_ci(updates: Iterable[RefUpdate], remote_name: str) -> bool:
    return any(
        requires_api_ci_for_changed_files(
            update, changed_files_for_update(update, remote_name)
        )
        for update in updates
    )


def run_api_ci_local() -> int:
    return subprocess.run(["make", "api-ci-local"]).returncode


def main(argv: list[str]) -> int:
    remote_name = argv[1] if len(argv) > 1 else "origin"
    updates = parse_pre_push_updates(sys.stdin.read())

    if not requires_api_ci(updates, remote_name):
        print("SDS pre-push: API CI inputs unchanged for main; skipping local API CI.")
        return 0

    print("SDS pre-push: API CI inputs changed on main; running local API CI.")
    return run_api_ci_local()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
