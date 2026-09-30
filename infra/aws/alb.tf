/*
One ALB, host-based routing to three target groups - api./ops./portal.
subdomains, matching how the app already reasons about CORS origins
(app/main.py's CORSMiddleware, DASHBOARD_CORS_ORIGINS) as distinct
origins per surface. The ACM certificate and the DNS records are still not provisioned here -
a certificate cannot be requested for a domain nobody owns, and lmxit.com's
DNS lives at Cloudflare rather than Route 53 (see infra/README.md step 3).
But the LISTENER that uses the certificate now is: set var.certificate_arn
and HTTPS appears, every host rule moves onto it, and :80 becomes a 301.

That split is deliberate. Hand-editing this file mid-deploy - which is what
the README used to ask for - means writing Terraform against a live stack at
the exact moment the thing is half-built and nothing works yet. One variable
is a better thing to get right under pressure.
*/

resource "aws_lb" "main" {
  name               = "${var.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "app" {
  name        = "${var.name_prefix}-app"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # required for awsvpc-networked Fargate tasks

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_target_group" "dashboard" {
  name        = "${var.name_prefix}-dashboard"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_target_group" "client_portal" {
  name        = "${var.name_prefix}-client-portal"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

locals {
  # One switch, read in three places below. A certificate is the only thing
  # standing between the two states, so it is the only thing that decides them.
  https_enabled = var.certificate_arn != ""

  # Host rules hang off HTTPS once it exists, HTTP until then. Moving them is
  # a destroy-and-recreate of three small rules - a second or two of 404s on
  # a stack that is not serving real traffic yet, and the alternative is
  # duplicate rules on both listeners, which is worse: the :80 copies would
  # keep serving plaintext after the redirect was supposed to end that.
  routed_listener_arn = (
    local.https_enabled ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  )
}

# :80. A 404 while there is no certificate - enough to prove the ALB is up
# against its own DNS name - and a permanent redirect to :443 once there is.
#
# Never a forward once HTTPS exists. The dashboard and portal containers are
# handed `https://api.lmxit.com` and the API's CORS allow-list contains only
# https:// origins, so a working :80 would be a route that loads a page and
# then fails every request it makes - which looks like a broken deployment
# rather than a misconfigured listener.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.https_enabled ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = local.https_enabled ? [] : [1]
    content {
      type = "fixed-response"
      fixed_response {
        status_code  = 404
        content_type = "text/plain"
        message_body = "Not found"
      }
    }
  }
}

# :443, once a certificate exists. The default action is a 404 rather than a
# forward for the same reason the ALB routes by host header at all: a request
# arriving on an unrecognised name is not one of our three surfaces, and
# answering it with somebody's ops console would be worse than answering it
# with nothing.
resource "aws_lb_listener" "https" {
  count = local.https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  # TLS 1.2 floor. 1.3 where the client supports it; no TLS 1.0/1.1, which
  # nothing that needs to reach this stack still speaks.
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type = "fixed-response"
    fixed_response {
      status_code  = 404
      content_type = "text/plain"
      message_body = "Not found"
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  listener_arn = local.routed_listener_arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = ["api.lmxit.com"]
    }
  }
}

resource "aws_lb_listener_rule" "dashboard" {
  listener_arn = local.routed_listener_arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard.arn
  }

  condition {
    host_header {
      values = ["ops.lmxit.com"]
    }
  }
}

resource "aws_lb_listener_rule" "client_portal" {
  listener_arn = local.routed_listener_arn
  priority     = 30

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.client_portal.arn
  }

  condition {
    host_header {
      values = ["portal.lmxit.com"]
    }
  }
}
