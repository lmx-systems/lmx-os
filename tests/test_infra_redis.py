"""The Terraform's Redis can fail over, and the app is pointed at the right end of it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AWS = ROOT / "infra" / "aws"


def _tf(name: str) -> str:
    return (AWS / name).read_text()


def test_redis_is_a_replication_group_with_failover_on_one_flag():
    elasticache = _tf("elasticache.tf")
    assert 'resource "aws_elasticache_replication_group" "main"' in elasticache
    assert "aws_elasticache_cluster" not in "".join(p.read_text() for p in AWS.glob("*.tf"))
    for setting in ("automatic_failover_enabled", "multi_az_enabled"):
        assert re.search(rf"{setting}\s*=\s*var\.redis_multi_az", elasticache)
    assert 'variable "redis_multi_az"' in _tf("variables.tf")


def test_the_app_writes_to_the_primary_endpoint_set_outside_the_secret():
    """The secret's ignore_changes would keep a stale endpoint after a replacement,
    and the reader endpoint would refuse every write."""
    ecs = _tf("ecs.tf")
    assert re.search(r'name\s*=\s*"REDIS_URL"', ecs)
    assert "primary_endpoint_address" in ecs and "reader_endpoint_address" not in ecs
    assert "REDIS_URL" not in re.sub(r"/\*.*?\*/", "", _tf("secrets.tf"), flags=re.S)
