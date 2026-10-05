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


def test_question1a_a_dispatchers_release_beats_waiting_for_a_partner():
    """Alone, the queue would hold this for a partner. A dispatcher released
    it, for a reason the queue can't see, so it goes."""
    order = make_held_order("o1", 34.05, -118.25)
    decision = evaluate_held_order(
        order, [], available_driver_count=2, now=NOW, released_by_dispatcher=True
    )
    assert decision.action == "release"
    assert decision.reason == "dispatcher_released"
    assert decision.cluster_mate_ids == []


def test_question1a_an_order_the_deadline_releases_anyway_keeps_the_queues_reason():
    order = make_held_order("o1", 34.05, -118.25, deadline_minutes_from_now=-1)
    decision = evaluate_held_order(
        order, [], available_driver_count=2, now=NOW, released_by_dispatcher=True
    )
    assert decision.reason == "sla_hold_deadline_reached"


def test_run_hold_cycle_releases_only_what_a_dispatcher_released():
    released = make_held_order("a", 34.05, -118.25)
    elsewhere = make_held_order("b", 40.0, -120.0)
    decisions = {
        d.order_id: (d.action, d.reason)
        for d in run_hold_cycle(
            [released, elsewhere], available_driver_count=2, now=NOW, released_by_dispatcher={"a"}
        )
    }
    assert decisions == {
        "a": ("release", "dispatcher_released"),
        "b": ("keep_holding", "waiting_for_cluster_mate"),
    }


def test_question3_no_available_drivers_keeps_holding_even_without_cluster_mate():
    order = make_held_order("o1", 34.05, -118.25)
    decision = evaluate_held_order(order, [], available_driver_count=0, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "no_available_drivers"


def test_question2_a_cluster_is_released_together():
    """Section 6: "Is there another held order within a configurable radius
    that could be batched? If yes -> batch and dispatch together." This held
    both until their deadline before October 2026."""
    a = make_held_order("a", 34.05, -118.25)
    b = make_held_order("b", 34.051, -118.25)
    decisions = {
        d.order_id: d for d in run_hold_cycle([a, b], available_driver_count=3, now=NOW)
    }
    for order_id, mate in (("a", "b"), ("b", "a")):
        assert (decisions[order_id].action, decisions[order_id].reason) == (
            "release",
            "cluster_mate_found",
        )
        assert decisions[order_id].cluster_mate_ids == [mate]


def test_a_lone_order_waits_for_a_partner():
    """The hold the queue exists for. This used to dispatch it alone the moment
    a driver was free, so a lone order never waited for a partner at all."""
    order = make_held_order("o1", 34.05, -118.25)
    far_order = make_held_order("o2", 40.0, -120.0)
    decision = evaluate_held_order(order, [far_order], available_driver_count=3, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "waiting_for_cluster_mate"


def test_a_hot_shot_neighbour_is_not_a_partner():
    """HOT_SHOT is never commingled, so releasing an order to batch with one
    would only send it out early and alone."""
    order = make_held_order("o1", 34.05, -118.25)
    hot_shot = make_held_order("hs", 34.051, -118.25, sla_tier="HOT_SHOT")
    decision = evaluate_held_order(order, [hot_shot], available_driver_count=3, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "waiting_for_cluster_mate"


def test_question4_conflict_with_imminent_higher_priority_order_keeps_holding():
    # One driver, and a T1 order elsewhere is about to hit its own hold
    # deadline - sending this batch now would strand it.
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    mate = make_held_order("o1b", 34.051, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    urgent = make_held_order(
        "urgent", 40.0, -120.0, sla_tier="T1", deadline_minutes_from_now=5
    )  # far away - not a cluster-mate
    decision = evaluate_held_order(order, [mate, urgent], available_driver_count=1, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "would_conflict_with_higher_priority_order"


def test_question4_does_not_fire_when_drivers_have_spare_capacity():
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    mate = make_held_order("o1b", 34.051, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    urgent = make_held_order("urgent", 40.0, -120.0, sla_tier="T1", deadline_minutes_from_now=5)
    # Two drivers available - sending this batch doesn't cost the urgent one anything.
    decision = evaluate_held_order(order, [mate, urgent], available_driver_count=2, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "cluster_mate_found"


def test_question4_does_not_fire_for_a_less_urgent_order():
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T1", deadline_minutes_from_now=60)
    mate = make_held_order("o1b", 34.051, -118.25, sla_tier="T1", deadline_minutes_from_now=60)
    less_urgent = make_held_order("o2", 40.0, -120.0, sla_tier="T3", deadline_minutes_from_now=5)
    decision = evaluate_held_order(order, [mate, less_urgent], available_driver_count=1, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "cluster_mate_found"


def test_question4_ignores_a_cluster_mate_even_if_more_urgent():
    # A cluster-mate is released together with this order, not competing
    # with it for a driver, so it should never trigger the conflict check.
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    mate = make_held_order("o2", 34.051, -118.25, sla_tier="T1", deadline_minutes_from_now=5)
    decision = evaluate_held_order(order, [mate], available_driver_count=1, now=NOW)
    assert decision.action == "release"
    assert decision.reason == "cluster_mate_found"


def test_question4_only_guards_a_release():
    """A lone order is held anyway, so its reason is the wait, not the conflict."""
    order = make_held_order("o1", 34.05, -118.25, sla_tier="T2", deadline_minutes_from_now=60)
    urgent = make_held_order("urgent", 40.0, -120.0, sla_tier="T1", deadline_minutes_from_now=5)
    decision = evaluate_held_order(order, [urgent], available_driver_count=1, now=NOW)
    assert decision.action == "keep_holding"
    assert decision.reason == "waiting_for_cluster_mate"


def test_run_hold_cycle_evaluates_every_order():
    orders = [make_held_order("o1", 34.05, -118.25), make_held_order("o2", 40.0, -120.0)]
    decisions = run_hold_cycle(orders, available_driver_count=2, now=NOW)
    assert {d.order_id for d in decisions} == {"o1", "o2"}


def test_question3_a_passing_driver_collects_a_lone_order():
    """Section 6: "Is a driver already heading in this direction? ... add it to
    their route." Needs no idle driver: the driver is on a route."""
    order = make_held_order("o1", 34.05, -118.25)
    decision = evaluate_held_order(order, [], available_driver_count=0, now=NOW, driver_passing=True)
    assert (decision.action, decision.reason) == ("release", "driver_passing")


def test_question3_does_not_fire_for_a_driver_who_is_not_passing():
    order = make_held_order("o1", 34.05, -118.25)
    decision = evaluate_held_order(order, [], available_driver_count=0, now=NOW, driver_passing=False)
    assert (decision.action, decision.reason) == ("keep_holding", "no_available_drivers")


def test_question2_a_batch_with_an_idle_driver_beats_question3():
    """The questions run in the spec's order: a cluster that can be dispatched
    together is the better dispatch, and the passing driver is for the order
    that has no partner."""
    a = make_held_order("a", 34.05, -118.25)
    b = make_held_order("b", 34.051, -118.25)
    decision = evaluate_held_order(a, [b], available_driver_count=3, now=NOW, driver_passing=True)
    assert (decision.action, decision.reason) == ("release", "cluster_mate_found")


def test_question3_collects_a_clustered_order_when_no_idle_driver_can_take_the_batch():
    a = make_held_order("a", 34.05, -118.25)
    b = make_held_order("b", 34.051, -118.25)
    decision = evaluate_held_order(a, [b], available_driver_count=0, now=NOW, driver_passing=True)
    assert (decision.action, decision.reason) == ("release", "driver_passing")
    assert decision.cluster_mate_ids == ["b"]


def test_question3_keeps_the_earlier_questions_reasons():
    hot = make_held_order("h", 34.05, -118.25, sla_tier="HOT_SHOT")
    late = make_held_order("l", 34.05, -118.25, deadline_minutes_from_now=-1)
    assert evaluate_held_order(hot, [], available_driver_count=0, now=NOW, driver_passing=True).reason == "hot_shot_immediate_release"
    assert evaluate_held_order(late, [], available_driver_count=0, now=NOW, driver_passing=True).reason == "sla_hold_deadline_reached"


def test_run_hold_cycle_releases_only_what_a_driver_is_passing():
    a = make_held_order("a", 34.05, -118.25)
    b = make_held_order("b", 40.0, -120.0)
    decisions = {d.order_id: d for d in run_hold_cycle([a, b], available_driver_count=0, now=NOW, driver_passing={"a"})}
    assert decisions["a"].reason == "driver_passing"
    assert decisions["b"].reason == "no_available_drivers"
