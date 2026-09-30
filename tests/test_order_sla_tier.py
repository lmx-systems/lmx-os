"""`Order.sla_tier` is what its annotation says, whoever set it.

Until a reload the attribute held whatever was assigned, and ingestion assigns
the classifier's string. Three kinds of code met that: one called `.value` and
crashed (#124), one worked around it with `hasattr`, and one formatted a loaded
order's tier into a credit reason that read "SLATier.T2 delivered ... late".
"""
import pytest

from app.models.order import Order, SLATier


def test_a_string_given_at_construction_becomes_the_enum():
    assert Order(sla_tier="HOT_SHOT").sla_tier is SLATier.HOT_SHOT


def test_a_string_assigned_later_becomes_the_enum():
    order = Order()
    order.sla_tier = "T2"
    assert order.sla_tier is SLATier.T2
    assert order.sla_tier.value == "T2"


def test_the_enum_and_none_pass_through():
    assert Order(sla_tier=SLATier.T1).sla_tier is SLATier.T1
    assert Order(sla_tier=None).sla_tier is None


def test_an_unknown_tier_fails_at_the_assignment_not_at_the_next_read():
    with pytest.raises(ValueError):
        Order(sla_tier="T4")
