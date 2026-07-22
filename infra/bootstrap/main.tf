/*
Bootstraps the S3 bucket + DynamoDB lock table that infra/aws/backend.tf
depends on for its own remote state. This is the one piece of this whole
setup that has to keep its state locally (or hand-applied once) - there's
no remote backend to point *this* config at without creating a circular
dependency on infrastructure it's the one creating.

Run this exactly once, before infra/aws/ is ever applied:
  cd infra/bootstrap && terraform init && terraform apply

After that, infra/aws/backend.tf's bucket/table names must match the
outputs here (they do, by using the same naming convention below) and
this directory is never touched again.
*/

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "terraform_state" {
  bucket = "lmx-terraform-state"

  # Terraform state contains no application secrets (those live in AWS
  # Secrets Manager, see infra/aws/secrets.tf) but does contain resource
  # IDs/ARNs - still not something to lose or leave world-readable.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_dynamodb_table" "terraform_lock" {
  name         = "lmx-terraform-lock"
  billing_mode = "PAY_PER_REQUEST" # a handful of applies a week - provisioned capacity buys nothing here
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

output "state_bucket" {
  value = aws_s3_bucket.terraform_state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.terraform_lock.name
}
