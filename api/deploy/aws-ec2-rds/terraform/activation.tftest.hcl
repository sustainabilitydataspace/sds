# Offline mocked-provider plans only. No AWS credentials, real plan or apply.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-west-1a", "eu-west-1b"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111111111111" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
  mock_data "aws_prefix_list" {
    defaults = { id = "pl-12345678" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "111111111111.dkr.ecr.eu-west-1.amazonaws.com/sds-test/sds-api" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-11111111111111111" }
  }
}

variables {
  aws_region                 = "eu-west-1"
  name_prefix                = "sds-test"
  allowed_public_cidrs       = ["203.0.113.0/24"]
  certificate_arn            = "arn:aws:acm:eu-west-1:111111111111:certificate/11111111-1111-1111-1111-111111111111"
  ami_id                     = "ami-11111111111111111"
  image_ref                  = "111111111111.dkr.ecr.eu-west-1.amazonaws.com/sds-test/sds-api@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  app_secret_version_id      = "11111111-1111-1111-1111-111111111111"
  runtime_activation_enabled = true
}

run "valid_activation_shape" {
  command = plan
  assert {
    condition     = length(aws_instance.app) == 1
    error_message = "Expected one activated instance"
  }
}

run "reject_zero_digest" {
  command = plan
  variables {
    image_ref = "111111111111.dkr.ecr.eu-west-1.amazonaws.com/sds-test/sds-api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  }
  expect_failures = [aws_instance.app]
}

run "reject_foreign_image" {
  command = plan
  variables {
    image_ref = "evil.invalid/not-owned@sha256:1111111111111111111111111111111111111111111111111111111111111111"
  }
  expect_failures = [aws_instance.app, check.closed_operator_contract]
}

run "reject_secret_sentinel" {
  command = plan
  variables {
    app_secret_version_id = "00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [aws_instance.app, check.closed_operator_contract]
}

run "reject_subnet_outside_vpc" {
  command = plan
  variables {
    db_subnet_cidrs = ["10.42.20.0/24", "10.43.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_overlapping_subnets" {
  command = plan
  variables {
    db_subnet_cidrs = ["10.42.0.0/25", "10.42.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_noncanonical_subnet" {
  command = plan
  variables {
    db_subnet_cidrs = ["10.42.20.128/24", "10.42.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_ipv6_subnet" {
  command = plan
  variables {
    db_subnet_cidrs = ["2001:db8::/64", "10.42.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_subnet_smaller_than_aws_supports" {
  command = plan
  variables {
    public_subnet_cidrs = ["10.42.0.0/29", "10.42.1.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_vpc_larger_than_aws_supports" {
  command = plan
  variables {
    vpc_cidr = "10.42.0.0/15"
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_loopback_vpc" {
  command = plan
  variables {
    vpc_cidr            = "127.42.0.0/16"
    public_subnet_cidrs = ["127.42.0.0/24", "127.42.1.0/24"]
    app_subnet_cidrs    = ["127.42.10.0/24", "127.42.11.0/24"]
    db_subnet_cidrs     = ["127.42.20.0/24", "127.42.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_link_local_vpc" {
  command = plan
  variables {
    vpc_cidr            = "169.254.0.0/16"
    public_subnet_cidrs = ["169.254.0.0/24", "169.254.1.0/24"]
    app_subnet_cidrs    = ["169.254.10.0/24", "169.254.11.0/24"]
    db_subnet_cidrs     = ["169.254.20.0/24", "169.254.21.0/24"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_tiny_public_alb_subnet" {
  command = plan
  variables {
    public_subnet_cidrs = ["10.42.0.0/28", "10.42.1.0/28"]
  }
  expect_failures = [aws_vpc.this, check.network_shape]
}

run "reject_subminimum_db_storage" {
  command = plan
  variables {
    db_allocated_storage_gib = 1
  }
  expect_failures = [aws_db_instance.this, check.durability_baseline]
}

run "reject_noncanonical_public_ingress" {
  command = plan
  variables {
    allowed_public_cidrs = ["203.0.113.0/024"]
  }
  expect_failures = [aws_vpc.this, check.closed_operator_contract]
}

run "reject_duplicate_public_ingress" {
  command = plan
  variables {
    allowed_public_cidrs = ["203.0.113.0/24", "203.0.113.0/24"]
  }
  expect_failures = [aws_vpc.this, check.closed_operator_contract]
}

run "reject_disabled_db_backups" {
  command = plan
  variables {
    backup_retention_days = 0
  }
  expect_failures = [var.backup_retention_days]
}

run "reject_short_log_retention" {
  command = plan
  variables {
    log_retention_days = 14
  }
  expect_failures = [var.log_retention_days]
}

run "accept_disjoint_subnets_in_smaller_vpc" {
  command = plan
  variables {
    vpc_cidr            = "10.42.0.0/24"
    public_subnet_cidrs = ["10.42.0.0/27", "10.42.0.32/27"]
    app_subnet_cidrs    = ["10.42.0.64/27", "10.42.0.96/27"]
    db_subnet_cidrs     = ["10.42.0.128/27", "10.42.0.160/27"]
  }
  assert {
    condition     = local.network_cidrs_valid
    error_message = "Disjoint IPv4 networks should be accepted"
  }
}
