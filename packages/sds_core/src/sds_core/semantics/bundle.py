from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sds_core.semantics.uris import CANONICAL_SDS_NAMESPACE, PUBLISH_CONTEXT_URL_V1_0


@dataclass(frozen=True)
class BundleSource:
    source_path: Path
    bundle_path: str


def default_semantics_bundle_sources() -> List[BundleSource]:
    return [
        BundleSource(
            source_path=Path("semantics/context/ngsi_ld_context.jsonld"),
            bundle_path="semantics/context/ngsi_ld_context.jsonld",
        ),
        BundleSource(
            source_path=Path("semantics/context/sds/v1.0.jsonld"),
            bundle_path="semantics/context/sds/v1.0.jsonld",
        ),
        BundleSource(
            source_path=Path("semantics/shacl/shapes_register_shacl.ttl"),
            bundle_path="semantics/shacl/shapes_register_shacl.ttl",
        ),
        BundleSource(
            source_path=Path("semantics/shacl/shapes_indicator_shacl.ttl"),
            bundle_path="semantics/shacl/shapes_indicator_shacl.ttl",
        ),
        BundleSource(
            source_path=Path("semantics/schema/register_schema.json"),
            bundle_path="semantics/schema/register_schema.json",
        ),
        BundleSource(
            source_path=Path("governance/policies/policy_registry_schema.json"),
            bundle_path="policies/policy_registry_schema.json",
        ),
        BundleSource(
            source_path=Path("api/ontologies/core_tbox.owl"),
            bundle_path="ontology/core_tbox.owl",
        ),
        BundleSource(
            source_path=Path("api/ontologies/generated_projection.owl"),
            bundle_path="ontology/generated_projection.owl",
        ),
    ]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_semantics_bundle_zip(
    project_root: Path,
    out_path: Path,
    *,
    version: str,
    sources: Sequence[BundleSource] | None = None,
) -> Dict[str, Any]:
    """Build a deterministic SDS semantics bundle as a ZIP file.

    - `project_root` should be the repository root.
    - `out_path` is created/overwritten.
    - `sources` controls which files are packaged and where they land in the zip.
    """
    srcs = list(sources) if sources is not None else default_semantics_bundle_sources()
    srcs = sorted(srcs, key=lambda s: s.bundle_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    files: List[Dict[str, Any]] = []

    with ZipFile(out_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        for src in srcs:
            abs_path = project_root / src.source_path
            if not abs_path.exists():
                raise FileNotFoundError(f"Missing semantics source: {src.source_path}")

            data = abs_path.read_bytes()
            files.append(
                {
                    "path": src.bundle_path,
                    "sha256": _sha256_hex(data),
                    "bytes": len(data),
                }
            )

            info = ZipInfo(filename=src.bundle_path)
            info.compress_type = ZIP_DEFLATED
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)

    return {
        "version": version,
        "namespace": CANONICAL_SDS_NAMESPACE,
        "publishContextUrl": PUBLISH_CONTEXT_URL_V1_0,
        "files": files,
    }
