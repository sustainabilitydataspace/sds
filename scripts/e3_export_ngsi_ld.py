#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from indicator_identifiers import (
    build_indicator_identifier,
    canonicalize_indicator_identifier,
)


def _bootstrap_repo_paths() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    sds_core_src = repo_root / "packages" / "sds_core" / "src"

    for candidate in (repo_root, sds_core_src):
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)

    return repo_root


REPO_ROOT = _bootstrap_repo_paths()

from sds_core.ngsi.context import (  # noqa: E402
    DEFAULT_EMBED_SDS_CONTEXT_FILE,
    DEFAULT_PUBLISH_SDS_CONTEXT_URL,
    build_context,
)
from sds_core.ngsi.export import dataset_entity, indicator_entity  # noqa: E402
from sds_core.policy.registry import load_policy_registry  # noqa: E402

DEFAULT_INPUT_CANDIDATES = [
    REPO_ROOT
    / "deliverables"
    / "E03-modelo-ngsi-ld"
    / "evidence"
    / "e03-dataset-register-v1-0.csv",
    REPO_ROOT / "data" / "processed" / "e1_dataset_register.csv",
    REPO_ROOT / "data" / "atomizer" / "framework_datapoints.csv",
    REPO_ROOT / "api" / "src" / "data" / "indicators.json",
]
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT / "data" / "extracted" / "analysis" / "ngsi_ld" / "entities.jsonld"
)
DEFAULT_POLICY_REGISTRY = REPO_ROOT / "governance" / "policies" / "policy_registry.json"


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def find_col(columns: Sequence[str], *candidates: str) -> str | None:
    lookup = {_normalize_key(col): col for col in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        normalized = _normalize_key(candidate)
        if normalized in lookup:
            return lookup[normalized]
    return None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    return columns, rows


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _infer_dimension(topic: str) -> str:
    topic_upper = (topic or "").upper()
    if any(
        token in topic_upper
        for token in ("SOCIAL", "WORKFORCE", "COMMUNITY", "EMPLOYEE", "DIVERSITY")
    ):
        return "S"
    if any(
        token in topic_upper
        for token in ("GOVERNANCE", "BOARD", "ETHICS", "COMPLIANCE", "ANTI")
    ):
        return "G"
    if any(token in topic_upper for token in ("TRANSVERSAL", "GENERAL", "BASIS")):
        return "Transversal"
    return "E"


def _coerce_source_row(value: str | int | None, fallback: int) -> int | str:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        if stripped:
            return stripped
    return fallback


def _register_row(
    row: dict[str, str], *, index: int, default_purpose: str, default_region: str
) -> dict[str, str]:
    return {
        "identifier": canonicalize_indicator_identifier(
            row.get("identifier", "").strip()
        ),
        "title": row.get("title", "").strip(),
        "indicator": row.get("indicator", "").strip(),
        "description": row.get("description", "").strip(),
        "dimension": row.get("dimension", "").strip(),
        "codeESRS": row.get("codeESRS", "").strip(),
        "codeGRI": row.get("codeGRI", "").strip(),
        "owner": row.get("owner", "").strip(),
        "accessRights": row.get("accessRights", "").strip(),
        "evidencePath": row.get("evidencePath", "").strip(),
        "sourceRow": str(_coerce_source_row(row.get("sourceRow"), index)),
        "purpose": (row.get("purpose") or default_purpose).strip() or default_purpose,
        "region": (row.get("region") or default_region).strip() or default_region,
        "policyId": row.get("policyId", "").strip(),
    }


def _framework_row(
    row: dict[str, str], *, index: int, default_purpose: str, default_region: str
) -> dict[str, str]:
    framework = row.get("FrameworkID", "").strip()
    code = row.get("DatapointCode", "").strip()
    label = row.get("Label", "").strip()
    description = row.get("Description", "").strip()
    topic = row.get("Topic", "").strip()
    unit = row.get("DefaultUnit", "").strip()
    dimension = _infer_dimension(topic)
    code_esrs = code if framework.upper() == "ESRS" else ""
    code_gri = code if framework.upper() == "GRI" else ""
    identifier = build_indicator_identifier(
        framework, code or label, fallback=f"item_{index}"
    )

    return {
        "identifier": identifier,
        "title": label or f"{framework} {code}",
        "indicator": label or code or identifier,
        "description": description,
        "dimension": dimension,
        "codeESRS": code_esrs,
        "codeGRI": code_gri,
        "owner": "system",
        "accessRights": "Internal",
        "evidencePath": "",
        "sourceRow": str(index),
        "purpose": default_purpose,
        "region": default_region,
        "policyId": "",
        "unitName": unit,
    }


def _indicator_json_row(
    row: dict[str, Any], *, index: int, default_purpose: str, default_region: str
) -> dict[str, str]:
    title = str(
        row.get("title")
        or row.get("indicator_name")
        or row.get("indicator")
        or row.get("identifier")
        or ""
    ).strip()
    identifier = canonicalize_indicator_identifier(
        str(
            row.get("identifier") or row.get("id") or title or f"indicator-{index}"
        ).strip()
    )
    description = str(row.get("description") or "").strip()
    dimension = str(row.get("dimension") or "").strip()
    code_esrs = str(row.get("code_esrs") or row.get("codeESRS") or "").strip()
    code_gri = str(row.get("code_gri") or row.get("codeGRI") or "").strip()
    source_row = row.get("source_row") or row.get("sourceRow") or index

    return {
        "identifier": identifier,
        "title": title or identifier,
        "indicator": title or identifier,
        "description": description,
        "dimension": dimension,
        "codeESRS": code_esrs,
        "codeGRI": code_gri,
        "owner": str(row.get("owner") or "system").strip() or "system",
        "accessRights": str(
            row.get("access_rights") or row.get("accessRights") or "Internal"
        ).strip()
        or "Internal",
        "evidencePath": str(
            row.get("evidence_path") or row.get("evidencePath") or ""
        ).strip(),
        "sourceRow": str(
            _coerce_source_row(
                source_row if isinstance(source_row, (str, int)) else str(source_row),
                index,
            )
        ),
        "purpose": str(row.get("purpose") or default_purpose).strip()
        or default_purpose,
        "region": str(row.get("region") or default_region).strip() or default_region,
        "policyId": str(row.get("policyId") or row.get("policy_id") or "").strip(),
    }


def _iter_input_rows(
    input_path: Path,
    *,
    default_purpose: str,
    default_region: str,
) -> tuple[str, list[dict[str, str]]]:
    if input_path.suffix.lower() == ".json":
        doc = _read_json(input_path)
        indicators = doc.get("indicators", [])
        rows: list[dict[str, str]] = []
        if isinstance(indicators, list):
            for index, item in enumerate(indicators, start=1):
                if isinstance(item, dict):
                    rows.append(
                        _indicator_json_row(
                            item,
                            index=index,
                            default_purpose=default_purpose,
                            default_region=default_region,
                        )
                    )
        return "json", rows

    columns, raw_rows = _read_csv(input_path)
    if find_col(columns, "FrameworkID", "DatapointCode"):
        rows = [
            _framework_row(
                row,
                index=index,
                default_purpose=default_purpose,
                default_region=default_region,
            )
            for index, row in enumerate(raw_rows, start=1)
        ]
        return "framework", rows

    rows = [
        _register_row(
            row,
            index=index,
            default_purpose=default_purpose,
            default_region=default_region,
        )
        for index, row in enumerate(raw_rows, start=1)
    ]
    return "register", rows


def _candidate_input_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path

    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No input file found. Expected one of: "
        + ", ".join(
            str(path.relative_to(REPO_ROOT)) for path in DEFAULT_INPUT_CANDIDATES
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export NGSI-LD entities from the SDS E3 common data model"
    )
    parser.add_argument(
        "--input",
        default="",
        help="Input CSV/JSON. Defaults to the first existing repo-local source register.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output JSON-LD path (default: repo-local analysis export)",
    )
    parser.add_argument(
        "--policy-registry",
        default=str(DEFAULT_POLICY_REGISTRY),
        help="Path to the local governance policy registry JSON.",
    )
    parser.add_argument(
        "--default-purpose",
        default="reporting",
        help="Fallback purpose when the input does not provide one.",
    )
    parser.add_argument(
        "--default-region",
        default="EU",
        help="Fallback region when the input does not provide one.",
    )
    parser.add_argument(
        "--context-mode",
        choices=["dev", "publish", "embed"],
        default="dev",
        help="How to include the SDS JSON-LD context.",
    )
    parser.add_argument(
        "--publish-context-url",
        default=DEFAULT_PUBLISH_SDS_CONTEXT_URL,
        help="Context URL used when --context-mode publish.",
    )
    parser.add_argument(
        "--embed-context-file",
        default=str(DEFAULT_EMBED_SDS_CONTEXT_FILE),
        help="Local JSON-LD context file used when --context-mode embed.",
    )
    args = parser.parse_args(argv)

    input_path = _candidate_input_path(args.input or None)
    source_kind, rows = _iter_input_rows(
        input_path,
        default_purpose=args.default_purpose,
        default_region=args.default_region,
    )

    policy_registry = load_policy_registry(Path(args.policy_registry))

    entities: list[dict[str, object]] = []
    seen_indicators: dict[str, str] = {}
    policy_usage: dict[str, int] = {}

    for row in rows:
        entity, indicator_id = dataset_entity(
            row,
            policy_registry,
            default_purpose=args.default_purpose,
            default_region=args.default_region,
        )
        entities.append(entity)

        policy_relation = entity.get("hasPolicy", {})
        if isinstance(policy_relation, dict):
            raw_policy_objects = policy_relation.get("object", "")
            policy_objects = (
                raw_policy_objects
                if isinstance(raw_policy_objects, list)
                else [raw_policy_objects]
            )
            for policy_object in policy_objects:
                policy_urn = str(policy_object)
                if policy_urn.startswith("urn:sds:policy:"):
                    policy_id = policy_urn.replace("urn:sds:policy:", "", 1)
                    policy_usage[policy_id] = policy_usage.get(policy_id, 0) + 1

        if indicator_id and indicator_id not in seen_indicators:
            entities.append(indicator_entity(indicator_id, row.get("indicator", "")))
            seen_indicators[indicator_id] = row.get("indicator", "")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "@context": build_context(
            args.context_mode,
            args.publish_context_url,
            Path(args.embed_context_file),
        ),
        "@graph": entities,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Wrote NGSI-LD entities: {out_path}")
    print(f"Source kind: {source_kind}")
    print(f"Rows exported: {len(rows)}")
    print(f"Entities written: {len(entities)}")
    if policy_usage:
        print("Policy distribution:")
        for policy_id, count in sorted(
            policy_usage.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"  - {policy_id}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
