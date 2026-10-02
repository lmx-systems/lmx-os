/*
The three sweeps docs/ALERTING.md section 6 says must run on a timer. Nothing
scheduled any of them, so on a deployed stack:

  - a webhook whose first attempt failed was never retried;
  - nothing guaranteed a dispatch cycle if every task's event loop stalled;
  - the retention periods the privacy policy states as facts were never
    enforced, because the prune never ran.

The shape is the one that document specifies: an EventBridge rule with a
schedule expression, targeting an API destination whose Connection carries the
X-LMX-Internal-Token header. The connection is the important part - it is how
the token reaches the request without appearing in a rule definition. The value
is the same random_password secrets.tf puts in the app's secret, so the two
cannot drift.

Armed only with HTTPS, like the listener in alb.tf: an API destination must be
an https URL, and api.lmxit.com has no certificate until var.certificate_arn is
set. Each sweep is safe to over-call and safe to miss for a run, so targets do
not retry.
*/

locals {
  internal_sweeps = {
    dispatch  = { path = "/internal/dispatch/run-all", schedule = "rate(5 minutes)" }
    webhooks  = { path = "/internal/webhooks/deliver-pending", schedule = "rate(5 minutes)" }
    retention = { path = "/internal/retention/prune", schedule = "rate(1 day)" }
  }
  armed_sweeps = { for name, sweep in local.internal_sweeps : name => sweep if local.https_enabled }
}

resource "aws_cloudwatch_event_connection" "internal" {
  count              = local.https_enabled ? 1 : 0
  name               = "${var.name_prefix}-internal"
  description        = "Carries X-LMX-Internal-Token to /internal/* on the API"
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      key   = "X-LMX-Internal-Token"
      value = random_password.internal_api_token.result
    }
  }
}

resource "aws_cloudwatch_event_api_destination" "sweep" {
  for_each = local.armed_sweeps

  name                             = "${var.name_prefix}-${each.key}"
  description                      = "POST ${each.value.path}"
  invocation_endpoint              = "https://api.lmxit.com${each.value.path}"
  http_method                      = "POST"
  invocation_rate_limit_per_second = 1
  connection_arn                   = aws_cloudwatch_event_connection.internal[0].arn
}

resource "aws_cloudwatch_event_rule" "sweep" {
  for_each = local.armed_sweeps

  name                = "${var.name_prefix}-${each.key}"
  description         = "POST ${each.value.path} on ${each.value.schedule}"
  schedule_expression = each.value.schedule
}

resource "aws_cloudwatch_event_target" "sweep" {
  for_each = local.armed_sweeps

  rule     = aws_cloudwatch_event_rule.sweep[each.key].name
  arn      = aws_cloudwatch_event_api_destination.sweep[each.key].arn
  role_arn = aws_iam_role.sweeps[0].arn

  retry_policy {
    maximum_retry_attempts       = 0
    maximum_event_age_in_seconds = 60
  }
}

resource "aws_iam_role" "sweeps" {
  count = local.https_enabled ? 1 : 0
  name  = "${var.name_prefix}-sweeps"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sweeps" {
  count = local.https_enabled ? 1 : 0
  name  = "invoke-internal-sweeps"
  role  = aws_iam_role.sweeps[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "events:InvokeApiDestination"
      Resource = [for destination in aws_cloudwatch_event_api_destination.sweep : destination.arn]
    }]
  })
}
