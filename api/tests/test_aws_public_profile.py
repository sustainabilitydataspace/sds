"""Offline contract for the public, no-apply SDS AWS deployment profile."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = REPO_ROOT / "api" / "deploy" / "aws-ec2-rds"


def test_aws_profile_is_complete_secret_free_and_fail_closed():
    required = {
        "README.md",
        "operator.example.json",
        "validate.py",
        "terraform_offline_check.py",
        "terraform/activation.tftest.hcl",
        "terraform/.terraform.lock.hcl",
        "terraform/versions.tf",
        "terraform/variables.tf",
        "terraform/main.tf",
        "terraform/checks.tf",
        "terraform/outputs.tf",
    }
    assert {
        path.relative_to(PROFILE).as_posix()
        for path in PROFILE.rglob("*")
        if path.is_file()
    } == required

    operator = json.loads(
        (PROFILE / "operator.example.json").read_text(encoding="utf-8")
    )
    assert operator["image_ref"].endswith("@sha256:" + "0" * 64)
    assert operator["app_secret_version_id"] == "00000000-0000-0000-0000-000000000000"
    assert "password" not in json.dumps(operator).lower()

    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    variables = (PROFILE / "terraform" / "variables.tf").read_text(encoding="utf-8")
    checks = (PROFILE / "terraform" / "checks.tf").read_text(encoding="utf-8")
    combined = "\n".join((main, variables, checks))
    versions = (PROFILE / "terraform" / "versions.tf").read_text(encoding="utf-8")

    for token in (
        'resource "aws_vpc"',
        'resource "aws_vpc_endpoint"',
        'resource "aws_ecr_repository"',
        'image_tag_mutability = "IMMUTABLE"',
        'resource "aws_db_instance"',
        "multi_az",
        'resource "aws_lb"',
        'resource "aws_wafv2_web_acl"',
        'resource "aws_kms_key"',
        'resource "aws_secretsmanager_secret"',
        'resource "aws_cloudwatch_log_group"',
        'resource "aws_backup_plan"',
        'resource "aws_iam_role"',
        'resource "aws_instance"',
        "associate_public_ip_address = false",
        'resource "aws_vpc_security_group_egress_rule"',
    ):
        assert token in combined

    assert "0.0.0.0/0" not in main.split('resource "aws_security_group" "app"', 1)[-1]
    assert "lifecycle" in main and "prevent_destroy" in main
    assert "app_secret_version_id" in variables
    assert "image_ref" in variables and "@sha256:" in checks
    assert "allowed_public_cidrs" in variables
    assert "ami_id" in variables
    assert "certificate_arn" in variables
    assert 'backend "s3"' in versions and "encrypt = true" in versions
    assert 'version = "= 5.100.0"' in versions


def test_aws_profile_validator_accepts_example_without_aws_calls():
    namespace: dict[str, object] = {}
    exec((PROFILE / "validate.py").read_text(encoding="utf-8"), namespace)

    validate = cast(Callable[[Path], dict[str, Any]], namespace["validate_profile"])
    result = validate(PROFILE / "operator.example.json")

    assert result["valid"] is True
    assert result["profile"] == "sds-aws-ec2-rds-v1"
    assert result["cloud_calls"] == 0
    assert result["terraform_apply"] is False


def test_aws_profile_has_offline_root_gate_and_read_only_ci():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "aws-profile-ci.yml").read_text(
        encoding="utf-8"
    )

    assert "aws-profile-check:" in makefile
    assert "api/deploy/aws-ec2-rds/validate.py" in makefile
    assert "test_aws_public_profile.py" in makefile
    assert "permissions:\n  contents: read" in workflow
    assert "terraform init -backend=false" in workflow
    assert "terraform validate" in workflow
    assert "terraform plan" not in workflow
    assert "terraform apply" not in workflow
    assert "aws-actions/configure-aws-credentials" not in workflow
    assert 'runpy.run_path("api/tests/test_aws_public_profile.py")' in workflow


def test_aws_runtime_secret_materialization_is_restart_safe(tmp_path):
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    match = re.search(
        r"python3 - /run/sds-api/runtime\.json /run/sds-api/runtime\.env <<'PY'\n"
        r"(?P<body>.*?)\n\s+PY",
        main,
        re.DOTALL,
    )
    assert match is not None
    script = textwrap.dedent(match.group("body"))
    source = tmp_path / "runtime.json"
    destination = tmp_path / "runtime.env"
    destination.write_text("STALE=true\n", encoding="utf-8")
    payload = {
        "ALLOWED_ORIGINS": "https://sds.example.invalid",
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql://runtime:" + "synthetic@db.example.invalid/sds",
        "EXPORT_SIGNING_SECRET": "synthetic-export-signing-secret-1234567890",
        "JWT_SECRET_KEY": "synthetic-jwt-signing-secret-123456789012",
        "LOG_LEVEL": "INFO",
        "REQUIRE_DATABASE": "true",
        "SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED": "true",
        "TRUSTED_PROXY_IPS": "10.42.0.0/16",
        "USE_POSTGRES_UNITS": "true",
        "VALUE_REVISION_API_ENABLED": "true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED": "true",
        "VALUE_REVISION_PRIMARY_READ_PATH": "revision",
    }
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", script, str(source), str(destination)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "STALE=true" not in destination.read_text(encoding="utf-8")
    assert "TRUSTED_PROXY_IPS=10.42.0.0/16" in destination.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.tmp")) == []


def test_aws_runtime_secret_rejects_non_production_database_mode(tmp_path):
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    match = re.search(
        r"python3 - /run/sds-api/runtime\.json /run/sds-api/runtime\.env <<'PY'\n"
        r"(?P<body>.*?)\n\s+PY",
        main,
        re.DOTALL,
    )
    assert match is not None
    script = textwrap.dedent(match.group("body"))
    source = tmp_path / "runtime.json"
    destination = tmp_path / "runtime.env"
    payload = {
        "ALLOWED_ORIGINS": "https://sds.example.invalid",
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql://runtime:" + "synthetic@db.example.invalid/sds",
        "EXPORT_SIGNING_SECRET": "synthetic-export-signing-secret-1234567890",
        "JWT_SECRET_KEY": "synthetic-jwt-signing-secret-123456789012",
        "LOG_LEVEL": "INFO",
        "REQUIRE_DATABASE": "false",
        "SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED": "true",
        "TRUSTED_PROXY_IPS": "10.42.0.0/16",
        "USE_POSTGRES_UNITS": "true",
        "VALUE_REVISION_API_ENABLED": "true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED": "true",
        "VALUE_REVISION_PRIMARY_READ_PATH": "revision",
    }
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", script, str(source), str(destination)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert not destination.exists()


def test_aws_bootstrap_removes_raw_secret_on_success_and_failure():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")

    trap = "trap cleanup_runtime_source EXIT"
    fetch = "aws secretsmanager get-secret-value"
    explicit_cleanup = "cleanup_runtime_source\n    trap - EXIT"
    assert trap in main
    assert main.index(trap) < main.index(fetch)
    assert explicit_cleanup in main


def test_aws_runtime_directory_is_recreated_by_systemd_after_reboot():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    unit = main.split("cat >/etc/systemd/system/sds-api.service <<'UNIT'", 1)[1].split(
        "\n    UNIT", 1
    )[0]
    assert "RuntimeDirectory=sds-api" in unit
    assert "RuntimeDirectoryMode=0700" in unit
    assert "ExecStart=/usr/local/sbin/sds-api-start" in unit


def test_aws_runtime_checks_approved_schema_before_replacing_container():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    assert '"SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED"' in main
    assert '"SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED": "true"' in main
    assert (
        main.index("cleanup_runtime_source\n    trap - EXIT")
        < main.index("--check-head-read-only")
        < main.index("if docker inspect sds-api")
    )
    assert "--entrypoint python" in main
    assert "src.database.init_db --check-head-read-only" in main


def test_aws_instance_waits_for_network_rules_and_cannot_self_approve_traffic():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    variables = (PROFILE / "terraform" / "variables.tf").read_text(encoding="utf-8")
    instance = main.split('resource "aws_instance" "app" {', 1)[1].split(
        'resource "aws_lb" "this" {', 1
    )[0]
    for rule in (
        "app_from_alb",
        "alb_to_app",
        "db_from_app",
        "app_to_db",
        "endpoints_from_app",
        "app_to_endpoints",
        "app_to_s3",
        "app_dns_udp",
        "app_dns_tcp",
        "app_ntp",
    ):
        assert re.search(
            rf"aws_vpc_security_group_(?:ingress|egress)_rule\.{rule}\b", instance
        )
    assert "aws_route_table_association.app" in instance
    assert 'resource "aws_lb_target_group_attachment"' not in main
    assert 'variable "traffic_promotion_enabled"' not in variables
    assert 'variable "traffic_approved_image_ref"' not in variables
    assert 'variable "traffic_approved_instance_id"' not in variables
    outputs = (PROFILE / "terraform" / "outputs.tf").read_text(encoding="utf-8")
    assert 'output "api_target_group_arn"' in outputs
    assert "aws_lb_target_group.api.arn" in outputs
    cases = (PROFILE / "terraform" / "activation.tftest.hcl").read_text(
        encoding="utf-8"
    )
    assert "traffic_promotion_enabled" not in cases


def test_local_aws_profile_gate_requires_mocked_terraform_plans():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("aws-profile-check:", 1)[1].split("\n\n", 1)[0]
    assert "terraform_offline_check.py" in target


def test_aws_profile_separates_foundation_from_runtime_activation():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    variables = (PROFILE / "terraform" / "variables.tf").read_text(encoding="utf-8")
    checks = (PROFILE / "terraform" / "checks.tf").read_text(encoding="utf-8")
    outputs = (PROFILE / "terraform" / "outputs.tf").read_text(encoding="utf-8")
    readme = (PROFILE / "README.md").read_text(encoding="utf-8")

    assert 'variable "runtime_activation_enabled"' in variables
    assert "default     = false" in variables
    assert (
        len(
            re.findall(
                r"(?m)^\s*count\s*=\s*var\.runtime_activation_enabled \? 1 : 0$", main
            )
        )
        >= 1
    )
    assert "!var.runtime_activation_enabled ||" in checks
    assert "try(aws_instance.app[0].id, null)" in outputs
    assert "foundation" in readme.lower()
    assert "runtime_activation_enabled=true" in readme


def test_aws_runtime_service_recovers_boundedly_from_process_failure():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")

    assert "Restart=on-failure" in main
    assert "RestartSec=15" in main
    assert "StartLimitIntervalSec=300" in main
    assert "StartLimitBurst=3" in main


def test_aws_logs_kms_key_authorizes_only_bound_cloudwatch_log_groups():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")

    assert 'data "aws_iam_policy_document" "logs_kms"' in main
    assert '"logs.${var.aws_region}.amazonaws.com"' in main
    assert 'variable = "kms:EncryptionContext:aws:logs:arn"' in main
    assert 'test     = "ArnEquals"' in main
    assert (
        '"arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/sds/${var.name_prefix}/api"'
        in main
    )
    assert (
        '"arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:aws-waf-logs-${var.name_prefix}-sds"'
        in main
    )
    assert (
        "policy                  = data.aws_iam_policy_document.logs_kms.json" in main
    )


def test_aws_operator_validator_rejects_provider_invalid_domains():
    namespace: dict[str, object] = {}
    exec((PROFILE / "validate.py").read_text(encoding="utf-8"), namespace)
    validate_operator = cast(
        Callable[[dict[str, Any]], bool], namespace["_validate_operator"]
    )
    error = cast(type[ValueError], namespace["ProfileError"])
    base = json.loads((PROFILE / "operator.example.json").read_text(encoding="utf-8"))

    for key, value in (("backup_retention_days", 36), ("log_retention_days", 31)):
        candidate = {**base, key: value}
        try:
            validate_operator(candidate)
        except error:
            pass
        else:
            raise AssertionError(f"validator accepted invalid {key}={value}")


def test_aws_runtime_image_is_bound_to_profile_ecr_repository():
    namespace: dict[str, object] = {}
    exec((PROFILE / "validate.py").read_text(encoding="utf-8"), namespace)
    validate_operator = cast(
        Callable[[dict[str, Any]], bool], namespace["_validate_operator"]
    )
    error = cast(type[ValueError], namespace["ProfileError"])
    candidate = json.loads(
        (PROFILE / "operator.example.json").read_text(encoding="utf-8")
    )
    candidate.update(
        {
            "runtime_activation_enabled": True,
            "app_secret_version_id": "1234567890abcdef",
            "image_ref": "evil.invalid/repository@sha256:" + "1" * 64,
        }
    )

    try:
        validate_operator(candidate)
    except error:
        pass
    else:
        raise AssertionError(
            "validator accepted an image outside the profile ECR repository"
        )


def test_aws_runtime_waits_for_iam_and_backup_restore_role_is_provisioned():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    outputs = (PROFILE / "terraform" / "outputs.tf").read_text(encoding="utf-8")
    instance = main.split('resource "aws_instance" "app"', 1)[1].split("\n}\n", 1)[0]

    assert "aws_iam_role_policy.runtime" in instance
    assert "aws_iam_role_policy_attachment.runtime_ssm" in instance
    assert 'resource "aws_iam_role" "backup_restore"' in main
    assert "AWSBackupServiceRolePolicyForRestores" in main
    assert 'output "backup_restore_role_arn"' in outputs


def test_aws_profile_redacts_all_supported_header_credentials():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")

    logging_block = main.split(
        'resource "aws_wafv2_web_acl_logging_configuration" "this"', 1
    )[1].split("\n}\n", 1)[0]
    for header in ("authorization", "cookie", "x-api-key"):
        assert f'name = "{header}"' in logging_block


def test_aws_runtime_secret_requires_revision_primary_value_service():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")

    assert '"VALUE_REVISION_API_ENABLED"' in main
    assert '"VALUE_REVISION_DUAL_WRITE_ENABLED"' in main
    assert '"VALUE_REVISION_PRIMARY_READ_PATH"' in main
    assert '"VALUE_REVISION_API_ENABLED": "true"' in main
    assert '"VALUE_REVISION_DUAL_WRITE_ENABLED": "true"' in main
    assert '"VALUE_REVISION_PRIMARY_READ_PATH": "revision"' in main


def test_aws_runtime_activation_refuses_wrong_image_and_unpopulated_secret_version():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    instance = main.split('resource "aws_instance" "app"', 1)[1].split("\n}\n", 1)[0]
    # Terraform check blocks only warn; these must be resource preconditions.
    assert "lifecycle {" in instance
    assert "precondition {" in instance
    assert "aws_ecr_repository.api.repository_url" in instance
    assert "var.image_ref" in instance
    assert (
        'var.image_ref != "${aws_ecr_repository.api.repository_url}@sha256:'
        + "0" * 64
        + '"'
        in instance
    )
    assert "var.app_secret_version_id" in instance
    assert "00000000-0000-0000-0000-000000000000" in instance


def test_aws_readme_runtime_secret_keys_match_bootstrap_contract():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    readme = (PROFILE / "README.md").read_text(encoding="utf-8")
    allowed = main.split("    allowed = {", 1)[1].split("    }", 1)[0]
    documented = readme.split("Its exact JSON key set is:", 1)[1].split(".\n", 1)[0]
    assert set(re.findall(r'"([A-Z_]+)"', allowed)) == set(
        re.findall(r"`([A-Z_]+)`", documented)
    )


def test_aws_foundation_network_checks_block_resource_creation():
    main = (PROFILE / "terraform" / "main.tf").read_text(encoding="utf-8")
    checks = (PROFILE / "terraform" / "checks.tf").read_text(encoding="utf-8")
    vpc = main.split('resource "aws_vpc" "this"', 1)[1].split("\n}\n", 1)[0]
    assert "precondition {" in vpc
    assert "local.network_cidrs_valid" in vpc
    assert "network_cidrs_valid" in checks


def test_aws_mocked_plan_cases_cannot_make_cloud_calls():
    cases = (PROFILE / "terraform" / "activation.tftest.hcl").read_text(
        encoding="utf-8"
    )
    runs = re.split(r'(?m)^run "[^"]+" \{', cases)[1:]
    assert len(runs) >= 11
    assert 'mock_provider "aws" {' in cases
    assert re.search(r'(?m)^provider "aws"', cases) is None
    assert all(re.search(r"(?m)^  command = plan$", run) for run in runs)
    assert "command = apply" not in cases


def test_aws_operator_validator_rejects_noncanonical_or_nonstring_public_cidrs():
    namespace: dict[str, object] = {}
    exec((PROFILE / "validate.py").read_text(encoding="utf-8"), namespace)
    validate_operator = cast(
        Callable[[dict[str, Any]], bool], namespace["_validate_operator"]
    )
    error = cast(type[ValueError], namespace["ProfileError"])
    base = json.loads((PROFILE / "operator.example.json").read_text(encoding="utf-8"))
    for cidrs in (
        [0],
        [["203.0.113.0/24"]],
        ["203.0.113.0/024"],
        ["203.0.113.1/24"],
        ["2001:db8::/32"],
        ["malformed"],
    ):
        try:
            validate_operator({**base, "allowed_public_cidrs": cidrs})
        except error:
            pass
        else:
            raise AssertionError("validator accepted an invalid public CIDR")


def test_aws_operator_validator_rejects_rds_storage_below_gp3_minimum():
    namespace: dict[str, object] = {}
    exec((PROFILE / "validate.py").read_text(encoding="utf-8"), namespace)
    validate_operator = cast(
        Callable[[dict[str, Any]], bool], namespace["_validate_operator"]
    )
    error = cast(type[ValueError], namespace["ProfileError"])
    base = json.loads((PROFILE / "operator.example.json").read_text(encoding="utf-8"))
    for gib in (1, -1):
        try:
            validate_operator({**base, "db_allocated_storage_gib": gib})
        except error:
            pass
        else:
            raise AssertionError("validator accepted subminimum RDS gp3 storage")
