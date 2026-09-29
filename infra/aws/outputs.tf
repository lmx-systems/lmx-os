output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Point api./ops./portal.lmxit.com's DNS at this (CNAME/ALIAS) once the domain is ready - see infra/README.md."
}

output "ecr_repository_urls" {
  value = {
    app           = aws_ecr_repository.app.repository_url
    dashboard     = aws_ecr_repository.dashboard.repository_url
    client_portal = aws_ecr_repository.client_portal.repository_url
  }
}

output "db_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "secrets_manager_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "photo_uploads_bucket" {
  value = aws_s3_bucket.photo_uploads.bucket
}

# --- What the first-migration run-task needs ------------------------------
#
# `infra/README.md`'s step 5 said to fill the `--network-configuration` in
# "from `terraform output`", and these two did not exist - so the one
# instruction standing between a fresh stack and a usable database could not
# be followed as written. Added rather than rewording the step, because the
# step was right about where the values should come from.
#
# The subnets are public and tasks carry public IPs (no NAT Gateway - see
# `vpc.tf`), which is why the run-task below needs `assignPublicIp=ENABLED`.
# Without it the task cannot reach ECR to pull its own image and fails with a
# timeout that reads like a networking fault rather than a missing flag.

output "ecs_task_subnet_ids" {
  value       = aws_subnet.public[*].id
  description = "For `aws ecs run-task --network-configuration`. Public by design; pair with assignPublicIp=ENABLED."
}

output "app_security_group_id" {
  value       = aws_security_group.app.id
  description = "The app tasks' security group - the one a one-off migration task must run in to reach RDS."
}
