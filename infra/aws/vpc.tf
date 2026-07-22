/*
Two-tier network: public subnets for the ALB and the ECS Fargate tasks,
isolated (no internet route at all, in or out) subnets for RDS and
ElastiCache - the database and cache are never reachable from the
internet under any security-group misconfiguration, because there's no
route that would get a packet there in the first place. This closes the
same class of gap the security review (docs/ROADMAP.md S6) flagged in
docker-compose.yml, for real this time.

Deliberately no NAT Gateway: ECS tasks run in the public subnets instead,
with public IPs but a security group that only accepts inbound traffic
from the ALB (see security_groups.tf) - outbound internet access (to
pull images from ECR, call Twilio/Google/S3/Secrets Manager) works the
same either way, and a NAT Gateway is a real fixed cost (~$32/mo plus
per-GB) this stage doesn't need to carry. Move the tasks into the
isolated subnets and add a NAT Gateway here if that boundary ever
matters more than the savings do.
*/

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.name_prefix}-public-${var.availability_zones[count.index]}" }
}

# No internet route at all - RDS/ElastiCache live here (rds.tf,
# elasticache.tf). "Private" in the sense of "isolated," not "has a NAT
# route out" - these two services never need one.
resource "aws_subnet" "isolated" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + length(var.availability_zones))
  availability_zone = var.availability_zones[count.index]

  tags = { Name = "${var.name_prefix}-isolated-${var.availability_zones[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name_prefix}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# No route table entries at all for the isolated subnets - the VPC's
# implicit "local" route (traffic within the VPC's own CIDR) is the only
# path in, which is exactly what RDS/ElastiCache need to talk to the
# app's ECS tasks in the public subnets and nothing else.
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name_prefix}-isolated-rt" }
}

resource "aws_route_table_association" "isolated" {
  count          = length(aws_subnet.isolated)
  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}
