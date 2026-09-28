from __future__ import annotations

import argparse
import json
from importlib import metadata
from pathlib import Path
from typing import Any

HARD_BLOCKED_LICENSE_MARKERS = ("agpl", "sspl")
REVIEW_LICENSE_MARKERS = ("lgpl", "gpl")


def _metadata_for(name: str) -> metadata.PackageMetadata | None:
    try:
        return metadata.distribution(name).metadata
    except metadata.PackageNotFoundError:
        return None


def _license_fields(
    package_metadata: metadata.PackageMetadata | None,
) -> dict[str, Any]:
    if package_metadata is None:
        return {
            "license": None,
            "license_expression": None,
            "license_classifiers": [],
        }

    classifiers = [
        classifier
        for classifier in package_metadata.get_all("Classifier", [])
        if classifier.startswith("License ::")
    ]
    return {
        "license": package_metadata.get("License"),
        "license_expression": package_metadata.get("License-Expression"),
        "license_classifiers": classifiers,
    }


def _marker(fields: dict[str, Any], markers: tuple[str, ...]) -> str | None:
    text = " ".join(
        str(item)
        for item in [
            fields.get("license"),
            fields.get("license_expression"),
            *fields.get("license_classifiers", []),
        ]
        if item
    ).lower()
    for marker in markers:
        if marker in text:
            return marker
    return None


def build_inventory(sbom_path: Path) -> dict[str, Any]:
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    components = sbom.get("components", [])
    rows = []
    blocked = []
    review = []
    unknown = 0

    for component in sorted(
        components,
        key=lambda item: (
            str(item.get("name", "")).lower(),
            str(item.get("version", "")),
        ),
    ):
        name = str(component.get("name", ""))
        fields = _license_fields(_metadata_for(name))
        hard_marker = _marker(fields, HARD_BLOCKED_LICENSE_MARKERS)
        review_marker = _marker(fields, REVIEW_LICENSE_MARKERS)
        if not any(fields.values()):
            unknown += 1
        row = {
            "name": name,
            "version": component.get("version"),
            **fields,
            "blocked_marker": hard_marker,
            "review_marker": review_marker,
        }
        rows.append(row)
        if hard_marker:
            blocked.append(row)
        elif review_marker:
            review.append(row)

    return {
        "source_sbom": str(sbom_path),
        "component_count": len(rows),
        "unknown_license_count": unknown,
        "hard_blocked_license_markers": list(HARD_BLOCKED_LICENSE_MARKERS),
        "review_license_markers": list(REVIEW_LICENSE_MARKERS),
        "blocked_count": len(blocked),
        "review_count": len(review),
        "blocked_components": blocked,
        "review_components": review,
        "components": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a runtime dependency license inventory from a CycloneDX SBOM."
    )
    parser.add_argument("--sbom", default="artifacts/security/runtime-sbom.cdx.json")
    parser.add_argument(
        "--output", default="artifacts/security/runtime-license-inventory.json"
    )
    args = parser.parse_args()

    sbom_path = Path(args.sbom)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    inventory = build_inventory(sbom_path)
    output_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "License inventory written to "
        f"{output_path} ({inventory['component_count']} components, "
        f"{inventory['blocked_count']} blocked, "
        f"{inventory['review_count']} review)"
    )
    return 1 if inventory["blocked_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
