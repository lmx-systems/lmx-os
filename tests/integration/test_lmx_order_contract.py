"""
Coverage for the LMX Order Object v1 and the status state machine
(docs/LMX_LINK_PLAN.md §1.2-§1.4).

The contract's whole justification is §1.1: *if an adapter ever needs a change
inside the SLA engine, hold queue or optimizer, the contract is wrong.* These
tests are the standing check on that claim - each of the three demand paths is
expressed as one object here, and none of them needs a different shape.

The `sla_owner` split (§1.3) carries most of that weight, so it gets the most
attention: an EXTERNAL order must never be classified, and must be refused if it
arrives without the window that is its only deadline.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.order import OrderStatus
from app.orders.state_machine import (
    InvalidOrderTransition,
    allowed_next,
    assert_transition,
    can_transition,
    public_label,
)
from app.schemas.lmx_order import LMXOrder, LineItem, ProofRequirements

NOW = datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc)


def _base(**overrides) -> dict:
    payload = dict(
        source_system="client_portal",
        source_order_ref="ORD-1",
        hub_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        pickup_address="1200 E 6th St, Austin TX",
        drop_address_raw="900 Congress Ave, Austin TX",
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The three demand paths, one object
# ---------------------------------------------------------------------------


def test_portal_path_lmx_owns_the_commitment():
    """A client typing an order into the portal. We promised, so we classify."""
    order = LMXOrder(**_base())
    assert order.sla_owner == "LMX"
    assert order.needs_classification is True


def test_pos_integration_path_uses_a_registered_shop():
    """An Epicor order names a shop we already have. Same object, different
    origin - and no pickup address needed."""
    order = LMXOrder(
        **_base(
            source_system="epicor",
            shop_external_ref="SHOP-001",
            pickup_address=None,
        )
    )
    assert order.needs_classification is True
    assert order.shop_external_ref == "SHOP-001"


def test_aggregator_path_external_commitment_is_never_classified():
    """The case the whole `sla_owner` split exists for. Somebody else promised
    the customer a window, so the SLA engine must not reclassify it - it accepts
    the window as a hard constraint (§1.3)."""
    order = LMXOrder(
        **_base(
            source_system="dispatch",
            client_id=None,
            sla_owner="EXTERNAL",
            delivery_window_start=NOW,
            delivery_window_end=NOW + timedelta(minutes=90),
        )
    )
    assert order.needs_classification is False
    assert order.client_id is None
    assert order.delivery_window_end == NOW + timedelta(minutes=90)


def test_external_without_a_window_is_refused():
    """An EXTERNAL order's given window is the only deadline it will ever have -
    we never compute one for it. Without it there is nothing for the hold queue
    to hold against, so this has to fail at the edge rather than become an
    order that can never be released."""
    with pytest.raises(ValidationError, match="delivery_window_end"):
        LMXOrder(**_base(sla_owner="EXTERNAL"))


# ---------------------------------------------------------------------------
# Origin resolution
# ---------------------------------------------------------------------------


def test_an_order_with_no_origin_at_all_is_refused():
    """Undispatchable. Failing here is far cheaper than discovering it when a
    driver has nowhere to go."""
    with pytest.raises(ValidationError, match="origin required"):
        LMXOrder(**_base(pickup_address=None))


@pytest.mark.parametrize(
    "origin",
    [
        {"shop_external_ref": "SHOP-001"},
        {"pickup_address": "1200 E 6th St, Austin TX"},
        {"pickup_lat": 30.2669, "pickup_lng": -97.7325},
    ],
)
def test_any_of_the_three_origin_forms_is_accepted(origin):
    """Registered shop, typed address, or raw coordinates. Ad-hoc pickup is what
    lets a brand-new client send a first order with nothing pre-registered."""
    payload = _base(pickup_address=None)
    payload.update(origin)
    assert LMXOrder(**payload) is not None


def test_an_order_is_not_dispatchable_until_both_ends_are_geocoded():
    """Intake never blocks on a missing field (§2.2 principle 7), so an order
    can be stored before geocoding resolves it - but it can't be planned."""
    ungeocoded = LMXOrder(**_base())
    assert ungeocoded.is_dispatchable is False

    geocoded = LMXOrder(
        **_base(
            pickup_lat=30.2669, pickup_lng=-97.7325,
            drop_lat=30.2729, drop_lng=-97.7414,
        )
    )
    assert geocoded.is_dispatchable is True


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["pickup", "delivery"])
def test_an_inverted_window_is_refused(prefix):
    """Usually a date rolling over midnight during extraction. Left unchecked it
    reaches the optimizer, where it makes an order look permanently infeasible
    rather than malformed."""
    payload = _base(sla_owner="LMX")
    payload[f"{prefix}_window_start"] = NOW + timedelta(hours=2)
    payload[f"{prefix}_window_end"] = NOW
    with pytest.raises(ValidationError, match=f"{prefix}_window_end"):
        LMXOrder(**payload)


# ---------------------------------------------------------------------------
# Proof requirements
# ---------------------------------------------------------------------------


def test_proof_defaults_match_what_the_driver_app_does_today():
    """An order that says nothing about proof must behave exactly as before this
    contract existed - one photo, no signature."""
    order = LMXOrder(**_base())
    assert order.proof.photo_count_required == 1
    assert order.proof.signature_required is False
    assert order.proof.photo_subjects == []


def test_proof_is_per_order_not_hardcoded():
    """An aggregator can mandate four photos with named subjects while a
    distributor wants one. The driver app reads this off the order."""
    order = LMXOrder(
        **_base(
            proof=ProofRequirements(
                photo_count_required=4,
                photo_subjects=["package", "door", "street", "label"],
                signature_required=True,
            )
        )
    )
    assert order.proof.photo_count_required == 4
    assert len(order.proof.photo_subjects) == 4


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_itemized_weight_is_reported_separately_from_the_stated_total():
    """Both are kept because they disagree in practice - a counter person's
    stated total is a claim about the real shipment, and itemized weights are
    often absent or wrong. The optimizer uses the stated total."""
    order = LMXOrder(
        **_base(
            total_weight_units=10.0,
            line_items=[
                LineItem(description="brake caliper", quantity=1, weight_units=6.0),
                LineItem(description="pad set", quantity=2, weight_units=1.0),
            ],
        )
    )
    assert order.total_weight_units == 10.0
    assert order.total_weight_from_items == 8.0


def test_line_items_are_optional():
    """Never block a counter person on itemizing."""
    assert LMXOrder(**_base()).line_items == []


# ---------------------------------------------------------------------------
# Status state machine (§1.4)
# ---------------------------------------------------------------------------


def test_the_happy_path_walks_end_to_end():
    path = [
        OrderStatus.received,
        OrderStatus.classified,
        OrderStatus.held,
        OrderStatus.queued,
        OrderStatus.assigned,
        OrderStatus.en_route_pickup,
        OrderStatus.picked_up,
        OrderStatus.en_route_drop,
        OrderStatus.delivered,
    ]
    for current, nxt in zip(path, path[1:]):
        assert_transition(current, nxt)


def test_an_external_order_reaches_held_without_being_classified():
    """§1.3: EXTERNAL skips classification entirely."""
    assert can_transition(OrderStatus.received, OrderStatus.held) is True


def test_an_order_cannot_skip_pickup_on_its_way_to_delivered():
    with pytest.raises(InvalidOrderTransition):
        assert_transition(OrderStatus.assigned, OrderStatus.delivered)


def test_a_driver_may_collect_without_a_separate_en_route_event():
    """Arriving and collecting in one action is normal, and the app does not
    always emit a distinct en-route."""
    assert can_transition(OrderStatus.assigned, OrderStatus.picked_up) is True


def test_repeating_the_current_status_is_idempotent():
    """A double-tap on a flaky connection, or an offline action replaying out of
    the outbox, must not be an error - same reasoning as complete_stop."""
    assert can_transition(OrderStatus.picked_up, OrderStatus.picked_up) is True
    assert_transition(OrderStatus.picked_up, OrderStatus.picked_up)


@pytest.mark.parametrize(
    "terminal", [OrderStatus.delivered, OrderStatus.returned, OrderStatus.cancelled]
)
def test_nothing_moves_out_of_a_terminal_status(terminal):
    assert allowed_next(terminal) == ()
    with pytest.raises(InvalidOrderTransition):
        assert_transition(terminal, OrderStatus.assigned)


def test_an_order_can_be_cancelled_from_any_live_status():
    for status in (
        OrderStatus.received,
        OrderStatus.held,
        OrderStatus.assigned,
        OrderStatus.picked_up,
        OrderStatus.en_route_drop,
    ):
        assert can_transition(status, OrderStatus.cancelled) is True


def test_a_failed_delivery_is_not_terminal():
    """R5: a failed order still has to be redelivered, returned or cancelled."""
    assert OrderStatus.delivery_failed not in allowed_next(OrderStatus.delivered)
    for resolution in (OrderStatus.assigned, OrderStatus.returned, OrderStatus.cancelled):
        assert can_transition(OrderStatus.delivery_failed, resolution) is True


def test_existing_statuses_map_onto_the_public_vocabulary():
    """The two §1.4 states this codebase already had under other names. Adding
    duplicates would have made every status query ambiguous forever."""
    assert public_label(OrderStatus.delivery_failed) == "EXCEPTION_RAISED"
    assert public_label(OrderStatus.returned) == "RETURNED_TO_HUB"


def test_internal_substates_are_not_leaked_to_a_consumer():
    """`classified` and `queued` are our business, not the customer's - both
    read as HELD outside."""
    assert public_label(OrderStatus.classified) == "HELD"
    assert public_label(OrderStatus.queued) == "HELD"


def test_every_status_has_a_public_label():
    """A status with no mapping would crash a sink at exactly the wrong moment."""
    for status in OrderStatus:
        assert public_label(status)
