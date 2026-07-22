resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name_prefix}-app"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/ecs/${var.name_prefix}-dashboard"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "client_portal" {
  name              = "/ecs/${var.name_prefix}-client-portal"
  retention_in_days = 14
}

# The one alarm that matters most before Hub 1 goes live: if the app
# stops running entirely, nothing else in CloudWatch will catch it faster
# than "desired count != running count." Wired to nothing yet (no SNS
# topic/on-call tool chosen - a real decision, not a code gap) - the alarm
# firing is real, where it notifies is the next thing to decide.
resource "aws_cloudwatch_metric_alarm" "app_unhealthy" {
  alarm_name          = "${var.name_prefix}-app-unhealthy-tasks"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  period              = 60
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Average"
  threshold           = var.app_desired_count
  treat_missing_data  = "breaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app.name
  }
}
