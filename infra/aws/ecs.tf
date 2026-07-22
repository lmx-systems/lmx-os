/*
One Fargate cluster, three services - a direct lift of docker-compose.yml's
app/dashboard/client-portal containers onto real, autoscaled, multi-task
infrastructure. SECRETS_MANAGER_SECRET_ID + AWS_REGION are the only two
env vars the app container needs for app/secrets_provider.py's
AWSSecretsManagerProvider to take over from there - every other setting
it loads (DATABASE_URL, the three JWT secrets, third-party creds) comes
from secrets.tf's secret, not from anything set here directly.
*/

resource "aws_ecs_cluster" "main" {
  name = "${var.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# --- app --------------------------------------------------------------

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name_prefix}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.app_task.arn

  container_definitions = jsonencode([{
    name         = "app"
    image        = "${aws_ecr_repository.app.repository_url}:${var.app_image_tag}"
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = [
      { name = "ENVIRONMENT", value = var.environment },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "SECRETS_MANAGER_SECRET_ID", value = aws_secretsmanager_secret.app.id },
      { name = "PHOTO_UPLOAD_BUCKET", value = aws_s3_bucket.photo_uploads.bucket },
      { name = "PHOTO_UPLOAD_REGION", value = var.aws_region },
      { name = "DASHBOARD_CORS_ORIGINS", value = "https://ops.lmxit.com,https://portal.lmxit.com" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = "${var.name_prefix}-app"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.app_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  # DB migrations (alembic upgrade head) are not run automatically by this
  # service starting - see infra/README.md's deploy runbook. Running them
  # as part of container startup would mean every one of app_desired_count
  # replicas racing to run the same migration simultaneously on a scale-out
  # event, not just on a real deploy.
  depends_on = [aws_lb_listener_rule.app]
}

resource "aws_appautoscaling_target" "app" {
  max_capacity       = 6
  min_capacity       = var.app_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "app_cpu" {
  name               = "${var.name_prefix}-app-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension
  service_namespace  = aws_appautoscaling_target.app.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 65
    scale_in_cooldown  = 120
    scale_out_cooldown = 60 # scale out faster than in - a slow dispatch cycle during a real order surge is the failure mode that actually matters
  }
}

# --- dashboard ----------------------------------------------------------

resource "aws_ecs_task_definition" "dashboard" {
  family                   = "${var.name_prefix}-dashboard"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.static_site_cpu
  memory                   = var.static_site_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.static_site_task.arn

  container_definitions = jsonencode([{
    name         = "dashboard"
    image        = "${aws_ecr_repository.dashboard.repository_url}:${var.app_image_tag}"
    portMappings = [{ containerPort = 80, protocol = "tcp" }]

    # Read by docker/generate-env-config.sh at *container* startup
    # (docs/ROADMAP.md D2) - not baked into the image, so pointing this at
    # a different API is a service update, not a rebuild.
    environment = [
      { name = "DASHBOARD_API_BASE_URL", value = "https://api.lmxit.com" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.dashboard.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "dashboard"
      }
    }
  }])
}

resource "aws_ecs_service" "dashboard" {
  name            = "${var.name_prefix}-dashboard"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.dashboard.arn
  desired_count   = var.dashboard_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.static_site.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.dashboard.arn
    container_name   = "dashboard"
    container_port   = 80
  }

  depends_on = [aws_lb_listener_rule.dashboard]
}

# --- client portal --------------------------------------------------------

resource "aws_ecs_task_definition" "client_portal" {
  family                   = "${var.name_prefix}-client-portal"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.static_site_cpu
  memory                   = var.static_site_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.static_site_task.arn

  container_definitions = jsonencode([{
    name         = "client-portal"
    image        = "${aws_ecr_repository.client_portal.repository_url}:${var.app_image_tag}"
    portMappings = [{ containerPort = 80, protocol = "tcp" }]

    environment = [
      { name = "CLIENT_PORTAL_API_BASE_URL", value = "https://api.lmxit.com" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.client_portal.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "client-portal"
      }
    }
  }])
}

resource "aws_ecs_service" "client_portal" {
  name            = "${var.name_prefix}-client-portal"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.client_portal.arn
  desired_count   = var.client_portal_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.static_site.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.client_portal.arn
    container_name   = "client-portal"
    container_port   = 80
  }

  depends_on = [aws_lb_listener_rule.client_portal]
}
