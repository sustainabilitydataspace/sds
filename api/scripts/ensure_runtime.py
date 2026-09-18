"""Create/repair the local SDS API Python runtime before starting commands."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = API_ROOT / ".venv"


def _venv_python() -> Path | None:
    candidates = [
        VENV_DIR / "bin" / "python",
        VENV_DIR / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _run(command: list[str]) -> None:
    subprocess.check_call(command, cwd=API_ROOT)


def _is_supported_python(python_path: Path) -> bool:
    probe = subprocess.run(
        [
            str(python_path),
            "-c",
            "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}'); raise SystemExit(0 if v.major == 3 and 10 <= v.minor < 14 else 1)",
        ],
        cwd=API_ROOT,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        version = (probe.stdout or probe.stderr or "unknown").strip()
        print(
            f"ERROR: SDS API local Python supports 3.10-3.13. Current: {version}",
            file=sys.stderr,
        )
        return False
    return True


def _has_uvicorn(python_path: Path) -> bool:
    probe = subprocess.run(
        [str(python_path), "-c", "import uvicorn"],
        cwd=API_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def ensure_runtime() -> Path:
    python_path = _venv_python()
    if python_path is None:
        print(f"Creating {VENV_DIR} with {sys.executable}...")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
        python_path = _venv_python()

    if python_path is None:
        raise SystemExit("ERROR: failed to create or find .venv Python.")

    if not _is_supported_python(python_path):
        raise SystemExit(1)

    if not _has_uvicorn(python_path):
        print(f"Missing runtime packages in {python_path}; installing requirements.txt...")
        _run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
        _run([str(python_path), "-m", "pip", "install", "-r", "requirements.txt"])

    return python_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Start uvicorn after ensuring runtime dependencies.")
    parser.add_argument("--reload", action="store_true", help="Pass --reload to uvicorn.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8090")
    args = parser.parse_args()

    python_path = ensure_runtime()

    if not args.start:
        return 0

    command = [str(python_path), "-m", "uvicorn", "src.api.main:app"]
    if args.reload:
        command.append("--reload")
    command.extend(["--host", args.host, "--port", str(args.port)])
    return subprocess.call(command, cwd=API_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
