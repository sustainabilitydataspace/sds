terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  backend "s3" {
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 5.100.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      ManagedBy = "terraform"
      Profile   = "sds-aws-ec2-rds-v1"
    })
  }
}
