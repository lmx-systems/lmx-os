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
