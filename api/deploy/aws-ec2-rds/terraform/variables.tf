variable "aws_region" {
  type        = string
  description = "AWS region selected by the private operator overlay."
}

variable "name_prefix" {
  type        = string
  description = "Lowercase deployment identifier, for example sds-prod."
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.42.0.0/24", "10.42.1.0/24"]
}

variable "app_subnet_cidrs" {
  type    = list(string)
  default = ["10.42.10.0/24", "10.42.11.0/24"]
}

variable "db_subnet_cidrs" {
  type    = list(string)
  default = ["10.42.20.0/24", "10.42.21.0/24"]
}

variable "allowed_public_cidrs" {
  type        = list(string)
  description = "Explicit client CIDRs allowed to reach the public ALB."
}

variable "certificate_arn" {
  type        = string
  description = "ARN of a prevalidated ACM certificate in aws_region."
}

variable "ami_id" {
  type        = string
  description = "Approved, patched AMI with Docker, Python 3, AWS CLI v2 and SSM Agent preinstalled."
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "image_ref" {
  type        = string
  description = "Immutable SDS API ECR reference in repository@sha256:digest form."
}

variable "app_secret_version_id" {
  type        = string
  description = "Exact version ID of the externally populated SDS runtime secret."
}

variable "runtime_activation_enabled" {
  type        = bool
  default     = false
  description = "Fail-closed phase gate. Keep false for foundation creation; set true only after the pinned image and secret version exist."
}

variable "db_name" {
  type    = string
  default = "sds_api"
}

variable "db_master_username" {
  type    = string
  default = "sds_master"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_allocated_storage_gib" {
  type    = number
  default = 50
}

variable "backup_retention_days" {
  type    = number
  default = 35
  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35 && var.backup_retention_days == floor(var.backup_retention_days)
    error_message = "RDS backup retention must be an integer between 7 and 35 days."
  }
}

variable "log_retention_days" {
  type    = number
  default = 90
  validation {
    condition = contains([
      30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096,
      1827, 2192, 2557, 2922, 3288, 3653
    ], var.log_retention_days)
    error_message = "CloudWatch Logs retention must be a supported value of at least 30 days."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
