/*
Managed Redis, replacing docker-compose.yml's redis:7-alpine container.

Redis holds dispatch's working set: the hold queue, the fleet state (who is on
shift, offered or on a route), the per-hub dispatch lock and the rate-limit
counters. It is not disposable, but it is rebuildable: Postgres holds enough to
put the queue and the fleet state back, and app/optimizer/redis_rebuild.py does
so at startup, on every dispatch sweep, and within 30 seconds of a flush. So a
lost node costs the time it is down, not the orders in it.

While it is down the API answers every request with a 500, because the general
rate limiter (app/rate_limit.py) can't count without it. `redis_multi_az` adds a
standby in a second zone that takes over automatically, which is the fix for that
window. Off by default, matching `db_multi_az`: a standby cache in front of a
single-zone database buys little. Flip both when uptime starts to matter.

Always a replication group, even with one node, so the endpoint and REDIS_URL
don't change when the flag does.

Never restore a snapshot into the live cluster: it brings back hours-old fleet
state, which the rebuild deliberately doesn't overwrite. Start empty and let the
rebuild run. The default parameter group's volatile-lru eviction never evicts a
key without a TTL, which is what keeps the queue and the rebuild's sentinel safe;
an allkeys-* policy would not.
*/

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.name_prefix}-redis"
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "LMX OS hold queue, fleet state, dispatch locks"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  port                 = 6379
  parameter_group_name = "default.redis7"

  num_cache_clusters         = var.redis_multi_az ? 2 : 1
  automatic_failover_enabled = var.redis_multi_az
  multi_az_enabled           = var.redis_multi_az

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # Explicit, because changing either later replaces the cluster. TLS would mean
  # rediss:// and its own change to the app's client.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = false

  snapshot_retention_limit = 3
  snapshot_window          = "07:00-08:00"
  maintenance_window       = "sun:08:30-sun:09:30"

  tags = { Name = "${var.name_prefix}-redis" }
}
