#!/usr/bin/env python3
"""Offline structural validator for the public SDS AWS profile; makes no cloud calls."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import stat
from pathlib import Path
from typing import Any

PROFILE = "sds-aws-ec2-rds-v1"
EXPECTED_FILES = {
    "README.md",
    "operator.example.json",
    "validate.py",
    "terraform_offline_check.py",
    "terraform/activation.tftest.hcl",
    "terraform/.terraform.lock.hcl",
    "terraform/checks.tf",
    "terraform/main.tf",
    "terraform/outputs.tf",
    "terraform/variables.tf",
    "terraform/versions.tf",
}
EXPECTED_OPERATOR_KEYS = {
    "profile",
    "aws_region",
    "name_prefix",
    "allowed_public_cidrs",
    "certificate_arn",
    "ami_id",
    "image_ref",
    "app_secret_version_id",
    "runtime_activation_enabled",
    "instance_type",
    "db_instance_class",
    "db_allocated_storage_gib",
    "backup_retention_days",
    "log_retention_days",
    "tags",
}
PLACEHOLDER_MARKERS = ("000000000000", "00000000-0000-0000-0000-000000000000")
LOG_RETENTION_DAYS = frozenset(
    {30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653}
)
PARTITION_DNS_SUFFIX = {
    "aws": "amazonaws.com",
    "aws-cn": "amazonaws.com.cn",
    "aws-us-gov": "amazonaws.com",
}


class ProfileError(ValueError):
    pass


def _load_unique_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProfileError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    raw = path.read_bytes()
    if len(raw) > 128 * 1024:
        raise ProfileError("operator file exceeds byte budget")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError("operator file is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ProfileError("operator file must be a JSON object")
    return payload


def _assert_regular_tree(root: Path) -> None:
    observed: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ProfileError(f"symlink rejected: {relative}")
        if path.is_file():
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProfileError(f"non-independent file rejected: {relative}")
            observed.add(relative)
    if observed != EXPECTED_FILES:
        raise ProfileError(
            f"profile inventory mismatch: missing={sorted(EXPECTED_FILES - observed)} "
            f"extra={sorted(observed - EXPECTED_FILES)}"
        )


def _validate_operator(payload: dict[str, Any]) -> bool:
    if set(payload) != EXPECTED_OPERATOR_KEYS or payload.get("profile") != PROFILE:
        raise ProfileError("operator contract shape/profile mismatch")
    region = payload["aws_region"]
    prefix = payload["name_prefix"]
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}-[a-z]+-[1-9]", region):
        raise ProfileError("invalid aws_region")
    if not isinstance(prefix, str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,23}", prefix):
        raise ProfileError("invalid name_prefix")
    cidrs = payload["allowed_public_cidrs"]
    if (
        not isinstance(cidrs, list)
        or not cidrs
        or not all(isinstance(cidr, str) for cidr in cidrs)
        or len(cidrs) != len(set(cidrs))
    ):
        raise ProfileError("allowed_public_cidrs must be a non-empty unique list")
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=True)
        except (TypeError, ValueError) as exc:
            raise ProfileError("invalid allowed public CIDR") from exc
        if network.version != 4 or str(network) != cidr:
            raise ProfileError("allowed public CIDRs must be canonical IPv4 strings")
    strings = (
        "certificate_arn",
        "ami_id",
        "image_ref",
        "app_secret_version_id",
        "instance_type",
        "db_instance_class",
    )
    if any(not isinstance(payload[key], str) or not payload[key] for key in strings):
        raise ProfileError("operator identity fields must be non-empty strings")
    if not re.fullmatch(r"ami-[0-9a-f]{8,17}", payload["ami_id"]):
        raise ProfileError("invalid AMI identifier")
    certificate = re.fullmatch(
        r"arn:(?P<partition>[^:]+):acm:(?P<region>[^:]+):(?P<account>[0-9]{12}):certificate/[0-9a-f-]+",
        payload["certificate_arn"],
    )
    if certificate is None or certificate.group("region") != region:
        raise ProfileError("invalid ACM certificate ARN")
    dns_suffix = PARTITION_DNS_SUFFIX.get(certificate.group("partition"))
    if dns_suffix is None:
        raise ProfileError("unsupported AWS partition")
    expected_image = (
        rf"{re.escape(certificate.group('account'))}\.dkr\.ecr\."
        rf"{re.escape(region)}\.{re.escape(dns_suffix)}/"
        rf"{re.escape(prefix)}/sds-api@sha256:[0-9a-f]{{64}}"
    )
    if not re.fullmatch(expected_image, payload["image_ref"]):
        raise ProfileError("image_ref must be digest-pinned in the profile ECR repository")
    for key in ("db_allocated_storage_gib", "backup_retention_days", "log_retention_days"):
        if not isinstance(payload[key], int) or isinstance(payload[key], bool):
            raise ProfileError(f"{key} must be an integer")
    if payload["db_allocated_storage_gib"] < 20:
        raise ProfileError("db_allocated_storage_gib must be at least 20 GiB for RDS gp3")
    if not 7 <= payload["backup_retention_days"] <= 35:
        raise ProfileError("backup_retention_days must be in the RDS range 7..35")
    if payload["log_retention_days"] not in LOG_RETENTION_DAYS:
        raise ProfileError("log_retention_days is not supported by CloudWatch Logs")
    if not isinstance(payload["tags"], dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in payload["tags"].items()
    ):
        raise ProfileError("tags must be a string map")
    if not isinstance(payload["runtime_activation_enabled"], bool):
        raise ProfileError("runtime_activation_enabled must be a boolean")
    serialized = json.dumps(payload, sort_keys=True)
    return payload["runtime_activation_enabled"] and not any(
        marker in serialized for marker in PLACEHOLDER_MARKERS
    )


def _validate_terraform(root: Path) -> None:
    text = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in sorted(EXPECTED_FILES)
        if relative.endswith(".tf")
    )
    required = (
        'image_tag_mutability = "IMMUTABLE"',
        "manage_master_user_password",
        "multi_az",
        "prevent_destroy",
        "AWSManagedRulesCommonRuleSet",
        "AmazonSSMManagedInstanceCore",
        "@sha256:",
    )
    for token in required:
        if token not in text:
            raise ProfileError(f"Terraform security contract missing: {token}")
    for attribute in ("multi_az", "publicly_accessible", "associate_public_ip_address"):
        expected = "true" if attribute == "multi_az" else "false"
        if not re.search(rf"(?m)^\s*{attribute}\s*=\s*{expected}\s*$", text):
            raise ProfileError(f"Terraform security contract has unsafe {attribute}")
    if re.search(r"(?mi)^\s*(access_key|secret_key)\s*=", text) or re.search(
        r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}", text
    ):
        raise ProfileError("Terraform contains a forbidden credential surface")


def validate_profile(operator_path: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parent if "__file__" in globals() else operator_path.parent
    _assert_regular_tree(root)
    payload = _load_unique_json(operator_path)
    deployable = _validate_operator(payload)
    _validate_terraform(root)
    return {
        "valid": True,
        "deployable_inputs": deployable,
        "profile": PROFILE,
        "cloud_calls": 0,
        "terraform_apply": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operator", type=Path)
    args = parser.parse_args()
    try:
        result = validate_profile(args.operator)
    except (OSError, ProfileError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if globals().get("__name__") == "__main__":
    raise SystemExit(main())
