terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Bootstrapped once by infra/bootstrap/ (bucket + lock table names must
  # match its outputs exactly). Never apply infra/aws/ against local state -
  # this is meant to be run by more than one person eventually, and local
  # state is exactly the failure mode a remote backend + lock table exist
  # to prevent.
  backend "s3" {
    bucket         = "lmx-terraform-state"
    key            = "prod/lmx.tfstate"
    region         = "us-east-1"
    dynamodb_table = "lmx-terraform-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "lmx-os"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
