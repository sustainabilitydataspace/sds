#!/usr/bin/env python3
"""
EDC Bundle Generator - Deny-by-Default Policy Generation

Generates EDC-compliant assets, policies, and contract definitions from:
- E1 dataset register (CSV)
- Policy registry (JSON, deny-by-default)

Security: Implements deny-by-default. No allow-all policies permitted.
Validation: Rejects empty constraints, missing policies, invalid references.

Version: 2026-01-27 (E6-000 hardening)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
MAX_REGISTER_CSV_BYTES = 16 * 1024 * 1024
MAX_REGISTER_ROWS = 20_000
MAX_CSV_FIELD_BYTES = 1024 * 1024

from sds_core.edc.policy import (
    AllowAllDetectedError,
    PolicyValidationError,
    transform_odrl_to_edc,
    validate_no_allow_all_in_output,
    validate_policy_not_allow_all,
)
from sds_core.policy.selection import select_policy_for_dataset


def _normalize_key(text: str) -> str:
    return "".join(ch for ch in (text or "").strip().lower() if ch.isalnum())


def find_col(columns: List[str], *candidates: str) -> Optional[str]:
    lookup = {_normalize_key(col): col for col in columns}
    for candidate in candidates:
        if candidate in columns:
            return candidate
        normalized = _normalize_key(candidate)
        if normalized in lookup:
            return lookup[normalized]
    return None


class RegisterReadError(ValueError):
    pass


def _read_register_bytes(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RegisterReadError(f"register CSV cannot be read: {path}: {exc}") from exc
    try:
        try:
            opened_stat = os.fstat(descriptor)
            opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                raise RegisterReadError(
                    f"register CSV is not an independent regular file: {path}"
                )
            if opened_stat.st_size > MAX_REGISTER_CSV_BYTES:
                raise RegisterReadError("register CSV exceeds size limit")
            chunks: List[bytes] = []
            remaining = MAX_REGISTER_CSV_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > MAX_REGISTER_CSV_BYTES:
                raise RegisterReadError("register CSV exceeds size limit")
            path_stat = path.lstat()
        except OSError as exc:
            raise RegisterReadError(
                f"register CSV cannot be read: {path}: {exc}"
            ) from exc
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_nlink != 1
        or (path_stat.st_dev, path_stat.st_ino) != opened_identity
    ):
        raise RegisterReadError("register CSV changed while being read")
    return content


def load_csv(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    content = _read_register_bytes(path)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RegisterReadError("register CSV is not UTF-8") from exc

    previous_field_limit = csv.field_size_limit(MAX_CSV_FIELD_BYTES)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        columns = list(reader.fieldnames or [])
        if any(
            len(column.encode("utf-8")) > MAX_CSV_FIELD_BYTES
            for column in columns
        ):
            raise RegisterReadError("register CSV field exceeds byte limit")
        rows: List[Dict[str, str]] = []
        for row in reader:
            if len(rows) >= MAX_REGISTER_ROWS:
                raise RegisterReadError("register CSV row count exceeds limit")
            if None in row or any(value is None for value in row.values()):
                raise RegisterReadError(
                    "register CSV column count does not match header"
                )
            if any(
                len(value.encode("utf-8")) > MAX_CSV_FIELD_BYTES
                for value in row.values()
            ):
                raise RegisterReadError("register CSV field exceeds byte limit")
            rows.append(row)
        return columns, rows
    except csv.Error as exc:
        raise RegisterReadError(f"register CSV is invalid: {exc}") from exc
    finally:
        csv.field_size_limit(previous_field_limit)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


@dataclass
class PolicyEntry:
    """Parsed policy from registry."""
    uid: str
    name: str
    status: str
    legal_basis: str
    purposes: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    retention_days: Optional[int] = None
    composition_mode: str = "standalone"
    odrl: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyRegistry:
    """Loaded policy registry with validation."""
    version: str
    effective_date: str
    deny_by_default: bool
    default_deny_policy: str
    policies: Dict[str, PolicyEntry]
    purpose_vocabulary: Dict[str, Dict[str, str]]
    region_vocabulary: Dict[str, Dict[str, Any]]
    source_path: str


def load_policy_registry(path: Path) -> PolicyRegistry:
    """Load and validate policy registry from JSON file."""
    logger.info(f"Loading policy registry from {path}")

    if not path.exists():
        raise FileNotFoundError(f"Policy registry not found: {path}")

    if path.suffix != ".json":
        raise PolicyValidationError(
            f"Policy registry must be JSON format (got {path.suffix}). "
            "Markdown parsing is deprecated due to ambiguity risks."
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate deny-by-default is enabled
    if not data.get("denyByDefault", False):
        raise PolicyValidationError(
            "Policy registry must have 'denyByDefault: true'. "
            "Allow-by-default configurations are not permitted."
        )

    default_deny = data.get("defaultDenyPolicy", "")
    if not default_deny:
        raise PolicyValidationError(
            "Policy registry must specify 'defaultDenyPolicy' for unmatched datasets."
        )

    # Parse policies
    policies: Dict[str, PolicyEntry] = {}
    for p in data.get("policies", []):
        uid = p.get("uid", "")
        if not uid:
            raise PolicyValidationError("Policy missing 'uid' field")

        # Validate no allow-all patterns
        validate_policy_not_allow_all(uid, p.get("odrl", {}))

        entry = PolicyEntry(
            uid=uid,
            name=p.get("name", uid),
            status=p.get("status", "active"),
            legal_basis=p.get("legalBasis", ""),
            purposes=p.get("purpose", []),
            regions=p.get("regions", []),
            retention_days=p.get("retentionDays"),
            composition_mode=p.get("compositionMode", "standalone"),
            odrl=p.get("odrl", {}),
        )
        policies[uid] = entry

    # Validate default deny policy exists
    if default_deny not in policies:
        raise PolicyValidationError(
            f"Default deny policy '{default_deny}' not found in registry"
        )

    logger.info(f"Loaded {len(policies)} policies from registry v{data.get('version', '?')}")

    return PolicyRegistry(
        version=data.get("version", ""),
        effective_date=data.get("effectiveDate", ""),
        deny_by_default=True,
        default_deny_policy=default_deny,
        policies=policies,
        purpose_vocabulary=data.get("purposeVocabulary", {}),
        region_vocabulary=data.get("regionVocabulary", {}),
        source_path=str(path),
    )


def build_edc_policies(registry: PolicyRegistry) -> List[Dict[str, Any]]:
    """
    Build EDC policy definitions from registry.

    Transforms ODRL policies into EDC-compatible format.
    """
    edc_policies: List[Dict[str, Any]] = []

    for uid, entry in registry.policies.items():
        if entry.status == "deprecated":
            logger.info(f"Skipping deprecated policy: {uid}")
            continue

        # Transform ODRL to EDC format
        edc_policy = transform_odrl_to_edc(uid, entry.odrl)
        edc_policies.append(edc_policy)

    return edc_policies


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate EDC bundle with deny-by-default policy enforcement"
    )

    ap.add_argument("--input", default=str(REPO_ROOT / "data" / "processed" / "e1_dataset_register.csv"),
                    help="Path to E1 dataset register CSV")
    ap.add_argument("--outdir", default="configs/edc",
                    help="Output directory for EDC bundle")
    ap.add_argument("--base-url", default="https://connector.example.org/data-products",
                    help="Base URL for data product endpoints")
    ap.add_argument("--policy-registry", default="docs/policies/policy_registry.json",
                    help="Path to policy registry JSON")
    ap.add_argument("--default-purpose", default="reporting",
                    help="Default purpose when not specified in register")
    ap.add_argument("--default-region", default="EU",
                    help="Default region when not specified in register")
    ap.add_argument("--strict", action="store_true",
                    help="Fail on any warning (recommended for production)")

    args = ap.parse_args()

    warnings: List[str] = []

    try:
        # Load policy registry
        registry = load_policy_registry(Path(args.policy_registry))

        # Build EDC policies
        edc_policies = build_edc_policies(registry)

        # Final allow-all validation
        validate_no_allow_all_in_output(edc_policies)

        # Load dataset register
        cols, rows = load_csv(Path(args.input))
        c_identifier = find_col(cols, "identifier") or "identifier"
        c_title = find_col(cols, "title") or "title"
        c_purpose = find_col(cols, "purpose")
        c_region = find_col(cols, "region")
        c_policy_id = find_col(cols, "policyId", "policy_id")

        # Create output directory
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        # Generate assets and contracts
        assets: List[Dict[str, Any]] = []
        contracts: List[Dict[str, Any]] = []
        policy_usage: Dict[str, int] = {}

        for r in rows:
            ident = (r.get(c_identifier) or "").strip()
            title = (r.get(c_title) or ident or "dataset").strip()
            asset_id = f"asset-{ident or title}".replace(":", "-").replace(" ", "-")

            # Get purpose and region from register or defaults
            purpose = ((r.get(c_purpose) or "").strip() if c_purpose else "") or args.default_purpose
            region = ((r.get(c_region) or "").strip() if c_region else "") or args.default_region
            explicit_policy = (r.get(c_policy_id) or "").strip() if c_policy_id else ""

            # Select policy
            primary_policy_id, additive_policies = select_policy_for_dataset(
                registry,
                purpose=purpose,
                region=region,
                explicit_policy_id=explicit_policy or None,
            )

            # Track policy usage
            policy_usage[primary_policy_id] = policy_usage.get(primary_policy_id, 0) + 1

            # Create asset
            assets.append({
                "asset": {
                    "properties": {
                        "asset:prop:id": asset_id,
                        "asset:prop:name": title,
                        "asset:prop:purpose": purpose,
                        "asset:prop:region": region,
                    }
                },
                "dataAddress": {
                    "type": "HttpData",
                    "baseUrl": f"{args.base_url}/{asset_id}",
                    "proxyMethod": True,
                    "proxyPath": True,
                }
            })

            # Create contract definition
            contracts.append({
                "id": f"contract-{asset_id}",
                "accessPolicyId": primary_policy_id,
                "contractPolicyId": primary_policy_id,
                "additionalPolicies": additive_policies,
                "assetsSelector": [
                    {"operandLeft": "asset:prop:id", "operator": "=", "operandRight": asset_id}
                ]
            })

        # Validate all referenced policies exist
        known_policy_ids = {p["uid"] for p in edc_policies}
        missing_policies: Set[str] = set()

        for c in contracts:
            for key in ("accessPolicyId", "contractPolicyId"):
                pid = c.get(key)
                if pid and pid not in known_policy_ids:
                    missing_policies.add(pid)
            for pid in c.get("additionalPolicies", []):
                if pid not in known_policy_ids:
                    missing_policies.add(pid)

        if missing_policies:
            raise PolicyValidationError(
                f"Contracts reference unknown policy IDs: {sorted(missing_policies)}"
            )

        # Write output files
        (outdir / "assets.json").write_text(
            json.dumps(assets, indent=2), encoding="utf-8"
        )
        (outdir / "policies.json").write_text(
            json.dumps(edc_policies, indent=2), encoding="utf-8"
        )
        (outdir / "contract-definitions.json").write_text(
            json.dumps(contracts, indent=2), encoding="utf-8"
        )

        # Generate README
        readme_lines = [
            "# EDC Bundle (Generated)",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Policy Registry: {registry.source_path} (v{registry.version})",
            f"Deny-by-Default: {registry.deny_by_default}",
            "",
            "## Security",
            "",
            "This bundle enforces **deny-by-default** policy semantics:",
            f"- Default deny policy: `{registry.default_deny_policy}`",
            "- No allow-all policies permitted",
            "- All permissions require explicit constraints",
            "",
            "## Files",
            "",
            f"- `assets.json` - {len(assets)} asset definitions",
            f"- `policies.json` - {len(edc_policies)} policy definitions",
            f"- `contract-definitions.json` - {len(contracts)} contract definitions",
            "",
            "## Policy Usage",
            "",
            "| Policy | Datasets |",
            "|--------|----------|",
        ]
        for pid, count in sorted(policy_usage.items(), key=lambda x: -x[1]):
            readme_lines.append(f"| `{pid}` | {count} |")

        readme_lines.extend([
            "",
            "## Usage",
            "",
            "POST these JSONs to EDC Data Management API in order:",
            "1. `assets.json` -> POST /management/v2/assets",
            "2. `policies.json` -> POST /management/v2/policydefinitions",
            "3. `contract-definitions.json` -> POST /management/v2/contractdefinitions",
            "",
            "Ensure consumers provide purpose/region claims compatible with policies.",
        ])

        (outdir / "README.md").write_text("\n".join(readme_lines), encoding="utf-8")

        # Summary
        logger.info(
            f"Generated EDC bundle: {len(assets)} assets, "
            f"{len(edc_policies)} policies, {len(contracts)} contracts"
        )

        if args.strict and warnings:
            logger.error(f"Strict mode: {len(warnings)} warnings treated as errors")
            for w in warnings:
                logger.error(f"  - {w}")
            return 1

        return 0

    except (PolicyValidationError, AllowAllDetectedError) as e:
        logger.error(f"Policy validation failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
