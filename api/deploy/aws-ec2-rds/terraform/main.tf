data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_region" "current" {}

data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${var.aws_region}.s3"
}

locals {
  azs               = slice(data.aws_availability_zones.available.names, 0, 2)
  endpoint_services = toset(["ecr.api", "ecr.dkr", "secretsmanager", "logs", "ssm", "ssmmessages", "ec2messages", "kms"])
  api_port          = 8090
  common_tags = merge(var.tags, {
    Application = "SustainabilityDataSpace"
    Environment = var.name_prefix
  })
}

resource "aws_kms_key" "data" {
  description             = "${var.name_prefix} SDS data encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = local.common_tags
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name_prefix}-sds-data"
  target_key_id = aws_kms_key.data.key_id
}

data "aws_iam_policy_document" "logs_kms" {
  statement {
    sid       = "EnableAccountIamPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowBoundCloudWatchLogGroups"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey*",
      "kms:ReEncrypt*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/sds/${var.name_prefix}/api",
        "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:aws-waf-logs-${var.name_prefix}-sds",
      ]
    }
  }
}

resource "aws_kms_key" "logs" {
  description             = "${var.name_prefix} SDS log encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.logs_kms.json
  tags                    = local.common_tags
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.name_prefix}-sds-logs"
  target_key_id = aws_kms_key.logs.key_id
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.common_tags, { Name = "${var.name_prefix}-vpc" })

  lifecycle {
    precondition {
      condition     = local.network_cidrs_valid
      error_message = "The VPC and six tier subnets must be canonical, disjoint IPv4 CIDRs fully contained in the VPC."
    }
    precondition {
      condition     = local.allowed_public_cidrs_valid
      error_message = "Public ALB ingress CIDRs must be non-empty, unique, canonical IPv4 networks."
    }
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${var.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  for_each                = { for index, cidr in var.public_subnet_cidrs : index => cidr }
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = local.azs[tonumber(each.key)]
  map_public_ip_on_launch = false
  tags                    = merge(local.common_tags, { Name = "${var.name_prefix}-public-${each.key}" })
}

resource "aws_subnet" "app" {
  for_each                = { for index, cidr in var.app_subnet_cidrs : index => cidr }
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = local.azs[tonumber(each.key)]
  map_public_ip_on_launch = false
  tags                    = merge(local.common_tags, { Name = "${var.name_prefix}-app-${each.key}" })
}

resource "aws_subnet" "db" {
  for_each                = { for index, cidr in var.db_subnet_cidrs : index => cidr }
  vpc_id                  = aws_vpc.this.id
  cidr_block              = each.value
  availability_zone       = local.azs[tonumber(each.key)]
  map_public_ip_on_launch = false
  tags                    = merge(local.common_tags, { Name = "${var.name_prefix}-db-${each.key}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${var.name_prefix}-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "app" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${var.name_prefix}-app" })
}

resource "aws_route_table_association" "app" {
  for_each       = aws_subnet.app
  subnet_id      = each.value.id
  route_table_id = aws_route_table.app.id
}

resource "aws_route_table" "db" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${var.name_prefix}-db" })
}

resource "aws_route_table_association" "db" {
  for_each       = aws_subnet.db
  subnet_id      = each.value.id
  route_table_id = aws_route_table.db.id
}

resource "aws_security_group" "alb" {
  name_prefix            = "${var.name_prefix}-alb-"
  description            = "Public TLS ingress; egress only to SDS API"
  vpc_id                 = aws_vpc.this.id
  revoke_rules_on_delete = true
  tags                   = local.common_tags
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.allowed_public_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = toset(var.allowed_public_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_security_group" "app" {
  name_prefix            = "${var.name_prefix}-app-"
  description            = "Private SDS API host"
  vpc_id                 = aws_vpc.this.id
  revoke_rules_on_delete = true
  tags                   = local.common_tags
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = local.api_port
  to_port                      = local.api_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = local.api_port
  to_port                      = local.api_port
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "db" {
  name_prefix            = "${var.name_prefix}-db-"
  description            = "RDS ingress only from SDS API"
  vpc_id                 = aws_vpc.this.id
  revoke_rules_on_delete = true
  tags                   = local.common_tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "endpoints" {
  name_prefix            = "${var.name_prefix}-endpoints-"
  description            = "PrivateLink endpoints for SDS runtime"
  vpc_id                 = aws_vpc.this.id
  revoke_rules_on_delete = true
  tags                   = local.common_tags
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_app" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_to_endpoints" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_to_s3" {
  security_group_id = aws_security_group.app.id
  prefix_list_id    = data.aws_prefix_list.s3.id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_dns_udp" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "${cidrhost(var.vpc_cidr, 2)}/32"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
}

resource "aws_vpc_security_group_egress_rule" "app_dns_tcp" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "${cidrhost(var.vpc_cidr, 2)}/32"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_ntp" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "169.254.169.123/32"
  from_port         = 123
  to_port           = 123
  ip_protocol       = "udp"
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.endpoint_services
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [for subnet in aws_subnet.app : subnet.id]
  security_group_ids  = [aws_security_group.endpoints.id]
  tags                = merge(local.common_tags, { Name = "${var.name_prefix}-${replace(each.value, ".", "-")}" })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.app.id]
  tags              = local.common_tags
}

resource "aws_ecr_repository" "api" {
  name                 = "${var.name_prefix}/sds-api"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest 30 release images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name_prefix}/sds-api/runtime"
  description             = "Externally populated SDS runtime configuration; Terraform stores no secret value"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 30
  tags                    = local.common_tags
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = [for subnet in aws_subnet.db : subnet.id]
  tags       = local.common_tags
}

resource "aws_db_instance" "this" {
  identifier                      = "${var.name_prefix}-postgres"
  engine                          = "postgres"
  engine_version                  = "15.10"
  instance_class                  = var.db_instance_class
  allocated_storage               = var.db_allocated_storage_gib
  max_allocated_storage           = max(var.db_allocated_storage_gib, 200)
  storage_type                    = "gp3"
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.data.arn
  db_name                         = var.db_name
  username                        = var.db_master_username
  manage_master_user_password     = true
  master_user_secret_kms_key_id   = aws_kms_key.data.key_id
  multi_az                        = true
  publicly_accessible             = false
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  backup_retention_period         = var.backup_retention_days
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name_prefix}-final"
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  auto_minor_version_upgrade      = true
  apply_immediately               = false
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.data.arn
  tags                            = local.common_tags

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.db_allocated_storage_gib >= 20
      error_message = "RDS gp3 allocated storage must be at least 20 GiB."
    }
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/sds/${var.name_prefix}/api"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name_prefix}-sds"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
  tags              = local.common_tags
}

resource "aws_iam_role" "runtime" {
  name = "${var.name_prefix}-sds-runtime"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "runtime_ssm" {
  role       = aws_iam_role.runtime.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "runtime" {
  name = "sds-runtime-minimum"
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadPinnedRuntimeSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.app.arn
      },
      {
        Sid      = "DecryptRuntimeSecret"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.data.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      },
      {
        Sid      = "PullApiImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = aws_ecr_repository.api.arn
      },
      {
        Sid      = "AuthenticateEcr"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid      = "WriteApiLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "runtime" {
  name = "${var.name_prefix}-sds-runtime"
  role = aws_iam_role.runtime.name
}

resource "aws_instance" "app" {
  count                       = var.runtime_activation_enabled ? 1 : 0
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = values(aws_subnet.app)[0].id
  vpc_security_group_ids      = [aws_security_group.app.id]
  associate_public_ip_address = false
  iam_instance_profile        = aws_iam_instance_profile.runtime.name
  monitoring                  = true
  ebs_optimized               = true
  user_data_replace_on_change = true

  lifecycle {
    precondition {
      condition = startswith(var.image_ref, "${aws_ecr_repository.api.repository_url}@sha256:") && can(regex(
        "^.+@sha256:[0-9a-f]{64}$", var.image_ref
      )) && var.image_ref != "${aws_ecr_repository.api.repository_url}@sha256:0000000000000000000000000000000000000000000000000000000000000000"
      error_message = "Activation requires a digest-pinned image in the module-owned ECR repository."
    }
    precondition {
      condition = can(regex("^[0-9A-Za-z-]{16,128}$", var.app_secret_version_id)) && (
        var.app_secret_version_id != "00000000-0000-0000-0000-000000000000"
      )
      error_message = "Activation requires a populated, pinned application-secret version."
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    encrypted   = true
    kms_key_id  = aws_kms_key.data.arn
    volume_type = "gp3"
    volume_size = 30
  }

  user_data = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    umask 077
    install -d -m 0700 /run/sds-api
    cat >/usr/local/sbin/sds-api-start <<'SCRIPT'
    #!/usr/bin/env bash
    set -euo pipefail
    umask 077
    IMAGE='${var.image_ref}'
    SECRET_ARN='${aws_secretsmanager_secret.app.arn}'
    SECRET_VERSION='${var.app_secret_version_id}'
    REGION='${var.aws_region}'
    LOG_GROUP='${aws_cloudwatch_log_group.api.name}'
    REGISTRY="$${IMAGE%%/*}"
    aws ecr get-login-password --region "$${REGION}" | docker login --username AWS --password-stdin "$${REGISTRY}" >/dev/null
    docker pull "$${IMAGE}" >/dev/null
    docker image inspect "$${IMAGE}" >/dev/null
    cleanup_runtime_source() { rm -f -- /run/sds-api/runtime.json; }
    trap cleanup_runtime_source EXIT
    aws secretsmanager get-secret-value --region "$${REGION}" --secret-id "$${SECRET_ARN}" --version-id "$${SECRET_VERSION}" --query SecretString --output text > /run/sds-api/runtime.json
    python3 - /run/sds-api/runtime.json /run/sds-api/runtime.env <<'PY'
    import json, os, sys, tempfile
    source, destination = sys.argv[1:]
    with open(source, encoding="utf-8") as handle:
        values = json.load(handle)
    allowed = {
        "ALLOWED_ORIGINS", "APP_ENV", "DATABASE_URL", "EXPORT_SIGNING_SECRET",
        "JWT_SECRET_KEY", "LOG_LEVEL", "REQUIRE_DATABASE", "TRUSTED_PROXY_IPS",
        "USE_POSTGRES_UNITS", "SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED",
        "VALUE_REVISION_API_ENABLED",
        "VALUE_REVISION_DUAL_WRITE_ENABLED", "VALUE_REVISION_PRIMARY_READ_PATH"
    }
    if not isinstance(values, dict) or set(values) != allowed:
        raise SystemExit("runtime secret has an unsupported key set")
    required_exact = {
        "APP_ENV": "production",
        "REQUIRE_DATABASE": "true",
        "SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED": "true",
        "USE_POSTGRES_UNITS": "true",
        "VALUE_REVISION_API_ENABLED": "true",
        "VALUE_REVISION_DUAL_WRITE_ENABLED": "true",
        "VALUE_REVISION_PRIMARY_READ_PATH": "revision",
    }
    if any(values.get(key) != expected for key, expected in required_exact.items()):
        raise SystemExit("runtime secret does not enable the production DB profile")
    lines = []
    for key in sorted(values):
        value = values[key]
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n\x00"):
            raise SystemExit("runtime secret contains an unsafe value")
        lines.append(f"{key}={value}")
    directory = os.path.dirname(destination) or "."
    fd, temporary = tempfile.mkstemp(prefix=".runtime.env.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = ""
        directory_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    PY
    cleanup_runtime_source
    trap - EXIT
    docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
      --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 64 \
      --env-file /run/sds-api/runtime.env --entrypoint python \
      "$${IMAGE}" -m src.database.init_db --check-head-read-only >/dev/null
    if docker inspect sds-api >/dev/null 2>&1; then docker rm -f sds-api >/dev/null; fi
    docker run --name sds-api --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m \
      --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 \
      --env-file /run/sds-api/runtime.env -p 8090:8090 \
      --log-driver awslogs --log-opt awslogs-region="$${REGION}" \
      --log-opt awslogs-group="$${LOG_GROUP}" --log-opt awslogs-stream=api \
      "$${IMAGE}"
    SCRIPT
    chmod 0700 /usr/local/sbin/sds-api-start
    cat >/etc/systemd/system/sds-api.service <<'UNIT'
    [Unit]
    Description=SustainabilityDataSpace API
    After=docker.service network-online.target
    Requires=docker.service
    StartLimitIntervalSec=300
    StartLimitBurst=3

    [Service]
    Type=simple
    RuntimeDirectory=sds-api
    RuntimeDirectoryMode=0700
    ExecStart=/usr/local/sbin/sds-api-start
    Restart=on-failure
    RestartSec=15
    TimeoutStartSec=900
    TimeoutStopSec=120
    KillMode=control-group

    [Install]
    WantedBy=multi-user.target
    UNIT
    systemctl daemon-reload
    systemctl enable --now sds-api.service
  EOT

  depends_on = [
    aws_vpc_endpoint.interface,
    aws_vpc_endpoint.s3,
    aws_db_instance.this,
    aws_iam_role_policy.runtime,
    aws_iam_role_policy_attachment.runtime_ssm,
    aws_route_table_association.app,
    aws_vpc_security_group_ingress_rule.app_from_alb,
    aws_vpc_security_group_egress_rule.alb_to_app,
    aws_vpc_security_group_ingress_rule.db_from_app,
    aws_vpc_security_group_egress_rule.app_to_db,
    aws_vpc_security_group_ingress_rule.endpoints_from_app,
    aws_vpc_security_group_egress_rule.app_to_endpoints,
    aws_vpc_security_group_egress_rule.app_to_s3,
    aws_vpc_security_group_egress_rule.app_dns_udp,
    aws_vpc_security_group_egress_rule.app_dns_tcp,
    aws_vpc_security_group_egress_rule.app_ntp,
  ]

  tags = merge(local.common_tags, { Name = "${var.name_prefix}-app" })
}

resource "aws_lb" "this" {
  name                       = substr("${var.name_prefix}-sds", 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = [for subnet in aws_subnet.public : subnet.id]
  enable_deletion_protection = true
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  tags                       = local.common_tags
}

resource "aws_lb_target_group" "api" {
  name        = substr("${var.name_prefix}-sds-api", 0, 32)
  port        = local.api_port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.this.id

  health_check {
    enabled             = true
    path                = "/healthz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = local.common_tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_wafv2_web_acl" "this" {
  name  = "${var.name_prefix}-sds"
  scope = "REGIONAL"
  default_action {
    allow {}
  }

  rule {
    name     = "aws-common"
    priority = 10
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "aws-known-bad-inputs"
    priority = 20
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-known-bad-inputs"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "per-ip-rate-limit"
    priority = 30
    action {
      block {}
    }
    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        limit              = 2000
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "per-ip-rate-limit"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-sds"
    sampled_requests_enabled   = false
  }
  tags = local.common_tags
}

resource "aws_wafv2_web_acl_association" "this" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
  redacted_fields {
    single_header {
      name = "x-api-key"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${var.name_prefix}-sds-unhealthy-targets"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "breaching"
  dimensions = {
    LoadBalancer = aws_lb.this.arn_suffix
    TargetGroup  = aws_lb_target_group.api.arn_suffix
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.name_prefix}-sds-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.name_prefix}-sds-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "breaching"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.id }
  tags                = local.common_tags
}

resource "aws_iam_role" "backup" {
  name = "${var.name_prefix}-sds-backup"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "backup.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role" "backup_restore" {
  name = "${var.name_prefix}-sds-backup-restore"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "backup.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "backup_restore" {
  role       = aws_iam_role.backup_restore.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

resource "aws_backup_vault" "this" {
  name        = "${var.name_prefix}-sds"
  kms_key_arn = aws_kms_key.data.arn
  tags        = local.common_tags
}

resource "aws_backup_plan" "this" {
  name = "${var.name_prefix}-sds"
  rule {
    rule_name         = "daily-rds"
    target_vault_name = aws_backup_vault.this.name
    schedule          = "cron(0 3 * * ? *)"
    lifecycle {
      delete_after = var.backup_retention_days
    }
    recovery_point_tags = local.common_tags
  }
  tags = local.common_tags
}

resource "aws_backup_selection" "rds" {
  iam_role_arn = aws_iam_role.backup.arn
  name         = "${var.name_prefix}-rds"
  plan_id      = aws_backup_plan.this.id
  resources    = [aws_db_instance.this.arn]
}
