/*
Two distinct roles per ECS convention, not one: the execution role is
what the ECS agent itself uses before the container even starts (pull
from ECR, write to CloudWatch Logs); the task role is what the
*application code inside the container* is allowed to do (read the
Secrets Manager secret, read/write the photo-uploads bucket). Collapsing
these into one role would hand the app broader AWS permissions than the
one thing it actually reaches for at runtime needs.
*/

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role also needs to read the Secrets Manager secret itself
# when the task definition wires it in via `secrets` (ecs.tf) rather than
# `environment` - that fetch happens before the container starts, so it's
# the execution role's job, not the task role's.
data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "${var.name_prefix}-ecs-execution-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

resource "aws_iam_role" "app_task" {
  name               = "${var.name_prefix}-app-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

# Covers both real uses today: app/secrets_provider.py's
# AWSSecretsManagerProvider re-reads this same secret at boot (belt and
# suspenders alongside the execution-role fetch above, for any future
# runtime re-read), and app/storage/photo_upload_client.py's
# S3PhotoUploadClient minting presigned PUT URLs for the photo bucket.
data "aws_iam_policy_document" "app_task" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.photo_uploads.arn}/*"]
  }
}

resource "aws_iam_role_policy" "app_task" {
  name   = "${var.name_prefix}-app-task"
  role   = aws_iam_role.app_task.id
  policy = data.aws_iam_policy_document.app_task.json
}

# dashboard/client-portal are static nginx bundles - no AWS API calls of
# their own, but ECS still requires a task role to be attached.
resource "aws_iam_role" "static_site_task" {
  name               = "${var.name_prefix}-static-site-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}
