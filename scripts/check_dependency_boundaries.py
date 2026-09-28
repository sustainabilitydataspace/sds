#!/usr/bin/env python3
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PATHS = {
    "deliverables_index": Path("deliverables/README.md"),
    "deliverables_register": Path("deliverables/deliverables-register.csv"),
    "extracted_readme": Path("data/extracted/README.md"),
}

SCAN_ROOTS = (
    Path("api"),
    Path("packages"),
    Path("cli"),
    Path("scripts"),
    Path("Makefile"),
)

TEXT_SUFFIXES = {".py", ".ps1", ".sh", ".yml", ".yaml", ".toml", ".ini"}
FORBIDDEN_DATASPACES_PATTERN = re.compile(r"\bdataspaces\b", re.IGNORECASE)
FORBIDDEN_IMPORT_PATTERNS = (
    re.compile(r"^\s*import\s+atomizer\b", re.MULTILINE),
    re.compile(r"^\s*from\s+atomizer\b", re.MULTILINE),
    re.compile(r"^\s*import\s+dataspaces\b", re.MULTILINE),
    re.compile(r"^\s*from\s+dataspaces\b", re.MULTILINE),
)
FORBIDDEN_API_RUNTIME_UNIT_HELPER_PATTERNS = (
    re.compile(r"^\s*import\s+sds_core\.units\b", re.MULTILINE),
    re.compile(r"^\s*from\s+sds_core\.units\b", re.MULTILINE),
)
CLI_ENTRYPOINT = Path("cli/sds_cli/src/sds_cli/__main__.py")


@dataclass(frozen=True)
class Issue:
    rule: str
    path: str
    message: str


def iter_scan_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    allowlist = {repo_root / "scripts" / "check_dependency_boundaries.py"}
    for relative_entry in SCAN_ROOTS:
        path = repo_root / relative_entry
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue
        for file_path in path.rglob("*"):
            if file_path.is_dir():
                continue
            if file_path in allowlist:
                continue
            if any(
                part in {".git", "__pycache__", ".venv", "tests"}
                for part in file_path.parts
            ):
                continue
            if file_path.suffix.lower() in TEXT_SUFFIXES:
                files.append(file_path)
    return files


@contextmanager
def _temporary_sys_path(paths: list[Path]) -> Iterator[None]:
    old_path = list(sys.path)
    for path in reversed(paths):
        path_str = str(path)
        if path.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)
    try:
        importlib.invalidate_caches()
        yield
    finally:
        sys.path[:] = old_path
        importlib.invalidate_caches()


def _load_cli_entrypoint(repo_root: Path):
    entrypoint = repo_root / CLI_ENTRYPOINT
    if not entrypoint.exists():
        return None

    spec = importlib.util.spec_from_file_location(
        "_sds_cli_boundary_entrypoint", entrypoint
    )
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load CLI entrypoint from {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _command_value(command: object, field_name: str) -> str:
    if isinstance(command, dict):
        value = command.get(field_name, "")
    else:
        value = getattr(command, field_name, "")
    return value if isinstance(value, str) else ""


def _collect_cli_passthrough_issues(repo_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    entrypoint = repo_root / CLI_ENTRYPOINT
    if not entrypoint.exists():
        return issues

    search_paths = [
        repo_root,
        repo_root / "packages" / "sds_core" / "src",
        repo_root / "cli" / "sds_cli" / "src",
        repo_root / "scripts",
    ]
    with _temporary_sys_path(search_paths):
        try:
            cli_module = _load_cli_entrypoint(repo_root)
        except Exception as exc:
            return [
                Issue(
                    rule="cli_passthrough_metadata",
                    path=str(CLI_ENTRYPOINT).replace("\\", "/"),
                    message=f"Could not import SDS CLI metadata: {exc}",
                )
            ]

        commands = getattr(cli_module, "PASSTHROUGH_COMMANDS", None)
        if not isinstance(commands, dict) or not commands:
            return [
                Issue(
                    rule="cli_passthrough_metadata",
                    path=str(CLI_ENTRYPOINT).replace("\\", "/"),
                    message="SDS CLI must expose PASSTHROUGH_COMMANDS metadata.",
                )
            ]

        for command_name, command in commands.items():
            module_name = _command_value(command, "module")
            script_path = _command_value(command, "script_path")
            if not module_name or not script_path:
                issues.append(
                    Issue(
                        rule="cli_passthrough_metadata",
                        path=str(CLI_ENTRYPOINT).replace("\\", "/"),
                        message=(
                            f"CLI command {command_name!r} lacks module or "
                            "script_path metadata."
                        ),
                    )
                )
                continue

            relative_script = Path(script_path)
            if relative_script.is_absolute():
                issues.append(
                    Issue(
                        rule="cli_passthrough_target",
                        path=script_path.replace("\\", "/"),
                        message=(
                            f"CLI command {command_name!r} uses an absolute "
                            "passthrough script path."
                        ),
                    )
                )
                continue

            target = repo_root / relative_script
            if not target.exists():
                issues.append(
                    Issue(
                        rule="cli_passthrough_target",
                        path=script_path.replace("\\", "/"),
                        message=(
                            f"CLI command {command_name!r} points to a missing "
                            f"script: {script_path}"
                        ),
                    )
                )
                continue

            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                issues.append(
                    Issue(
                        rule="cli_passthrough_import",
                        path=script_path.replace("\\", "/"),
                        message=(
                            f"CLI command {command_name!r} target module "
                            f"{module_name!r} failed to import: {exc}"
                        ),
                    )
                )
                continue

            if not callable(getattr(module, "main", None)):
                issues.append(
                    Issue(
                        rule="cli_passthrough_import",
                        path=script_path.replace("\\", "/"),
                        message=(
                            f"CLI command {command_name!r} target module "
                            f"{module_name!r} does not expose callable main()."
                        ),
                    )
                )

    return issues


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def collect_issues(repo_root: Path = REPO_ROOT) -> list[Issue]:
    repo_root = repo_root.resolve()
    issues: list[Issue] = []

    for name, relative_path in REQUIRED_PATHS.items():
        candidate = repo_root / relative_path
        if not candidate.exists():
            issues.append(
                Issue(
                    rule="required_path",
                    path=str(relative_path).replace("\\", "/"),
                    message=f"Missing required boundary artifact: {name}",
                )
            )

    extracted_readme = repo_root / "data" / "extracted" / "README.md"
    if extracted_readme.exists():
        text = extracted_readme.read_text(encoding="utf-8")
        if "non-canonical" not in text.lower():
            issues.append(
                Issue(
                    rule="extracted_policy",
                    path="data/extracted/README.md",
                    message="data/extracted/README.md must classify the directory as non-canonical.",
                )
            )

    for path in iter_scan_files(repo_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if FORBIDDEN_DATASPACES_PATTERN.search(text):
            issues.append(
                Issue(
                    rule="dataspaces_reference",
                    path=str(path.relative_to(repo_root)).replace("\\", "/"),
                    message="Active code/build files must not reference the dataspaces repo.",
                )
            )
        for pattern in FORBIDDEN_IMPORT_PATTERNS:
            if pattern.search(text):
                issues.append(
                    Issue(
                        rule="forbidden_import",
                        path=str(path.relative_to(repo_root)).replace("\\", "/"),
                        message=f"Forbidden dependency import matched: {pattern.pattern}",
                    )
                )
        if _is_relative_to(path, repo_root / "api" / "src"):
            for pattern in FORBIDDEN_API_RUNTIME_UNIT_HELPER_PATTERNS:
                if pattern.search(text):
                    issues.append(
                        Issue(
                            rule="api_runtime_unit_helper_import",
                            path=str(path.relative_to(repo_root)).replace("\\", "/"),
                            message=(
                                "Production API runtime must use the DB-backed "
                                "UnitService/UnitRepository, not sds_core.units."
                            ),
                        )
                    )
    issues.extend(_collect_cli_passthrough_issues(repo_root))
    return issues


def main() -> int:
    issues = collect_issues()
    print("Dependency boundary check")
    print("=========================")
    if not issues:
        print("PASS")
        return 0

    for issue in issues:
        print(f"[FAIL] {issue.rule}: {issue.path} :: {issue.message}")
    print("")
    print("FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
