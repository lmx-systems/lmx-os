/*
Every hop is locked to the one thing that's actually allowed to call it:
internet -> ALB -> app tasks -> RDS/Redis. Nothing skips a link in that
chain - in particular, RDS and ElastiCache only ever accept traffic from
the app tasks' own security group, never "the VPC CIDR" or wider.
*/

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb"
  description = "Public internet -> ALB, ports 80/443 only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-alb" }
}

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app"
  description = "ALB -> app ECS tasks, port 8000 only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-app" }
}

resource "aws_security_group" "static_site" {
  name        = "${var.name_prefix}-static-site"
  description = "ALB -> dashboard/client-portal ECS tasks, port 80 only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-static-site" }
}

resource "aws_security_group" "db" {
  name        = "${var.name_prefix}-db"
  description = "App tasks -> RDS, port 5432 only - never reachable from anywhere else, including the rest of the VPC"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.name_prefix}-db" }
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "App tasks -> ElastiCache, port 6379 only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.name_prefix}-redis" }
}
