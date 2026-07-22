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
