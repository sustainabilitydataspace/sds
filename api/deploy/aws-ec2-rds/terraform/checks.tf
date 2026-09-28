locals {
  tier_cidrs       = concat(var.public_subnet_cidrs, var.app_subnet_cidrs, var.db_subnet_cidrs)
  vpc_prefix       = try(tonumber(split("/", var.vpc_cidr)[1]), -1)
  vpc_first_octet  = try(tonumber(split(".", cidrhost(var.vpc_cidr, 0))[0]), -1)
  vpc_second_octet = try(tonumber(split(".", cidrhost(var.vpc_cidr, 0))[1]), -1)
  vpc_start_ipv4 = try(sum([
    for i, octet in split(".", cidrhost(var.vpc_cidr, 0)) : tonumber(octet) * pow(256, 3 - i)
  ]), -1)
  tier_networks = [for cidr in local.tier_cidrs : {
    prefix    = try(tonumber(split("/", cidr)[1]), -1)
    start     = try(sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    canonical = try(cidrsubnet(cidr, 0, 0) == cidr, false)
    ipv4      = try(length(split(".", cidrhost(cidr, 0))) == 4, false)
  }]
  allowed_public_cidrs_valid = (
    length(var.allowed_public_cidrs) > 0 &&
    length(toset(var.allowed_public_cidrs)) == length(var.allowed_public_cidrs) &&
    alltrue([for cidr in var.allowed_public_cidrs :
      try(cidrsubnet(cidr, 0, 0) == cidr, false) &&
      try(length(split(".", cidrhost(cidr, 0))) == 4, false)
    ])
  )
  network_cidrs_valid = (
    length(local.tier_cidrs) == 6 &&
    length(toset(local.tier_cidrs)) == 6 &&
    local.vpc_prefix >= 16 && local.vpc_prefix <= 28 &&
    local.vpc_first_octet > 0 && local.vpc_first_octet < 224 &&
    local.vpc_first_octet != 127 &&
    !(local.vpc_first_octet == 169 && local.vpc_second_octet == 254) &&
    try(cidrsubnet(var.vpc_cidr, 0, 0) == var.vpc_cidr, false) &&
    try(length(split(".", cidrhost(var.vpc_cidr, 0))) == 4, false) &&
    alltrue([for network in local.tier_networks :
      network.canonical && network.ipv4 &&
      network.prefix >= local.vpc_prefix && network.prefix <= 28 &&
      network.start >= local.vpc_start_ipv4 &&
      network.start + pow(2, 32 - network.prefix) <= local.vpc_start_ipv4 + pow(2, 32 - local.vpc_prefix)
    ]) &&
    alltrue([for network in slice(local.tier_networks, 0, 2) : network.prefix <= 27]) &&
    alltrue(flatten([for i, first in local.tier_networks : [for j, second in local.tier_networks :
      i >= j || first.start + pow(2, 32 - first.prefix) <= second.start ||
      second.start + pow(2, 32 - second.prefix) <= first.start
    ]]))
  )
}

check "closed_operator_contract" {
  assert {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.name_prefix))
    error_message = "name_prefix must be a lowercase 3-24 character deployment identifier."
  }
  assert {
    condition     = local.allowed_public_cidrs_valid
    error_message = "allowed_public_cidrs must be non-empty, unique, canonical IPv4 CIDRs."
  }
  assert {
    condition     = can(regex("^arn:[^:]+:acm:${var.aws_region}:[0-9]{12}:certificate/[0-9a-f-]+$", var.certificate_arn))
    error_message = "certificate_arn must identify an ACM certificate in aws_region."
  }
  assert {
    condition     = can(regex("^ami-[0-9a-f]{8,17}$", var.ami_id))
    error_message = "ami_id must be an explicit approved AMI identifier."
  }
  assert {
    condition = !var.runtime_activation_enabled || can(regex(
      "^${data.aws_caller_identity.current.account_id}\\.dkr\\.ecr\\.${var.aws_region}\\.${data.aws_partition.current.dns_suffix}/${var.name_prefix}/sds-api@sha256:[0-9a-f]{64}$",
      var.image_ref
    ))
    error_message = "image_ref must be digest-pinned in this profile's ECR repository."
  }
  assert {
    condition = !var.runtime_activation_enabled || (
      can(regex("^[0-9A-Za-z-]{16,128}$", var.app_secret_version_id)) &&
      var.app_secret_version_id != "00000000-0000-0000-0000-000000000000"
    )
    error_message = "app_secret_version_id must be the exact populated secret version, not the example sentinel."
  }
}

check "network_shape" {
  assert {
    condition     = length(var.public_subnet_cidrs) == 2 && length(var.app_subnet_cidrs) == 2 && length(var.db_subnet_cidrs) == 2
    error_message = "The reference profile requires exactly two AZ-aligned subnets per tier."
  }
  assert {
    condition     = length(toset(concat(var.public_subnet_cidrs, var.app_subnet_cidrs, var.db_subnet_cidrs))) == 6
    error_message = "Subnet CIDRs must be unique."
  }
  assert {
    condition     = local.network_cidrs_valid
    error_message = "Tier subnet CIDRs must be canonical, disjoint IPv4 ranges contained by vpc_cidr."
  }
}

check "durability_baseline" {
  assert {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35
    error_message = "RDS backup_retention_days must be between 7 and 35."
  }
  assert {
    condition = contains([
      30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096,
      1827, 2192, 2557, 2922, 3288, 3653
    ], var.log_retention_days)
    error_message = "log_retention_days must be a supported CloudWatch Logs retention value of at least 30 days."
  }
  assert {
    condition     = var.db_allocated_storage_gib >= 20
    error_message = "RDS allocated storage must be at least 20 GiB."
  }
}
