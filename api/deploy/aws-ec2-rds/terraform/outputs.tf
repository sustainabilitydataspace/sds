output "alb_dns_name" {
  description = "Public ALB DNS name. Bind DNS only after the exact release passes smoke."
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  description = "Immutable SDS API ECR repository. Publication is a separate operation."
  value       = aws_ecr_repository.api.repository_url
}

output "app_instance_id" {
  description = "Private EC2 instance administered only through SSM."
  value       = try(aws_instance.app[0].id, null)
}

output "api_target_group_arn" {
  description = "Empty public target group; registration requires a separately qualified private promoter."
  value       = aws_lb_target_group.api.arn
}

output "app_secret_arn" {
  description = "Metadata-only secret ARN; no secret value is managed by this module."
  value       = aws_secretsmanager_secret.app.arn
}

output "rds_endpoint" {
  description = "Private RDS endpoint."
  value       = aws_db_instance.this.address
}

output "rds_master_secret_arn" {
  description = "RDS-managed bootstrap secret; never grant this ARN to the runtime role."
  value       = try(aws_db_instance.this.master_user_secret[0].secret_arn, null)
  sensitive   = true
}

output "waf_web_acl_arn" {
  value = aws_wafv2_web_acl.this.arn
}

output "backup_vault_arn" {
  value = aws_backup_vault.this.arn
}

output "backup_restore_role_arn" {
  description = "Dedicated role for separately approved AWS Backup restore jobs."
  value       = aws_iam_role.backup_restore.arn
}
