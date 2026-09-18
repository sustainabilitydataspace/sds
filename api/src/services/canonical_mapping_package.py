"""Canonical mapping package contract helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

SUPPORTED_CANONICAL_MAPPING_PACKAGE_SCHEMA_VERSION = "1.0"

REQUIRED_CANONICAL_MAPPING_PACKAGE_FILES = (
    "sds_standard_releases",
    "sds_standard_datapoints",
    "sds_mapping_assertion_groups",
    "sds_mapping_assertion_components",
)


class CanonicalMappingPackageError(ValueError):
    """Raised when a canonical mapping package cannot be accepted by SDS."""


@dataclass(frozen=True)
class CanonicalMappingPackageManifest:
    """Minimal manifest metadata SDS needs before accepting a mapping package."""

    package_schema_version: str
    files: tuple[str, ...]


def parse_canonical_mapping_manifest(
    manifest: Mapping[str, Any],
) -> CanonicalMappingPackageManifest:
    """Parse and validate the stable manifest fields before package import.

    This intentionally validates only the preflight contract that protects SDS
    from forward-incompatible Atomizer packages. Row-level validation belongs in
    the shadow/report-only importer.
    """

    version = str(manifest.get("package_schema_version") or "").strip()
    if not version:
        raise CanonicalMappingPackageError("package_schema_version is required")
    if _version_tuple(version) > _version_tuple(
        SUPPORTED_CANONICAL_MAPPING_PACKAGE_SCHEMA_VERSION
    ):
        raise CanonicalMappingPackageError(
            "package_schema_version "
            f"{version} is newer than supported "
            f"{SUPPORTED_CANONICAL_MAPPING_PACKAGE_SCHEMA_VERSION}"
        )

    files = manifest.get("files")
    if not isinstance(files, list):
        raise CanonicalMappingPackageError("files must be a list")

    file_names = tuple(
        canonical_mapping_manifest_file_name(item)
        for item in files
        if isinstance(item, Mapping)
    )
    missing = [
        required
        for required in REQUIRED_CANONICAL_MAPPING_PACKAGE_FILES
        if not any(name.startswith(required) for name in file_names)
    ]
    if missing:
        raise CanonicalMappingPackageError(
            "canonical mapping package is missing required files: " + ", ".join(missing)
        )

    return CanonicalMappingPackageManifest(
        package_schema_version=version,
        files=file_names,
    )


def canonical_mapping_manifest_file_name(item: Mapping[str, Any]) -> str:
    """Return the stable filename portion of a canonical mapping manifest item."""

    raw = str(item.get("name") or item.get("filename") or item.get("path") or "")
    return raw.strip().replace("\\", "/").rsplit("/", 1)[-1]


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as exc:
        raise CanonicalMappingPackageError(
            f"invalid package_schema_version: {version}"
        ) from exc
