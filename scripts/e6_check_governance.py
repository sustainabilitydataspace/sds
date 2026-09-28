#!/usr/bin/env python3
"""
E6 Governance Checker

Validates governance artifacts for compliance with E6 requirements:
- Policy registry has deny-by-default enabled
- No allow-all policies exist
- Policy and trust JSON documents validate against their referenced schemas
- Trust enforcement is explicitly held or uses current, non-placeholder issuers
  aligned to Bitstring Status List 2025
- Governance/supporting Markdown points only to repo-local artifacts that exist
- EDC bundle counts are aligned with the current dataset register

Exit codes:
  0 - All checks pass
  1 - Validation errors found
  2 - Configuration/file errors

Version: 2026-09-12 (E6-006)
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import jsonschema
except Exception:  # pragma: no cover
    jsonschema = None


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
DATE_MARKER_RE = re.compile(r"v(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"placeholder", re.IGNORECASE)
FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
DEPRECATED_STATUS_TERMS = ("StatusList2021", "Status List 2021")
VALID_POLICY_STATUSES = {"active", "deprecated", "pilot", "planned"}
VALID_ISSUER_STATUSES = {"active", "revoked", "pilot", "planned", "expired"}
VALID_RUNTIME_ENFORCEMENT_STATUSES = {"not_activated", "activated"}
GLOB_CHARS = frozenset("*?[")
LOCAL_SOURCE_PATH_RE = re.compile(
    r"(?:\\\\wsl\.localhost\b|/home/[A-Za-z0-9._-]+(?:/|\b))"
)
BASE64URL_NO_PADDING_RE = re.compile(r"^[A-Za-z0-9_-]+$")
P256_FIELD_MODULUS = int(
    "ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16
)
P256_B = int(
    "5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b",
    16,
)
MAX_JSON_FILE_BYTES = 16 * 1024 * 1024
MAX_REGISTER_CSV_BYTES = 16 * 1024 * 1024
MAX_REGISTER_ROWS = 20_000
MAX_CSV_FIELD_BYTES = 1024 * 1024


class JsonFileReadError(ValueError):
    pass


class RegisterFileReadError(ValueError):
    pass


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    severity: str = "error"


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.severity == "error")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == "warning")

    def add(
        self, name: str, passed: bool, message: str, severity: str = "error"
    ) -> None:
        self.results.append(CheckResult(name, passed, message, severity))


def _read_json(path: Path) -> Any:
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
        raise JsonFileReadError(f"JSON file cannot be read: {path}: {exc}") from exc
    try:
        try:
            opened_stat = os.fstat(descriptor)
            opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                raise JsonFileReadError(f"JSON path is not a regular file: {path}")
            if opened_stat.st_size > MAX_JSON_FILE_BYTES:
                raise JsonFileReadError(f"JSON file exceeds size limit: {path}")
            chunks: list[bytes] = []
            remaining = MAX_JSON_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > MAX_JSON_FILE_BYTES:
                raise JsonFileReadError(f"JSON file exceeds size limit: {path}")
            path_stat = path.lstat()
        except OSError as exc:
            raise JsonFileReadError(f"JSON file cannot be read: {path}: {exc}") from exc
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or (path_stat.st_dev, path_stat.st_ino) != opened_identity
        or path_stat.st_nlink != 1
    ):
        raise JsonFileReadError(f"JSON file changed while being read: {path}")
    try:
        return json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise JsonFileReadError(f"JSON file is not UTF-8: {path}") from exc


def _parse_iso_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _extract_local_refs(content: str) -> list[str]:
    content = FENCED_CODE_BLOCK_RE.sub("", content)
    refs: list[str] = []
    for token in INLINE_CODE_RE.findall(content):
        token = token.strip()
        if "://" in token:
            continue
        if token.startswith("\\\\"):
            continue
        if re.match(r"^[A-Za-z]:[\\/]", token):
            continue
        if not any(sep in token for sep in ("/", "\\")):
            continue
        refs.append(token)
    return refs


def _resolve_local_ref(ref: str, *, repo_root: Path, doc_path: Path) -> Path:
    candidate = Path(ref)
    if candidate.is_absolute():
        return candidate
    if ref.startswith("./") or ref.startswith("../"):
        return (doc_path.parent / candidate).resolve()
    return (repo_root / candidate).resolve()


def _glob_has_matches(ref: str, *, repo_root: Path, doc_path: Path) -> bool:
    normalized = ref.replace("\\", "/")
    base_dir = (
        doc_path.parent
        if normalized.startswith("./") or normalized.startswith("../")
        else repo_root
    )
    return any(base_dir.glob(normalized))


def _contains_local_source_path(content: str) -> bool:
    return bool(LOCAL_SOURCE_PATH_RE.search(content))


def _load_schema(
    document_path: Path, document: dict, report: ValidationReport, check_prefix: str
) -> Optional[dict]:
    schema_ref = document.get("$schema")
    if not isinstance(schema_ref, str) or not schema_ref:
        report.add(
            f"{check_prefix}_schema_declared",
            False,
            f"{document_path.name} does not declare a JSON schema reference",
        )
        return None

    if "://" in schema_ref:
        report.add(
            f"{check_prefix}_schema_ref_remote",
            True,
            f"Schema reference is remote: {schema_ref}",
            severity="info",
        )
        return None

    schema_path = (document_path.parent / schema_ref).resolve()
    if not schema_path.exists():
        report.add(
            f"{check_prefix}_schema_exists",
            False,
            f"Schema file not found: {schema_path}",
        )
        return None

    report.add(f"{check_prefix}_schema_exists", True, f"Found schema: {schema_path}")

    if jsonschema is None:
        report.add(
            f"{check_prefix}_schema_validated",
            False,
            "Required dependency jsonschema is unavailable; install "
            "api/requirements-dev.txt in the selected Python environment. "
            "Schema validation cannot proceed.",
        )
        return None

    try:
        schema = _read_json(schema_path)
        jsonschema.validate(document, schema)
    except (json.JSONDecodeError, JsonFileReadError) as exc:
        report.add(
            f"{check_prefix}_schema_valid_json", False, f"Invalid schema JSON: {exc}"
        )
        return None
    except jsonschema.ValidationError as exc:
        report.add(
            f"{check_prefix}_schema_validated",
            False,
            f"Schema validation failed: {exc.message}",
        )
        return None

    report.add(
        f"{check_prefix}_schema_validated",
        True,
        f"{document_path.name} validates against its declared schema",
    )
    return schema


def _decode_p256_coordinate(value: object, coordinate_name: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{coordinate_name} is not a string")
    if "=" in value or not BASE64URL_NO_PADDING_RE.fullmatch(value):
        raise ValueError(f"{coordinate_name} is not strict unpadded base64url")
    if len(value) % 4 == 1:
        raise ValueError(f"{coordinate_name} has invalid base64url length")

    padded_value = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            padded_value.encode("ascii"), altchars=b"-_", validate=True
        )
    except Exception as exc:
        raise ValueError(f"{coordinate_name} is not valid base64url") from exc

    if len(decoded) != 32:
        raise ValueError(f"{coordinate_name} decodes to {len(decoded)} bytes, not 32")

    return int.from_bytes(decoded, "big")


def _validate_p256_public_jwk(public_key_jwk: object) -> Optional[str]:
    if not isinstance(public_key_jwk, dict):
        return "publicKeyJwk is not an object"
    if public_key_jwk.get("kty") != "EC" or public_key_jwk.get("crv") != "P-256":
        return "publicKeyJwk is not an EC P-256 public JWK"

    try:
        x = _decode_p256_coordinate(public_key_jwk.get("x"), "x")
        y = _decode_p256_coordinate(public_key_jwk.get("y"), "y")
    except ValueError as exc:
        return str(exc)

    if not (0 <= x < P256_FIELD_MODULUS and 0 <= y < P256_FIELD_MODULUS):
        return "coordinate is outside the P-256 field"

    left = pow(y, 2, P256_FIELD_MODULUS)
    right = (pow(x, 3, P256_FIELD_MODULUS) - 3 * x + P256_B) % P256_FIELD_MODULUS
    if left != right:
        return "point is not on the P-256 curve"

    return None


def _check_markdown_refs(
    doc_path: Path, report: ValidationReport, *, check_prefix: str, repo_root: Path
) -> str:
    if not doc_path.exists():
        report.add(
            f"{check_prefix}_exists", False, f"Markdown artifact not found: {doc_path}"
        )
        return ""

    content = doc_path.read_text(encoding="utf-8")
    report.add(f"{check_prefix}_exists", True, f"Found: {doc_path}")

    missing_refs: list[str] = []
    for ref in _extract_local_refs(content):
        if any(char in ref for char in GLOB_CHARS):
            if _glob_has_matches(ref, repo_root=repo_root, doc_path=doc_path):
                continue
            missing_refs.append(ref)
            continue
        resolved = _resolve_local_ref(ref, repo_root=repo_root, doc_path=doc_path)
        if not resolved.exists():
            missing_refs.append(ref)

    if missing_refs:
        report.add(
            f"{check_prefix}_local_refs",
            False,
            f"Missing local references: {', '.join(sorted(set(missing_refs)))}",
        )
    else:
        report.add(
            f"{check_prefix}_local_refs",
            True,
            "All local markdown references resolve within the repo",
        )

    return content


def _has_effective_permission_constraints(
    permission: dict,
    *,
    collection: str,
    left: str,
    right: str,
    allowed_operators: frozenset[str],
) -> bool:
    constraints = permission.get(collection)
    if not isinstance(constraints, list) or not constraints:
        return False
    for constraint in constraints:
        if not isinstance(constraint, dict):
            return False
        if constraint.get(left) not in {"purpose", "spatial"}:
            return False
        if constraint.get("operator") not in allowed_operators:
            return False
        operand = constraint.get(right)
        if isinstance(operand, str):
            if not operand.strip():
                return False
        elif not (
            isinstance(operand, list)
            and operand
            and all(isinstance(value, str) and value.strip() for value in operand)
        ):
            return False
    return True


def check_policy_registry(
    registry_path: Path, report: ValidationReport
) -> Optional[dict]:
    if not registry_path.exists():
        report.add(
            "policy_registry_exists",
            False,
            f"Policy registry not found: {registry_path}",
        )
        return None

    report.add("policy_registry_exists", True, f"Found: {registry_path}")

    if registry_path.suffix != ".json":
        report.add(
            "policy_registry_format",
            False,
            f"Policy registry must be JSON (got {registry_path.suffix}). Markdown format is deprecated.",
        )
        return None

    try:
        registry = _read_json(registry_path)
    except (json.JSONDecodeError, JsonFileReadError) as exc:
        report.add("policy_registry_valid_json", False, f"Invalid JSON: {exc}")
        return None

    report.add("policy_registry_valid_json", True, "Valid JSON")
    policies = registry.get("policies") if isinstance(registry, dict) else None
    structurally_valid = (
        isinstance(policies, list)
        and isinstance(registry.get("purposeVocabulary", {}), dict)
        and all(
            isinstance(policy, dict)
            and isinstance(policy.get("uid"), str)
            and isinstance(policy.get("odrl", {}), dict)
            and all(
                isinstance(policy.get("odrl", {}).get(field, []), list)
                and all(
                    isinstance(item, dict)
                    for item in policy.get("odrl", {}).get(field, [])
                )
                for field in ("permission", "prohibition")
            )
            for policy in policies
        )
    )
    report.add(
        "policy_registry_structure",
        structurally_valid,
        (
            "Policy registry has valid collection shapes"
            if structurally_valid
            else "Policy registry has malformed collection structure"
        ),
    )
    if not structurally_valid:
        return None
    _load_schema(registry_path, registry, report, "policy_registry")

    report.add(
        "deny_by_default",
        bool(registry.get("denyByDefault", False)),
        (
            "Deny-by-default enabled"
            if registry.get("denyByDefault", False)
            else "Policy registry must have 'denyByDefault: true'"
        ),
    )

    default_deny = registry.get("defaultDenyPolicy", "")
    if not default_deny:
        report.add(
            "default_deny_policy_specified",
            False,
            "Registry must specify 'defaultDenyPolicy'",
        )
    else:
        policy_uids = {
            p.get("uid") for p in registry.get("policies", []) if isinstance(p, dict)
        }
        report.add(
            "default_deny_policy_exists",
            default_deny in policy_uids,
            (
                f"Default deny: {default_deny}"
                if default_deny in policy_uids
                else f"Default deny policy '{default_deny}' not found in registry"
            ),
        )

    allow_all_found = []
    missing_legal_basis = []
    invalid_status = []
    invalid_uid_format = []
    invalid_purpose_defaults = []
    uid_pattern = re.compile(r"^policy-[a-z0-9-]+$")
    policy_uids = {
        p.get("uid") for p in registry.get("policies", []) if isinstance(p, dict)
    }

    for policy in registry.get("policies", []):
        uid = policy.get("uid", "unknown")
        if not uid_pattern.match(uid):
            invalid_uid_format.append(uid)
        if policy.get("status", "") not in VALID_POLICY_STATUSES:
            invalid_status.append(f"{uid} (status={policy.get('status', '')})")
        if not policy.get("legalBasis"):
            missing_legal_basis.append(uid)

        odrl = policy.get("odrl", {})
        permissions = odrl.get("permission", [])
        for perm in permissions:
            if not _has_effective_permission_constraints(
                perm,
                collection="constraint",
                left="leftOperand",
                right="rightOperand",
                allowed_operators=frozenset({"eq", "isAnyOf"}),
            ):
                allow_all_found.append(uid)
                break

    for purpose, entry in registry.get("purposeVocabulary", {}).items():
        if isinstance(entry, dict):
            default_policy = entry.get("defaultPolicyId")
            if default_policy and default_policy not in policy_uids:
                invalid_purpose_defaults.append(f"{purpose}->{default_policy}")

    report.add(
        "no_allow_all_policies",
        not allow_all_found,
        (
            f"No allow-all patterns in {len(registry.get('policies', []))} policies"
            if not allow_all_found
            else f"Allow-all patterns detected in: {', '.join(allow_all_found)}"
        ),
    )
    report.add(
        "all_policies_have_legal_basis",
        not missing_legal_basis,
        (
            "All policies have legal basis"
            if not missing_legal_basis
            else f"Missing legal basis: {', '.join(missing_legal_basis)}"
        ),
    )
    report.add(
        "all_policies_valid_status",
        not invalid_status,
        (
            "All policies use valid statuses"
            if not invalid_status
            else f"Invalid status: {', '.join(invalid_status)}"
        ),
    )
    report.add(
        "all_policies_valid_uid",
        not invalid_uid_format,
        (
            "All policy UIDs follow the naming convention"
            if not invalid_uid_format
            else f"Invalid UID format: {', '.join(invalid_uid_format)}"
        ),
    )
    report.add(
        "purpose_vocabulary_consistency",
        not invalid_purpose_defaults,
        (
            "Purpose vocabulary default policies resolve correctly"
            if not invalid_purpose_defaults
            else f"Purpose vocabulary references non-existent policies: {', '.join(invalid_purpose_defaults)}"
        ),
    )

    return registry


def check_data_product_terms(
    terms_path: Path,
    registry: Optional[dict],
    report: ValidationReport,
    *,
    repo_root: Path,
) -> None:
    content = _check_markdown_refs(
        terms_path, report, check_prefix="data_product_terms", repo_root=repo_root
    )
    if not content:
        return

    if registry:
        policy_uids = {
            p.get("uid") for p in registry.get("policies", []) if isinstance(p, dict)
        }
        policy_refs = set(re.findall(r"policy-[a-z0-9-]+", content))
        unknown_refs = policy_refs - policy_uids
        report.add(
            "data_product_terms_policy_refs",
            not unknown_refs,
            (
                f"All {len(policy_refs)} policy references are valid"
                if not unknown_refs
                else f"Unknown policy references: {', '.join(sorted(unknown_refs))}"
            ),
        )

        registry_version = str(registry.get("version", "") or "")
        version_match = DATE_MARKER_RE.search(content)
        aligned = not (
            version_match
            and registry_version
            and version_match.group(1) != registry_version
        )
        report.add(
            "data_product_terms_version_alignment",
            aligned,
            (
                "Terms document version aligns with the policy registry version"
                if aligned
                else f"Terms document version {version_match.group(1)} does not match policy registry version {registry_version}"
            ),
        )
    else:
        report.add(
            "data_product_terms_policy_refs",
            False,
            "Cannot validate policy references without a valid policy registry",
            severity="warning",
        )

    has_wsl_refs = _contains_local_source_path(content)
    report.add(
        "data_product_terms_no_wsl_refs",
        not has_wsl_refs,
        (
            "Data product terms contain no WSL-specific paths"
            if not has_wsl_refs
            else "Data product terms still contain WSL-specific paths"
        ),
    )


def check_trust_issuers(
    issuers_path_stem: Path,
    report: ValidationReport,
    *,
    repo_root: Path,
    current_date: date,
) -> None:
    json_path = issuers_path_stem.with_suffix(".json")
    md_path = issuers_path_stem.with_suffix(".md")

    if not json_path.exists():
        report.add(
            "trust_issuers_json", False, f"Trust issuers JSON not found: {json_path}"
        )
        return

    report.add("trust_issuers_json", True, f"Found: {json_path}")

    try:
        issuers = _read_json(json_path)
    except (json.JSONDecodeError, JsonFileReadError) as exc:
        report.add("trust_issuers_valid_json", False, f"Invalid JSON: {exc}")
        return

    report.add("trust_issuers_valid_json", True, "Valid JSON")
    _load_schema(json_path, issuers, report, "trust_issuers")
    report.add(
        "trust_issuers_standard",
        issuers.get("statusListStandard") == "BitstringStatusList2025",
        (
            "Trust issuer registry declares Bitstring Status List 2025"
            if issuers.get("statusListStandard") == "BitstringStatusList2025"
            else f"Unexpected status list standard: {issuers.get('statusListStandard')!r}"
        ),
    )

    runtime_enforcement = issuers.get("runtimeEnforcement")
    runtime_status = (
        runtime_enforcement.get("status")
        if isinstance(runtime_enforcement, dict)
        else None
    )
    issuer_records_value = issuers.get("issuers")
    issuer_records_are_list = isinstance(issuer_records_value, list)
    issuer_records = issuer_records_value if issuer_records_are_list else []

    hold_invariant_passed = (
        runtime_status in VALID_RUNTIME_ENFORCEMENT_STATUSES
        and issuer_records_are_list
        and (
            (runtime_status == "not_activated" and not issuer_records)
            or (runtime_status == "activated" and bool(issuer_records))
        )
    )
    report.add(
        "trust_issuers_runtime_enforcement_invariant",
        hold_invariant_passed,
        (
            "Trust enforcement is held with no published issuer records"
            if runtime_status == "not_activated" and not issuer_records
            else (
                "Trust enforcement is activated with issuer records subject to key validation"
                if runtime_status == "activated" and issuer_records
                else (
                    "runtimeEnforcement must declare not_activated with an empty issuer list "
                    "or activated with at least one issuer record"
                )
            )
        ),
    )

    hidden_trust_material = []

    def collect_hidden_trust_material(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                nested_path = f"{path}.{key}"
                if key.casefold() in {
                    "did",
                    "issuer",
                    "issuers",
                    "publickeyjwk",
                    "jwk",
                    "trustanchor",
                    "trustanchors",
                }:
                    hidden_trust_material.append(nested_path)
                collect_hidden_trust_material(nested_value, nested_path)
        elif isinstance(value, list):
            for index, nested_value in enumerate(value):
                collect_hidden_trust_material(nested_value, f"{path}[{index}]")

    for field_name in ("rotationPolicy", "verificationRequirements"):
        collect_hidden_trust_material(issuers.get(field_name), field_name)

    report.add(
        "trust_issuers_no_hidden_trust_material",
        not hidden_trust_material,
        (
            "No issuer or key material appears outside the issuer list"
            if not hidden_trust_material
            else "Trust material outside issuer list: "
            + ", ".join(hidden_trust_material)
        ),
    )

    missing_fields = []
    invalid_statuses = []
    expired_issuers = []
    placeholder_keys = []
    unusable_public_keys = []
    invalid_status_list_type = []
    missing_status_list = []

    for issuer in issuer_records:
        did = issuer.get("did", "unknown")
        for field_name in (
            "did",
            "name",
            "status",
            "validFrom",
            "validUntil",
            "statusListCredential",
            "statusListType",
            "publicKeyJwk",
        ):
            if field_name not in issuer:
                missing_fields.append(f"{did}:{field_name}")

        status = issuer.get("status", "")
        if status not in VALID_ISSUER_STATUSES:
            invalid_statuses.append(f"{did} ({status})")

        valid_until = _parse_iso_date(str(issuer.get("validUntil", "") or ""))
        if valid_until is None:
            missing_fields.append(f"{did}:validUntil(parse)")
        elif valid_until < current_date and status not in {"expired", "revoked"}:
            expired_issuers.append(f"{did} ({valid_until.isoformat()})")

        status_list_credential = str(issuer.get("statusListCredential", "") or "")
        status_list_type = str(issuer.get("statusListType", "") or "")
        if not status_list_credential:
            missing_status_list.append(did)
        if status_list_type != "BitstringStatusListCredential":
            invalid_status_list_type.append(f"{did} ({status_list_type})")

        public_key_jwk = issuer.get("publicKeyJwk", {})
        if isinstance(public_key_jwk, dict):
            for key_name, key_value in public_key_jwk.items():
                if isinstance(key_value, str) and PLACEHOLDER_RE.search(key_value):
                    placeholder_keys.append(f"{did}:{key_name}")
        p256_error = _validate_p256_public_jwk(public_key_jwk)
        if p256_error:
            unusable_public_keys.append(f"{did} ({p256_error})")

    report.add(
        "trust_issuers_required_fields",
        not missing_fields,
        (
            "All issuers have the required fields"
            if not missing_fields
            else f"Missing or unparsable issuer fields: {', '.join(missing_fields)}"
        ),
    )
    report.add(
        "trust_issuers_status_valid",
        not invalid_statuses,
        (
            "All issuer statuses are valid"
            if not invalid_statuses
            else f"Invalid issuer statuses: {', '.join(invalid_statuses)}"
        ),
    )
    report.add(
        "trust_issuers_validity_window",
        not expired_issuers,
        (
            "No active/pilot/planned issuers are expired"
            if not expired_issuers
            else f"Expired issuers still marked usable: {', '.join(expired_issuers)}"
        ),
    )
    report.add(
        "trust_issuers_status_list_type",
        not invalid_status_list_type and not missing_status_list,
        (
            "All issuers use BitstringStatusListCredential with a populated status list"
            if not invalid_status_list_type and not missing_status_list
            else (
                f"Invalid status list type: {', '.join(invalid_status_list_type)}"
                if invalid_status_list_type
                else f"Issuers missing status list credentials: {', '.join(missing_status_list)}"
            )
        ),
    )
    report.add(
        "trust_issuers_placeholder_keys",
        not placeholder_keys,
        (
            "No placeholder public keys detected"
            if not placeholder_keys
            else f"Placeholder public keys detected: {', '.join(placeholder_keys)}"
        ),
    )
    report.add(
        "trust_issuers_public_key_jwk_usable",
        not unusable_public_keys,
        (
            "No issuer JWKs are published while trust enforcement is held"
            if runtime_status == "not_activated" and not issuer_records
            else (
                "All issuer public P-256 JWKs are cryptographically usable"
                if not unusable_public_keys
                else f"unusable P-256 JWKs detected: {', '.join(unusable_public_keys)}"
            )
        ),
    )

    md_content = _check_markdown_refs(
        md_path, report, check_prefix="trust_issuers_markdown", repo_root=repo_root
    )
    if md_content:
        deprecated_terms_found = [
            term for term in DEPRECATED_STATUS_TERMS if term in md_content
        ]
        report.add(
            "trust_issuers_markdown_no_deprecated_terms",
            not deprecated_terms_found,
            (
                "Trust issuer markdown uses the current status-list terminology"
                if not deprecated_terms_found
                else f"Deprecated terminology still present: {', '.join(deprecated_terms_found)}"
            ),
        )
        md_version = DATE_MARKER_RE.search(md_content)
        json_version = str(issuers.get("version", "") or "")
        aligned = not (
            md_version and json_version and md_version.group(1) != json_version
        )
        report.add(
            "trust_issuers_markdown_version_alignment",
            aligned,
            (
                "Markdown trust registry version aligns with the JSON registry"
                if aligned
                else f"Markdown trust registry version {md_version.group(1)} does not match JSON version {json_version}"
            ),
        )


def check_supporting_docs(
    *,
    onboarding_flow_path: Path,
    operating_model_path: Path,
    report: ValidationReport,
    repo_root: Path,
) -> None:
    onboarding_content = _check_markdown_refs(
        onboarding_flow_path,
        report,
        check_prefix="onboarding_flow",
        repo_root=repo_root,
    )
    if onboarding_content:
        deprecated_terms_found = [
            term for term in DEPRECATED_STATUS_TERMS if term in onboarding_content
        ]
        report.add(
            "onboarding_flow_no_deprecated_terms",
            not deprecated_terms_found,
            (
                "Onboarding flow uses current status-list terminology"
                if not deprecated_terms_found
                else f"Deprecated terminology still present: {', '.join(deprecated_terms_found)}"
            ),
        )
    _check_markdown_refs(
        operating_model_path,
        report,
        check_prefix="operating_model_raci",
        repo_root=repo_root,
    )


def _load_register_count(
    register_meta_path: Path,
    register_csv_path: Path,
    *,
    allow_metadata_fallback: bool = True,
) -> Optional[int]:
    if register_csv_path.exists():
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(register_csv_path, flags)
        except OSError as exc:
            raise RegisterFileReadError(f"register CSV cannot be read: {exc}") from exc
        try:
            try:
                opened_stat = os.fstat(descriptor)
                opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
                if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                    raise RegisterFileReadError(
                        "register CSV must be an independent regular file"
                    )
                if opened_stat.st_size > MAX_REGISTER_CSV_BYTES:
                    raise RegisterFileReadError("register CSV exceeds size limit")
                chunks: list[bytes] = []
                remaining = MAX_REGISTER_CSV_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)
                if len(content) > MAX_REGISTER_CSV_BYTES:
                    raise RegisterFileReadError("register CSV exceeds size limit")
                path_stat = register_csv_path.lstat()
            except OSError as exc:
                raise RegisterFileReadError(
                    f"register CSV cannot be read: {exc}"
                ) from exc
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino) != opened_identity
            or path_stat.st_nlink != 1
        ):
            raise RegisterFileReadError("register CSV changed while being read")
        try:
            register_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RegisterFileReadError("register CSV is not UTF-8") from exc
        previous_field_limit = csv.field_size_limit(MAX_CSV_FIELD_BYTES)
        try:
            reader = csv.DictReader(io.StringIO(register_text, newline=""), strict=True)
            if any(
                len(header.encode("utf-8")) > MAX_CSV_FIELD_BYTES
                for header in (reader.fieldnames or [])
            ):
                raise RegisterFileReadError("register CSV field exceeds byte limit")
            count = 0
            for row in reader:
                count += 1
                if count > MAX_REGISTER_ROWS:
                    raise RegisterFileReadError("register CSV row count exceeds limit")
                if None in row or any(value is None for value in row.values()):
                    raise RegisterFileReadError(
                        "register CSV column count does not match header"
                    )
                if any(
                    len(value.encode("utf-8")) > MAX_CSV_FIELD_BYTES
                    for value in row.values()
                ):
                    raise RegisterFileReadError("register CSV field exceeds byte limit")
            return count
        except csv.Error as exc:
            raise RegisterFileReadError(f"register CSV is invalid: {exc}") from exc
        finally:
            csv.field_size_limit(previous_field_limit)

    if allow_metadata_fallback and register_meta_path.exists():
        try:
            meta = _read_json(register_meta_path)
        except (json.JSONDecodeError, JsonFileReadError):
            meta = {}
        count = meta.get("count")
        if isinstance(count, int) and count >= 0:
            return count
    return None


def check_edc_bundle(
    edc_dir: Path,
    report: ValidationReport,
    *,
    register_meta_path: Path,
    register_csv_path: Path,
    allow_register_metadata_fallback: bool = True,
) -> None:
    policies_path = edc_dir / "policies.json"
    assets_path = edc_dir / "assets.json"
    contracts_path = edc_dir / "contract-definitions.json"

    for check_name, path in (
        ("edc_policies_exists", policies_path),
        ("edc_assets_exists", assets_path),
        ("edc_contract_definitions_exists", contracts_path),
    ):
        report.add(
            check_name,
            path.exists(),
            f"Found: {path}" if path.exists() else f"Missing EDC artifact: {path}",
        )

    if not (
        policies_path.exists() and assets_path.exists() and contracts_path.exists()
    ):
        return

    try:
        policies = _read_json(policies_path)
        assets = _read_json(assets_path)
        contracts = _read_json(contracts_path)
    except (json.JSONDecodeError, JsonFileReadError) as exc:
        report.add("edc_bundle_valid_json", False, f"Invalid EDC JSON: {exc}")
        return

    report.add("edc_bundle_valid_json", True, "EDC bundle JSON files are valid")

    structurally_valid = (
        isinstance(policies, list)
        and isinstance(assets, list)
        and isinstance(contracts, list)
        and all(isinstance(asset, dict) for asset in assets)
        and all(isinstance(contract, dict) for contract in contracts)
        and all(
            isinstance(policy, dict)
            and isinstance(policy.get("uid"), str)
            and isinstance(policy.get("policy"), dict)
            and all(
                isinstance(policy["policy"].get(field), list)
                and all(isinstance(item, dict) for item in policy["policy"][field])
                for field in ("permissions", "prohibitions")
            )
            and all(
                isinstance(permission.get("constraints"), list)
                and all(
                    isinstance(constraint, dict)
                    for constraint in permission["constraints"]
                )
                for permission in policy["policy"]["permissions"]
            )
            for policy in policies
        )
    )
    report.add(
        "edc_bundle_structure",
        structurally_valid,
        (
            "EDC bundle has valid policy, asset and contract collection shapes"
            if structurally_valid
            else "EDC bundle has malformed policy, asset or contract structure"
        ),
    )
    if not structurally_valid:
        return

    policy_ids = [policy["uid"] for policy in policies]
    asset_ids = []
    for asset in assets:
        payload = asset.get("asset")
        properties = payload.get("properties") if isinstance(payload, dict) else None
        asset_ids.append(
            properties.get("asset:prop:id") if isinstance(properties, dict) else None
        )
    contract_ids = [contract.get("id") for contract in contracts]
    unique_ids = all(
        all(isinstance(value, str) and bool(value.strip()) for value in ids)
        and len(ids) == len(set(ids))
        for ids in (policy_ids, asset_ids, contract_ids)
    )
    report.add(
        "edc_unique_ids",
        unique_ids,
        (
            "All EDC policy, asset and contract IDs are unique and non-empty"
            if unique_ids
            else "EDC policy, asset or contract IDs are missing or duplicated"
        ),
    )
    known_policies = set(policy_ids)
    selected_assets = []
    refs_resolve = unique_ids
    for contract in contracts:
        additional = contract.get("additionalPolicies")
        selectors = contract.get("assetsSelector")
        if not (
            isinstance(additional, list)
            and all(isinstance(ref, str) for ref in additional)
            and isinstance(selectors, list)
            and len(selectors) == 1
            and isinstance(selectors[0], dict)
            and selectors[0].get("operandLeft") == "asset:prop:id"
            and selectors[0].get("operator") == "="
            and isinstance(selectors[0].get("operandRight"), str)
        ):
            refs_resolve = False
            continue
        selected_assets.append(selectors[0]["operandRight"])
        refs_resolve = refs_resolve and all(
            ref in known_policies
            for ref in (
                contract.get("accessPolicyId"),
                contract.get("contractPolicyId"),
                *additional,
            )
        )
    refs_resolve = refs_resolve and sorted(selected_assets) == sorted(asset_ids)
    report.add(
        "edc_references_resolve",
        refs_resolve,
        (
            "All EDC contracts reference installed policies and cover assets once"
            if refs_resolve
            else "EDC contracts have missing policy references or mismatched asset selectors"
        ),
    )

    allow_all_found = []
    forbidden_names = []
    forbidden_patterns = ["allow-all", "allow_all", "allowall", "permit-all"]

    for policy in policies:
        uid = policy.get("uid", "unknown")
        uid_lower = uid.lower()
        for pattern in forbidden_patterns:
            if pattern in uid_lower:
                forbidden_names.append(uid)
                break

        permissions = policy.get("policy", {}).get("permissions", [])
        for perm in permissions:
            if not _has_effective_permission_constraints(
                perm,
                collection="constraints",
                left="leftExpression",
                right="rightExpression",
                allowed_operators=frozenset({"EQ", "IN"}),
            ):
                allow_all_found.append(uid)
                break

    report.add(
        "edc_no_allow_all",
        not allow_all_found,
        (
            f"No allow-all patterns in {len(policies)} EDC policies"
            if not allow_all_found
            else f"EDC bundle contains allow-all patterns: {', '.join(allow_all_found)}"
        ),
    )
    report.add(
        "edc_no_forbidden_names",
        not forbidden_names,
        (
            "EDC policy names avoid forbidden allow-all patterns"
            if not forbidden_names
            else f"EDC policies with forbidden names: {', '.join(forbidden_names)}"
        ),
    )

    try:
        expected_count = _load_register_count(
            register_meta_path,
            register_csv_path,
            allow_metadata_fallback=allow_register_metadata_fallback,
        )
    except RegisterFileReadError as exc:
        report.add("edc_register_count_available", False, str(exc))
        return
    if expected_count is None:
        report.add(
            "edc_register_count_available",
            False,
            "Current register count is unavailable; freshness check skipped",
            severity="warning",
        )
        return

    report.add(
        "edc_register_count_available",
        True,
        f"Current register count: {expected_count}",
    )
    report.add(
        "edc_assets_match_contract_definitions",
        len(assets) == len(contracts),
        (
            "Asset and contract-definition counts match"
            if len(assets) == len(contracts)
            else f"assets.json has {len(assets)} rows but contract-definitions.json has {len(contracts)} rows"
        ),
    )
    report.add(
        "edc_assets_match_register_count",
        len(assets) == expected_count,
        (
            "EDC assets count matches the current register"
            if len(assets) == expected_count
            else f"assets.json has {len(assets)} rows but the current register has {expected_count}"
        ),
    )
    report.add(
        "edc_contract_definitions_match_register_count",
        len(contracts) == expected_count,
        (
            "EDC contract-definition count matches the current register"
            if len(contracts) == expected_count
            else f"contract-definitions.json has {len(contracts)} rows but the current register has {expected_count}"
        ),
    )


def check_deliverable(
    deliverable_path: Path, report: ValidationReport, *, repo_root: Path
) -> None:
    content = _check_markdown_refs(
        deliverable_path, report, check_prefix="e6_deliverable", repo_root=repo_root
    )
    if not content:
        return

    has_wsl_refs = _contains_local_source_path(content)
    report.add(
        "e6_deliverable_no_wsl_refs",
        not has_wsl_refs,
        (
            "The published E6 deliverable contains no WSL-specific references"
            if not has_wsl_refs
            else "The published E6 deliverable still contains WSL-specific source references"
        ),
    )


def print_report(report: ValidationReport) -> None:
    print("\n" + "=" * 60)
    print("E6 GOVERNANCE VALIDATION REPORT")
    print("=" * 60 + "\n")
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        icon = "[+]" if result.passed else "[-]"
        severity_tag = f"[{result.severity.upper()}]" if not result.passed else ""
        print(f"{icon} {result.name}: {status} {severity_tag}")
        print(f"    {result.message}\n")

    print("=" * 60)
    if report.passed and report.warning_count == 0:
        print("RESULT: ALL CHECKS PASSED")
    elif report.passed:
        print(f"RESULT: PASSED WITH WARNINGS ({report.warning_count} warnings)")
    else:
        print(
            f"RESULT: FAILED ({report.error_count} errors, {report.warning_count} warnings)"
        )
    print("=" * 60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate E6 governance artifacts")
    parser.add_argument(
        "--policy-registry",
        default=REPO_ROOT / "docs/policies/policy_registry.json",
        help="Path to policy registry",
    )
    parser.add_argument(
        "--data-product-terms",
        default=REPO_ROOT / "docs/policies/data_product_terms.md",
        help="Path to data product terms",
    )
    parser.add_argument(
        "--trust-issuers",
        default=REPO_ROOT / "docs/governance/trust_issuers",
        help="Path stem to trust issuers (without extension)",
    )
    parser.add_argument(
        "--onboarding-flow",
        default=REPO_ROOT / "docs/governance/onboarding_flow.md",
        help="Path to onboarding flow doc",
    )
    parser.add_argument(
        "--operating-model",
        default=REPO_ROOT / "docs/governance/operating_model_raci.md",
        help="Path to operating model doc",
    )
    parser.add_argument(
        "--deliverable",
        default=REPO_ROOT
        / "deliverables/E06-governance/final/e06-gobernanza-politica-datos-v2026-06-09.md",
        help="Path to the published E6 deliverable",
    )
    parser.add_argument(
        "--edc-dir",
        default=REPO_ROOT / "configs/edc",
        help="Path to the EDC bundle directory",
    )
    parser.add_argument(
        "--register-meta",
        default=REPO_ROOT / "data/processed/e1_dataset_register.meta.json",
        help="Path to the current register metadata file",
    )
    parser.add_argument(
        "--register-csv",
        default=None,
        help="Path to the current register CSV file",
    )
    parser.add_argument(
        "--today",
        default="",
        help="Override current date (YYYY-MM-DD) for deterministic checks",
    )
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors"
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    today = (
        _parse_iso_date(args.today) if args.today else datetime.now(timezone.utc).date()
    )
    if today is None:
        print("Invalid --today value; expected YYYY-MM-DD", file=sys.stderr)
        return 2

    report = ValidationReport()
    registry = check_policy_registry(Path(args.policy_registry), report)
    check_data_product_terms(
        Path(args.data_product_terms), registry, report, repo_root=REPO_ROOT
    )
    check_trust_issuers(
        Path(args.trust_issuers), report, repo_root=REPO_ROOT, current_date=today
    )
    check_supporting_docs(
        onboarding_flow_path=Path(args.onboarding_flow),
        operating_model_path=Path(args.operating_model),
        report=report,
        repo_root=REPO_ROOT,
    )
    register_csv_path = Path(
        args.register_csv or REPO_ROOT / "data/processed/e1_dataset_register.csv"
    )
    check_edc_bundle(
        Path(args.edc_dir),
        report,
        register_meta_path=Path(args.register_meta),
        register_csv_path=register_csv_path,
        allow_register_metadata_fallback=args.register_csv is None,
    )
    check_deliverable(Path(args.deliverable), report, repo_root=REPO_ROOT)

    if args.json:
        print(
            json.dumps(
                {
                    "passed": report.passed,
                    "error_count": report.error_count,
                    "warning_count": report.warning_count,
                    "results": [
                        {
                            "name": result.name,
                            "passed": result.passed,
                            "message": result.message,
                            "severity": result.severity,
                        }
                        for result in report.results
                    ],
                },
                indent=2,
            )
        )
    else:
        print_report(report)

    if report.error_count > 0:
        return 1
    if args.strict and report.warning_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
