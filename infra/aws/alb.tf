/*
One ALB, host-based routing to three target groups - api./ops./portal.
subdomains, matching how the app already reasons about CORS origins
(app/main.py's CORSMiddleware, DASHBOARD_CORS_ORIGINS) as distinct
origins per surface. HTTPS/ACM certificate and the real DNS records
(Route 53 or wherever the domain is actually registered) are deliberately
not provisioned here - the domain/registrar is an account-and-ownership
decision, not an infrastructure-shape one; see infra/README.md for the
one-time step of pointing a real domain at this ALB.
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

# Plain HTTP for a first apply against the ALB's own DNS name (no
# certificate exists yet). infra/README.md covers the one-time follow-up:
# request an ACM cert for the real domain, add an HTTPS listener with it,
# then flip this listener to a redirect. Not automated here since it
# needs a real, owned domain to request a certificate for in the first
# place - an account/ownership step, not a shape-of-the-infrastructure one.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

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
  listener_arn = aws_lb_listener.http.arn
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
  listener_arn = aws_lb_listener.http.arn
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
  listener_arn = aws_lb_listener.http.arn
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
