#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPO_ROOT / "api"
DEFAULT_DEST_DIR = REPO_ROOT / "data" / "atomizer"
DEFAULT_MANIFEST_PATH = DEFAULT_DEST_DIR / "manifest.json"
DEFAULT_CHECKSUM_PATH = DEFAULT_DEST_DIR / "MANIFEST.sha256"
ATOMIZER_EXPORT_DIR_ENV = "ATOMIZER_EXPORT_DIR"
CONTRACT_VERSION = "2026-04-13"
REGISTER_FILENAME = "sds_dataset_register.csv"
REGISTER_JSON_FILENAME = "sds_dataset_register.json"
CALCULATION_CONTRACT_FILENAME = "sds_calculation_contract.json"
REGISTER_REQUIRED_HEADERS = (
    "identifier",
    "title",
    "indicator",
    "description",
    "dimension",
    "unitName",
    "unitType",
    "periodicity",
    "periodType",
    "sourceRef",
    "codeESRS",
    "codeGRI",
    "codeGRI_expanded",
    "evidencePath",
    "sourceRow",
    "owner",
    "accessRights",
    "validationMethod",
    "doubleMateriality",
    "valueType",
)


@dataclass(frozen=True)
class ExportSpec:
    filename: str
    required_headers: tuple[str, ...]
    description: str
    allowed_headers: tuple[str, ...] | None = None


def _load_value_csv_contract() -> tuple[tuple[str, ...], tuple[str, ...]]:
    api_root = str(API_ROOT)
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    module_path = API_ROOT / "src" / "services" / "value_csv_import.py"
    spec = importlib.util.spec_from_file_location(
        "value_csv_import_contract",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load SDS value CSV contract from {module_path}")
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Unable to import SDS value CSV contract from {API_ROOT}"
        ) from exc
    return tuple(module.REQUIRED_VALUE_COLUMNS), tuple(module.ALLOWED_VALUE_COLUMNS)


EXPORT_SPECS = (
    ExportSpec(
        filename="framework_datapoints.csv",
        required_headers=(
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "Label",
            "Description",
            "DataType",
            "DefaultUnit",
            "Topic",
        ),
        description="Canonical framework datapoints exported from Atomizer.",
    ),
    ExportSpec(
        filename="atomized_variables.csv",
        required_headers=(
            "FrameworkID",
            "FrameworkVersion",
            "DatapointCode",
            "AtomizedID",
            "Label",
            "Description",
            "UnitName",
            "UnitType",
            "IsActivityData",
            "IsCalculated",
            "CalculationLogic",
        ),
        description="Canonical atomized variables exported from Atomizer.",
    ),
)

REGISTER_EXPORT_SPEC = ExportSpec(
    filename=REGISTER_FILENAME,
    required_headers=REGISTER_REQUIRED_HEADERS,
    description="Canonical SDS dataset register package emitted by Atomizer.",
)
VALUES_FILENAME = "sds_values.csv"
VALUES_REQUIRED_HEADERS, VALUES_ALLOWED_HEADERS = _load_value_csv_contract()
VALUES_EXPORT_SPEC = ExportSpec(
    filename=VALUES_FILENAME,
    required_headers=VALUES_REQUIRED_HEADERS,
    description="Optional operational values package aligned to the SDS indicator register.",
    allowed_headers=VALUES_ALLOWED_HEADERS,
)
CALCULATION_CONTRACT_EXPORT_SPEC = ExportSpec(
    filename=CALCULATION_CONTRACT_FILENAME,
    required_headers=(),
    description="Optional calculation semantics package aligned to the SDS indicator register.",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_csv_summary(
    path: Path,
    required_headers: Iterable[str],
    allowed_headers: Iterable[str] | None = None,
) -> tuple[list[str], int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} is empty or has no header row")
        headers = list(reader.fieldnames)
        missing = [header for header in required_headers if header not in headers]
        if missing:
            raise ValueError(f"{path} is missing required headers: {', '.join(missing)}")
        if allowed_headers is not None:
            allowed = set(allowed_headers)
            unsupported = [header for header in headers if header not in allowed]
            if unsupported:
                raise ValueError(f"{path} has unsupported headers: {', '.join(unsupported)}")
        row_count = sum(1 for _ in reader)
    return headers, row_count


def validate_export_file(path: Path, spec: ExportSpec) -> None:
    if path.suffix.lower() == ".csv":
        load_csv_summary(path, spec.required_headers, spec.allowed_headers)
        return
    if path.suffix.lower() == ".json" and path.name == CALCULATION_CONTRACT_FILENAME:
        load_calculation_contract_summary(path)
        return
    if not path.exists():
        raise FileNotFoundError(f"Missing Atomizer export: {path}")


def load_calculation_contract_summary(path: Path) -> tuple[list[str], int]:
    if not path.exists():
        raise FileNotFoundError(f"Missing Atomizer export: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "contract_version" not in payload:
        raise ValueError(f"{path} is missing contract_version")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError(f"{path} is missing nodes list")
    return list(payload.keys()), len(nodes)


def detect_contract_mode(directory: Path) -> str:
    register_path = directory / REGISTER_FILENAME
    if register_path.exists():
        return "register"

    legacy_files = [directory / spec.filename for spec in EXPORT_SPECS]
    if all(path.exists() for path in legacy_files):
        return "legacy"

    missing = [path.name for path in [register_path, *legacy_files] if not path.exists()]
    raise FileNotFoundError(
        "Missing Atomizer contract files. Expected either "
        f"'{REGISTER_FILENAME}' or the legacy pair. Missing: {', '.join(missing)}"
    )


def resolve_export_specs(source_dir: Path) -> tuple[str, list[ExportSpec]]:
    contract_mode = detect_contract_mode(source_dir)
    if contract_mode == "register":
        specs = [REGISTER_EXPORT_SPEC]
        if (source_dir / REGISTER_JSON_FILENAME).exists():
            specs.append(
                ExportSpec(
                    filename=REGISTER_JSON_FILENAME,
                    required_headers=(),
                    description="Optional JSON sibling for the canonical SDS dataset register package.",
                )
            )
        if (source_dir / CALCULATION_CONTRACT_FILENAME).exists():
            specs.append(CALCULATION_CONTRACT_EXPORT_SPEC)
        if (source_dir / VALUES_FILENAME).exists():
            specs.append(VALUES_EXPORT_SPEC)
        return contract_mode, specs
    return contract_mode, list(EXPORT_SPECS)


def relative_or_absolute(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def find_atomizer_repo_root(source_dir: Path | None) -> Path | None:
    if source_dir is None:
        return None
    current = source_dir.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "README.md").exists():
            return candidate
    return None


def parse_atomizer_version(repo_root: Path | None) -> str | None:
    if repo_root is None:
        return None
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version = "):
            return stripped.split("=", 1)[1].strip().strip('"')
    return None


def build_file_manifest(path: Path, spec: ExportSpec, repo_root: Path) -> dict[str, object]:
    headers: list[str] = []
    row_count = 0
    if path.suffix.lower() == ".csv":
        headers, row_count = load_csv_summary(path, spec.required_headers, spec.allowed_headers)
    elif path.name == CALCULATION_CONTRACT_FILENAME:
        headers, row_count = load_calculation_contract_summary(path)
    return {
        "filename": spec.filename,
        "path": relative_or_absolute(path, repo_root),
        "description": spec.description,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_for(path),
        "row_count": row_count,
        "required_headers": list(spec.required_headers),
        "headers": headers,
    }


def copy_exports(source_dir: Path, dest_dir: Path, specs: Iterable[ExportSpec]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        source_path = source_dir / spec.filename
        if not source_path.exists():
            raise FileNotFoundError(f"Missing Atomizer export: {source_path}")
        shutil.copy2(source_path, dest_dir / spec.filename)


def write_manifest(
    *,
    dest_dir: Path,
    manifest_path: Path,
    checksum_path: Path,
    source_dir: Path | None,
    repo_root: Path,
    contract_mode: str,
    specs: list[ExportSpec],
) -> dict[str, object]:
    source_repo_root = find_atomizer_repo_root(source_dir)
    if contract_mode == "register":
        canonical_inputs = [REGISTER_FILENAME]
        if any(spec.filename == CALCULATION_CONTRACT_FILENAME for spec in specs):
            canonical_inputs.append(CALCULATION_CONTRACT_FILENAME)
        if any(spec.filename == VALUES_FILENAME for spec in specs):
            canonical_inputs.append(VALUES_FILENAME)
    else:
        canonical_inputs = [spec.filename for spec in EXPORT_SPECS]
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "manifest_generated_at": utc_now_iso(),
        "source_project": "atomizer",
        "canonical_dir": relative_or_absolute(dest_dir, repo_root),
        "contract_mode": contract_mode,
        "policy": {
            "canonical_inputs": canonical_inputs,
            "non_canonical_derived_dirs": ["data/processed", "data/extracted"],
            "preferred_boundary": REGISTER_FILENAME,
            "legacy_compatibility_boundary": [
                spec.filename for spec in EXPORT_SPECS
            ] if contract_mode == "register" else [],
            "adapter_script": "scripts/prepare_atomizer_for_import.py" if contract_mode == "legacy" else None,
        },
        "sync": {
            "mode": "source_sync" if source_dir else "local_snapshot_refresh",
            "source_dir": relative_or_absolute(source_dir, repo_root),
            "source_env_var": ATOMIZER_EXPORT_DIR_ENV,
            "source_repo_root": relative_or_absolute(source_repo_root, repo_root),
            "atomizer_project_version": parse_atomizer_version(source_repo_root),
        },
        "files": [build_file_manifest(dest_dir / spec.filename, spec, repo_root) for spec in specs],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    checksum_lines = [f"{item['sha256']} *{item['filename']}" for item in manifest["files"]]
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return manifest


def sync_exports(
    *,
    source_dir: Path | None,
    dest_dir: Path = DEFAULT_DEST_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    checksum_path: Path = DEFAULT_CHECKSUM_PATH,
    repo_root: Path = REPO_ROOT,
    dry_run: bool = False,
) -> dict[str, object]:
    source_dir = source_dir.resolve() if source_dir else None
    dest_dir = dest_dir.resolve()
    manifest_path = manifest_path.resolve()
    checksum_path = checksum_path.resolve()
    repo_root = repo_root.resolve()

    local_contract_mode: str | None = None
    specs: list[ExportSpec]
    if source_dir is not None:
        local_contract_mode, specs = resolve_export_specs(source_dir)
        for spec in specs:
            validate_export_file(source_dir / spec.filename, spec)
        if dry_run:
            return {
                "contract_version": CONTRACT_VERSION,
                "contract_mode": local_contract_mode,
                "dry_run": True,
                "source_dir": str(source_dir),
                "dest_dir": str(dest_dir),
                "validated_files": [spec.filename for spec in specs],
            }
        copy_exports(source_dir, dest_dir, specs)
    else:
        local_contract_mode, specs = resolve_export_specs(dest_dir)

    for spec in specs:
        target_path = dest_dir / spec.filename
        if not target_path.exists():
            raise FileNotFoundError(
                f"Missing local Atomizer export: {target_path}. Run with --source-dir or restore local package files."
            )
        validate_export_file(target_path, spec)

    if dry_run:
        # Local-snapshot validation must NOT write the manifest/checksum: --dry-run is a
        # read-only validation contract for both branches (codex F12 M1).
        return {
            "contract_version": CONTRACT_VERSION,
            "contract_mode": local_contract_mode,
            "dry_run": True,
            "source_dir": str(source_dir) if source_dir else None,
            "dest_dir": str(dest_dir),
            "validated_files": [spec.filename for spec in specs],
        }

    return write_manifest(
        dest_dir=dest_dir,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        source_dir=source_dir,
        repo_root=repo_root,
        contract_mode=local_contract_mode,
        specs=specs,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh a local Atomizer export package cache. The default data/atomizer cache is git-ignored."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing Atomizer export CSVs. "
            f"If omitted, the script only validates the current local snapshot. "
            f"The {ATOMIZER_EXPORT_DIR_ENV} environment variable is also supported."
        ),
    )
    parser.add_argument("--dest-dir", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--checksum-path", type=Path, default=DEFAULT_CHECKSUM_PATH)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source_dir = args.source_dir or (Path(os.environ[ATOMIZER_EXPORT_DIR_ENV]) if os.environ.get(ATOMIZER_EXPORT_DIR_ENV) else None)

    try:
        manifest = sync_exports(
            source_dir=source_dir,
            dest_dir=args.dest_dir,
            manifest_path=args.manifest_path,
            checksum_path=args.checksum_path,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("PASS (dry-run)")
        if source_dir:
            print(f"Validated Atomizer source: {source_dir}")
        print(f"Validated local contract directory: {args.dest_dir}")
        return 0

    print("PASS")
    if source_dir:
        print(f"Synced from: {source_dir}")
    else:
        print("Refreshed manifest from the current local snapshot.")
    print(f"Manifest: {args.manifest_path}")
    print(f"Checksums: {args.checksum_path}")
    for item in manifest["files"]:
        print(f"- {item['filename']}: {item['row_count']} rows, {item['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
