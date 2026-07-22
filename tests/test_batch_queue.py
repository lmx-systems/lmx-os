from datetime import datetime, timedelta, timezone

from app.batch_queue.clustering import cluster_members, miles_between
from app.batch_queue.queue import HeldOrder, evaluate_held_order, run_hold_cycle

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def make_held_order(order_id, lat, lng, held_minutes_ago=0, deadline_minutes_from_now=30, sla_tier="T2"):
    return HeldOrder(
        order_id=order_id,
        shop_lat=lat,
        shop_lng=lng,
        sla_tier=sla_tier,
        hold_deadline=NOW + timedelta(minutes=deadline_minutes_from_now),
        held_since=NOW - timedelta(minutes=held_minutes_ago),
    )


def test_miles_between_same_point_is_zero():
    assert miles_between(34.05, -118.25, 34.05, -118.25) == 0


def test_cluster_members_respects_radius():
    # ~0.5 miles apart
    candidates = [("a", 34.05, -118.25), ("b", 34.058, -118.25), ("c", 40.0, -120.0)]
    members = cluster_members(34.05, -118.25, candidates, radius_miles=0.8)
    assert "a" in members
    assert "b" in members
    assert "c" not in members


def test_question0_hot_shot_always_releases_immediately_even_with_cluster_mate():
    order = make_held_order("o1", 34.05, -118.25, sla_tier="HOT_SHOT")
    mate = make_held_order("o2", 34.051, -118.25)  # would otherwise cluster-match
    decision = evaluate_held_order(order, [mate], available_driver_count=0, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "hot_shot_immediate_release"
    assert decision.cluster_mate_ids == []


def test_question1_sla_deadline_always_releases():
    order = make_held_order("o1", 34.05, -118.25, deadline_minutes_from_now=-1)
    decision = evaluate_held_order(order, [], available_driver_count=5, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "sla_hold_deadline_reached"


def test_question3_no_available_drivers_keeps_holding_even_without_cluster_mate():
    order = make_held_order("o1", 34.05, -118.25)
    decision = evaluate_held_order(order, [], available_driver_count=0, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "no_available_drivers"


def test_question4_conflict_with_imminent_higher_priority_order_keeps_holding():
    # Only one driver available, and a T1 order elsewhere is about to hit
    # its own hold_deadline - dispatching this T2 order now would strand it.
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    urgent = make_held_order(
        "urgent", 40.0, -120.0, sla_tier="T1", deadline_minutes_from_now=5
    )  # far away - not a cluster-mate
    decision = evaluate_held_order(order, [urgent], available_driver_count=1, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "would_conflict_with_higher_priority_order"


def test_question4_does_not_fire_when_drivers_have_spare_capacity():
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    urgent = make_held_order("urgent", 40.0, -120.0, sla_tier="T1", deadline_minutes_from_now=5)
    # Two drivers available - sending this order doesn't cost the urgent one anything.
    decision = evaluate_held_order(order, [urgent], available_driver_count=2, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "no_cluster_mate_and_drivers_available"


def test_question4_does_not_fire_for_a_less_urgent_order():
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T1", deadline_minutes_from_now=60)
    less_urgent = make_held_order("o2", 40.0, -120.0, sla_tier="T3", deadline_minutes_from_now=5)
    decision = evaluate_held_order(order, [less_urgent], available_driver_count=1, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "no_cluster_mate_and_drivers_available"


def test_question4_ignores_a_cluster_mate_even_if_more_urgent():
    # A cluster-mate is released together with this order, not competing
    # with it for a driver, so it should never trigger the conflict check.
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    mate = make_held_order("o2", 34.051, -118.25, sla_tier="T1", deadline_minutes_from_now=5)
    decision = evaluate_held_order(order, [mate], available_driver_count=1, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "cluster_mate_found"


def test_question2_cluster_mate_keeps_holding():
    order = make_held_order("o1", 34.05, -118.25)
    mate = make_held_order("o2", 34.051, -118.25)
    decision = evaluate_held_order(order, [mate], available_driver_count=3, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "cluster_mate_found"
    assert "o2" in decision.cluster_mate_ids


def test_no_cluster_mate_and_drivers_available_releases():
    order = make_held_order("o1", 34.05, -118.25)
    far_order = make_held_order("o2", 40.0, -120.0)
    decision = evaluate_held_order(order, [far_order], available_driver_count=3, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "no_cluster_mate_and_drivers_available"


def test_run_hold_cycle_evaluates_every_order():
    orders = [make_held_order("o1", 34.05, -118.25), make_held_order("o2", 40.0, -120.0)]
    decisions = run_hold_cycle(orders, available_driver_count=2, now=NOW)
    assert {d.order_id for d in decisions} == {"o1", "o2"}
