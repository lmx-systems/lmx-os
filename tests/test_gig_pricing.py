"""
app/payroll/gig_pricing.py - real per-delivery pay estimate for
gig-classified drivers (docs/ROADMAP.md A11). GIG_BASE_PAY_CENTS/
GIG_PER_MILE_CENTS/GIG_SLA_TIER_BONUS_CENTS are explicitly-labeled
placeholders (see that module's docstring) - these tests confirm the
formula's mechanics, not any particular real-world dollar amount.
"""
import pytest

from app.payroll.gig_pricing import (
    GIG_BASE_PAY_CENTS,
    GIG_SLA_TIER_BONUS_CENTS,
    estimate_delivery_pay_cents,
    haversine_miles,
)


def test_haversine_distance_between_identical_points_is_zero():
    assert haversine_miles(34.05, -118.25, 34.05, -118.25) == pytest.approx(0.0)


def test_haversine_distance_matches_a_known_reference_value():
    # Los Angeles City Hall to Santa Monica Pier - a real, checkable
    # distance (~14.5 straight-line miles), not an arbitrary pair.
    la_city_hall = (34.0537, -118.2428)
    santa_monica_pier = (34.0092, -118.4973)
    distance = haversine_miles(*la_city_hall, *santa_monica_pier)
    assert distance == pytest.approx(14.5, abs=0.5)


def test_estimate_delivery_pay_is_just_the_base_at_zero_distance():
    pay_cents = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.05, dropoff_lng=-118.25, sla_tier="T2"
    )
    assert pay_cents == GIG_BASE_PAY_CENTS + GIG_SLA_TIER_BONUS_CENTS["T2"]


def test_estimate_delivery_pay_increases_with_distance():
    near = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.051, dropoff_lng=-118.25, sla_tier="T2"
    )
    far = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.5, dropoff_lng=-118.25, sla_tier="T2"
    )
    assert far > near


def test_estimate_delivery_pay_applies_the_hot_shot_bonus():
    standard = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.06, dropoff_lng=-118.26, sla_tier="T2"
    )
    hot_shot = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.06, dropoff_lng=-118.26, sla_tier="HOT_SHOT"
    )
    assert hot_shot == standard + GIG_SLA_TIER_BONUS_CENTS["HOT_SHOT"]


def test_estimate_delivery_pay_defaults_to_standard_bonus_for_an_unknown_tier():
    unknown_tier = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.06, dropoff_lng=-118.26, sla_tier=None
    )
    t2 = estimate_delivery_pay_cents(
        pickup_lat=34.05, pickup_lng=-118.25, dropoff_lat=34.06, dropoff_lng=-118.26, sla_tier="T2"
    )
    assert unknown_tier == t2
