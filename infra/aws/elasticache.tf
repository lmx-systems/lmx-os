/*
Managed Redis, replacing docker-compose.yml's redis:7-alpine container.
Fleet state, the hold queue, and rate-limit counters are all designed
around Redis being disposable (app/db.py/app/redis_client.py already
treat Postgres as the durable source of truth), so this deliberately
isn't Multi-AZ/cluster-mode - a brief Redis blip degrades hot-path reads,
it doesn't lose anything Postgres doesn't already have a durable copy of.
*/

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.name_prefix}-redis"
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "${var.name_prefix}-redis"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  port                 = 6379
  parameter_group_name = "default.redis7"

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  snapshot_retention_limit = 3
  snapshot_window          = "07:00-08:00"
  maintenance_window       = "sun:08:30-sun:09:30"

  tags = { Name = "${var.name_prefix}-redis" }
}
