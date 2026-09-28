"""Install the local API CI pre-push hook for this checkout."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

MANAGED_MARKER = "SDS managed API CI pre-push hook"
HOOK_BODY = f"""#!/bin/sh
# {MANAGED_MARKER}
REPO_ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$REPO_ROOT" || exit 1

if [ -x "api/.venv/Scripts/python.exe" ]; then
  PYTHON="api/.venv/Scripts/python.exe"
elif [ -x "api/.venv/bin/python" ]; then
  PYTHON="api/.venv/bin/python"
else
  PYTHON="python"
fi

exec "$PYTHON" scripts/api_ci_pre_push.py "$@"
"""


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return Path(result.stdout.strip())


def install_hook(root: Path, *, force: bool = False) -> Path:
    hook_path = root / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if MANAGED_MARKER not in existing and not force:
            raise RuntimeError(
                f"Refusing to overwrite unmanaged hook: {hook_path}. "
                "Re-run with --force after preserving its behavior."
            )

    hook_path.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    current_mode = hook_path.stat().st_mode
    hook_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def main(argv: list[str]) -> int:
    force = "--force" in argv[1:]
    try:
        hook_path = install_hook(repo_root(), force=force)
    except Exception as exc:
        print(f"Failed to install API CI pre-push hook: {exc}", file=sys.stderr)
        return 1

    print(f"Installed API CI pre-push hook: {hook_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
