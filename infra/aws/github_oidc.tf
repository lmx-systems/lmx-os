/*
Lets .github/workflows/deploy.yml authenticate to AWS by presenting a
short-lived, GitHub-signed OIDC token instead of a long-lived AWS access
key pasted into a repo secret - the modern, no-static-credential-to-leak
way to let CI deploy. Scoped tightly: only workflow runs from this exact
repo, on main, can assume this role (the `sub` condition below) - a fork's
CI run, or a run on a feature branch, cannot.
*/

variable "github_repository" {
  type        = string
  default     = "lmx-systems/lmx-os"
  description = "owner/repo - must match wherever this codebase's remote actually is, so the OIDC trust condition below is scoped to the real repo."
}

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  name               = "${var.name_prefix}-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json
}

data "aws_iam_policy_document" "github_actions_deploy" {
  statement {
    sid = "PushImages"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"] # GetAuthorizationToken has no resource-level scoping - the repo-specific actions below do
  }
  statement {
    sid = "PushToTheseReposOnly"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [
      aws_ecr_repository.app.arn,
      aws_ecr_repository.dashboard.arn,
      aws_ecr_repository.client_portal.arn,
    ]
  }
  statement {
    sid = "DeployToECS"
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:RegisterTaskDefinition",
    ]
    resources = ["*"] # ECS task-definition ARNs get a new revision number every register - scoping by name prefix via a condition is possible but adds real complexity for a single-repo, single-cluster deploy role
  }
  statement {
    sid       = "PassRolesToNewTaskDefinitions"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.app_task.arn, aws_iam_role.static_site_task.arn]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "${var.name_prefix}-github-actions-deploy"
  role   = aws_iam_role.github_actions_deploy.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}

output "github_actions_deploy_role_arn" {
  value       = aws_iam_role.github_actions_deploy.arn
  description = "Paste into .github/workflows/deploy.yml's role-to-assume input, and into the repo's AWS_DEPLOY_ROLE_ARN variable (Settings > Secrets and variables > Actions)."
}
