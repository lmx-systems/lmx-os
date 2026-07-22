resource "aws_ecr_repository" "app" {
  name                 = "${var.name_prefix}-app"
  image_tag_mutability = "IMMUTABLE" # a tag (the git SHA - see deploy.yml) always means exactly one build, never a moving target

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "dashboard" {
  name                 = "${var.name_prefix}-dashboard"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "client_portal" {
  name                 = "${var.name_prefix}-client-portal"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Keeps each repo from growing unbounded - untagged images (superseded by
# a re-push under the same logical build, or a failed/aborted push) are
# the only thing this ever removes; every real, IMMUTABLE-tagged image a
# deploy has ever pointed at stays forever.
resource "aws_ecr_lifecycle_policy" "expire_untagged" {
  for_each = {
    app           = aws_ecr_repository.app.name
    dashboard     = aws_ecr_repository.dashboard.name
    client_portal = aws_ecr_repository.client_portal.name
  }
  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
