variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "name_prefix" {
  type        = string
  default     = "lmx-prod"
  description = "Every resource name and tag is prefixed with this - the single knob to change if a second environment (e.g. staging) is ever stood up from a copy of this config."
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

# Two AZs, not three - RDS/ElastiCache subnet groups need at least two,
# and a third buys marginal extra resilience at real extra complexity for
# a single-hub pilot. Revisit once running more than one hub concurrently
# actually depends on it.
variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "db_instance_class" {
  type        = string
  default     = "db.t4g.micro"
  description = "Smallest Graviton burstable class - right-sized for pre-Hub-1 real-traffic volume, not a placeholder. Bump this before it bumps into you (CloudWatch CPU/connection alarms - see logs.tf)."
}

variable "db_multi_az" {
  type        = bool
  default     = false
  description = "Off by default - real cost for a benefit (automatic failover) that matters once real revenue depends on uptime, not before. This is the one flag to flip when that's true; nothing else about this config needs to change."
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "app_image_tag" {
  type        = string
  default     = "latest"
  description = "Set by CI (.github/workflows/deploy.yml) to the built commit SHA on every deploy - 'latest' is only a safe default for a first manual apply before CI has ever pushed a real tag."
}

variable "app_desired_count" {
  type    = number
  default = 2
}

variable "app_cpu" {
  type    = number
  default = 512 # 0.5 vCPU
}

variable "app_memory" {
  type    = number
  default = 1024 # 1 GB
}

variable "dashboard_desired_count" {
  type    = number
  default = 1
}

variable "client_portal_desired_count" {
  type    = number
  default = 1
}

variable "static_site_cpu" {
  type    = number
  default = 256 # nginx serving a static bundle - far lighter than the API
}

variable "static_site_memory" {
  type    = number
  default = 512
}

variable "certificate_arn" {
  type        = string
  default     = ""
  description = <<-EOT
    ACM certificate covering api./ops./portal. Empty until one exists, which is
    the state of a first apply: a certificate cannot be requested for a domain
    nobody owns, so this cannot be provisioned here.

    Empty, the ALB serves HTTP on :80 and routes by host header - enough to
    prove the stack came up against the ALB's own DNS name. Set, an HTTPS
    listener appears, every host rule moves to it, and :80 becomes a 301.

    Must be issued in this stack's region (var.aws_region). An ALB cannot use a
    certificate from another region, and the failure is an apply-time error
    rather than anything subtler.
  EOT
}

# Route Optimization from ECS (docs/E1_ROUTE_OPTIMIZATION_ACCESS.md). Google
# workload identity federation trusts this task's AWS role, so no Google key
# file exists. Empty until the Google side is set up; then the app falls back to
# the built-in planner and says so, rather than failing dispatch.
variable "google_wif_audience" {
  description = "Google workload identity pool provider audience, //iam.googleapis.com/projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER"
  type        = string
  default     = ""
}

variable "google_wif_service_account" {
  description = "Service account the pool impersonates, if any (route-optimization@PROJECT.iam.gserviceaccount.com)"
  type        = string
  default     = ""
}
