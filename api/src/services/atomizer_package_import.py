"""Helpers for Atomizer SDS package import orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CALCULATION_CONTRACT_FILENAME = "sds_calculation_contract.json"
SEMANTICS_MODES = ("required", "optional", "off")
STANDARD_VERSIONING_SCHEMA_VERSION = "standard-versioning-v1"


class AtomizerPackageImportError(ValueError):
    """Raised when package-level import options are inconsistent."""


@dataclass(frozen=True)
class CalculationContractSelection:
    """Resolved calculation contract import decision for a package."""

    enabled: bool
    required: bool
    path: Path | None
    reason: str


@dataclass(frozen=True)
class StandardVersioningValidation:
    """Validated standard-versioning block from an Atomizer SDS package."""

    present: bool
    required: bool
    valid: bool
    standard_id: str | None = None
    release_id: str | None = None
    release_state: str | None = None
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "required": self.required,
            "valid": self.valid,
            "standard_id": self.standard_id,
            "release_id": self.release_id,
            "release_state": self.release_state,
            "errors": list(self.errors),
        }


def resolve_calculation_contract_selection(
    *,
    package_dir: Path,
    explicit_path: Path | None = None,
    skip: bool = False,
    require: bool = False,
    semantics_mode: str = "optional",
    repo_root: Path | None = None,
) -> CalculationContractSelection:
    """Resolve whether a package calculation contract should be imported."""

    if semantics_mode not in SEMANTICS_MODES:
        raise AtomizerPackageImportError(
            f"semantics_mode must be one of: {', '.join(SEMANTICS_MODES)}"
        )
    if skip and require:
        raise AtomizerPackageImportError(
            "--skip-calculation-contract cannot be combined with required semantics"
        )
    if skip or semantics_mode == "off":
        return CalculationContractSelection(
            enabled=False,
            required=False,
            path=None,
            reason="disabled",
        )

    required = require or semantics_mode == "required" or explicit_path is not None
    candidates: list[Path]
    if explicit_path is not None:
        candidates = [explicit_path]
        if not explicit_path.is_absolute():
            candidates.append(package_dir / explicit_path)
            if repo_root is not None:
                candidates.append(repo_root / explicit_path)
    else:
        candidates = [package_dir / CALCULATION_CONTRACT_FILENAME]

    for candidate in candidates:
        if candidate.exists():
            return CalculationContractSelection(
                enabled=True,
                required=required,
                path=candidate.resolve(),
                reason="found",
            )

    missing_path = candidates[-1].resolve()
    if required:
        raise AtomizerPackageImportError(
            f"calculation contract JSON not found: {missing_path}"
        )

    return CalculationContractSelection(
        enabled=False,
        required=False,
        path=None,
        reason=f"missing_optional:{missing_path}",
    )


def validate_standard_versioning_manifest(
    package_dir: Path,
    *,
    require: bool = False,
) -> StandardVersioningValidation:
    """Validate optional Atomizer standard-versioning package metadata.

    Legacy Atomizer packages without a manifest remain importable unless
    ``require`` is true. When present, the block must expose the release anchor,
    source hashes, delta intent, lineage map, and dry-run-first activation policy
    that SDS needs before it can safely reason about reporting-standard changes.
    """

    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        if require:
            return _standard_versioning_error(
                required=require,
                message=f"manifest.json not found: {manifest_path}",
            )
        return StandardVersioningValidation(
            present=False,
            required=False,
            valid=True,
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _standard_versioning_error(
            required=require,
            message=f"manifest.json is invalid: {exc}",
        )
    if not isinstance(manifest, Mapping):
        return _standard_versioning_error(
            required=require,
            message="manifest.json must be a JSON object",
        )

    versioning = manifest.get("standard_versioning")
    if versioning is None:
        if require:
            return _standard_versioning_error(
                required=require,
                message="manifest.json is missing standard_versioning",
            )
        return StandardVersioningValidation(
            present=False,
            required=False,
            valid=True,
        )
    if not isinstance(versioning, Mapping):
        return _standard_versioning_error(
            required=require,
            present=True,
            message="standard_versioning must be a JSON object",
        )

    errors: list[str] = []
    if versioning.get("schema_version") != STANDARD_VERSIONING_SCHEMA_VERSION:
        errors.append(
            "standard_versioning.schema_version must be "
            f"{STANDARD_VERSIONING_SCHEMA_VERSION}"
        )

    release = _object_field(versioning, "release_manifest", errors)
    standard_id = _required_str(release, "standard_id", errors, "release_manifest")
    release_id = _required_str(release, "release_id", errors, "release_manifest")
    release_state = _required_str(
        release,
        "release_state",
        errors,
        "release_manifest",
    )
    if release_state and release_state not in {
        "draft",
        "active",
        "superseded",
        "withdrawn",
        "archived",
    }:
        errors.append("release_manifest.release_state is not supported")
    source_hash = str(release.get("source_hash") or "").strip()
    if not source_hash:
        errors.append("release_manifest.source_hash is required")
    elif not _is_sha256(source_hash):
        errors.append("release_manifest.source_hash must be a SHA-256 hex digest")
    source_hash_set = release.get("source_hash_set")
    if not isinstance(source_hash_set, Mapping) or not source_hash_set:
        errors.append("release_manifest.source_hash_set must be a non-empty object")
    elif any(not _is_sha256(str(value)) for value in source_hash_set.values()):
        errors.append("release_manifest.source_hash_set values must be SHA-256 hex")

    delta = _object_field(versioning, "delta_manifest", errors)
    transition_kind = str(delta.get("transition_kind") or "").strip()
    if transition_kind not in {"initial_snapshot", "release_transition"}:
        errors.append(
            "delta_manifest.transition_kind must be initial_snapshot or release_transition"
        )

    affected = _object_field(versioning, "affected_scope_plan", errors)
    if not isinstance(affected.get("scope_states"), Mapping):
        errors.append("affected_scope_plan.scope_states must be an object")

    lineage = versioning.get("datapoint_lineage_map")
    if not isinstance(lineage, list):
        errors.append("datapoint_lineage_map must be a list")
    else:
        for index, item in enumerate(lineage, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"datapoint_lineage_map[{index}] must be an object")
                continue
            for key in ("sds_identifier", "standard_id", "release_id", "datapoint_id"):
                if not str(item.get(key) or "").strip():
                    errors.append(f"datapoint_lineage_map[{index}].{key} is required")

    activation = _object_field(versioning, "tenant_activation_policy", errors)
    if activation.get("dry_run_required") is not True:
        errors.append("tenant_activation_policy.dry_run_required must be true")
    if activation.get("real_import_requires_explicit_approval") is not True:
        errors.append(
            "tenant_activation_policy.real_import_requires_explicit_approval must be true"
        )

    return StandardVersioningValidation(
        present=True,
        required=require,
        valid=not errors,
        standard_id=standard_id or None,
        release_id=release_id or None,
        release_state=release_state or None,
        errors=tuple(errors),
    )


def _standard_versioning_error(
    *,
    required: bool,
    message: str,
    present: bool = False,
) -> StandardVersioningValidation:
    return StandardVersioningValidation(
        present=present,
        required=required,
        valid=False,
        errors=(message,),
    )


def _object_field(
    parent: Mapping[str, Any],
    key: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = parent.get(key)
    if isinstance(value, Mapping):
        return value
    errors.append(f"{key} must be a JSON object")
    return {}


def _required_str(
    parent: Mapping[str, Any],
    key: str,
    errors: list[str],
    prefix: str,
) -> str:
    value = str(parent.get(key) or "").strip()
    if not value:
        errors.append(f"{prefix}.{key} is required")
    return value


def _is_sha256(value: str) -> bool:
    stripped = str(value or "").strip().lower()
    return len(stripped) == 64 and all(char in "0123456789abcdef" for char in stripped)
