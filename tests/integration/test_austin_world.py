"""Does the measurement recover the answer we planted?

A pipeline that runs over real data without erroring proves only that it runs.
This generates a day where the true dwell and the true geofence lead are known
in advance, puts it through the real code, and checks that what comes out is
what went in.

That is the one thing the design partner's export cannot do for us. It has no
coordinates and no street addresses, so it cannot place a fence; and its
timestamps are minute-resolution, which is the defect DRV-1 exists to replace.
Nothing here is evidence about the business - no synthetic dataset closes gap
2 - it is evidence that the chain computes what it claims to.
"""
import statistics

import pytest

from app.identity import refresh_dwell_statistics, resolve_location
from app.reporting.geofence_calibration import measure_geofence_calibration
from demo.austin_persist import persist_world
from demo.austin_world import AUSTIN_ZIPS, generate_world

pytestmark = pytest.mark.integration


class TestTheGeneratorItself:
    """The fixture has to be faithful before anything graded against it means
    anything."""

    def test_the_same_seed_gives_the_same_world(self):
        a = generate_world(seed=7, shops=10, routes=2, stops_per_route=5)
        b = generate_world(seed=7, shops=10, routes=2, stops_per_route=5)
        assert a.true_dwells == b.true_dwells
        assert [s.name for s in a.shops] == [s.name for s in b.shops]

    def test_a_different_seed_gives_a_different_world(self):
        a = generate_world(seed=1, shops=10, routes=2, stops_per_route=5)
        b = generate_world(seed=2, shops=10, routes=2, stops_per_route=5)
        assert a.true_dwells != b.true_dwells

    def test_the_dwell_distribution_matches_the_finding_it_is_modelled_on(self):
        """Median ~2.1 minutes, a third of stops under 60 seconds.

        Those are the design partner's real numbers, and they are the reason
        dwell is stored in seconds. A fixture whose dwells were all minutes
        long would quietly stop exercising the thing that matters.
        """
        dwells = generate_world(seed=3, shops=40, routes=40, stops_per_route=20).true_dwells

        assert 100 <= statistics.median(dwells) <= 160, "median should sit near 126s"
        under_a_minute = sum(1 for d in dwells if d < 60) / len(dwells)
        assert 0.25 <= under_a_minute <= 0.42, f"{under_a_minute:.0%} under 60s, expected ~a third"

    def test_stops_are_spread_across_austin_rather_than_stacked_on_a_point(self):
        world = generate_world(seed=4, shops=40, routes=1, stops_per_route=5)
        postcodes = {shop.postcode for shop in world.shops}
        assert postcodes == {z[0] for z in AUSTIN_ZIPS}
        # And within a postcode, no two shops share coordinates - a geofence
        # radius is metres, so a fixture on centroids would have every stop in
        # one zip inside every other stop's fence.
        coords = {(round(s.lat, 5), round(s.lng, 5)) for s in world.shops}
        assert len(coords) == len(world.shops)

    def test_a_route_can_be_longer_than_the_geofence_window(self):
        """18 is where DRV-1's rolling window stops holding the whole route."""
        world = generate_world(seed=5, shops=40, routes=1, stops_per_route=25)
        assert len(world.routes[0].stops) == 25

    def test_some_stops_have_no_crossing(self):
        """Permission declined, app killed, GPS indoors. The tap fallback is a
        real path and a fixture where the fence always fires never tests it."""
        world = generate_world(seed=6, shops=40, routes=10, stops_per_route=20)
        sensed = [s.sensed for s in world.all_stops]
        assert any(sensed) and not all(sensed)

    def test_a_tap_is_never_earlier_than_the_truth(self):
        """A tap happens when somebody remembers, and nobody remembers early.
        If this ever fails the fixture has stopped modelling the error the
        sensor exists to remove."""
        for stop in generate_world(seed=8, shops=20, routes=4, stops_per_route=10).all_stops:
            assert stop.tapped_arrived_at > stop.true_arrived_at
            assert stop.tapped_completed_at > stop.true_departed_at


class TestTheMeasurementRecoversTheTruth:
    async def test_dwell_from_crossings_overstates_by_exactly_two_leads(self, db_session):
        """The whole argument for DRV-1, graded - and a bias I had backwards.

        I expected the two edges to cancel: early in, late out. They do not.
        Early in and late out both make the measured interval LONGER, so a
        dwell from crossings is `true + 2 * radius / speed` - about 19 seconds
        at a 75m radius. This test exists because writing the fixture is what
        exposed it.

        The bias is systematic and predictable, which is the good news: it can
        be subtracted once the radius is known. It is also proportional to the
        radius, which makes calibrating that radius a correctness question and
        not only an accuracy one.
        """
        world = generate_world(seed=11, shops=12, routes=3, stops_per_route=15, miss_rate=0.0)
        await persist_world(db_session, world)

        # One dock, measured. Any dock will do - pick the busiest so the
        # sample is worth a median.
        counts: dict[int, int] = {}
        for stop in world.all_stops:
            counts[stop.shop_index] = counts.get(stop.shop_index, 0) + 1
        busiest = max(counts, key=counts.get)
        shop = world.shops[busiest]

        location = await resolve_location(db_session, address=shop.address)
        profile = await refresh_dwell_statistics(db_session, location)

        planted = statistics.median(
            s.true_dwell_seconds for s in world.all_stops if s.shop_index == busiest
        )
        assert profile.dwell_sample_count == counts[busiest]
        expected = planted + world.expected_dwell_bias_seconds
        assert profile.dwell_p50_seconds == pytest.approx(expected, abs=3), (
            "a geofence dwell should be the true dwell plus two leads"
        )
        # And the bias is real rather than noise - it is bigger than a second.
        assert profile.dwell_p50_seconds > planted + 10

    async def test_the_calibration_report_recovers_the_radius_we_chose(self, db_session):
        """`GEOFENCE_RADIUS_M` is uncalibrated, and this is the instrument that
        would calibrate it. Point it at a world whose radius we set and check
        the lead it reports is the lead that radius implies."""
        world = generate_world(seed=12, shops=12, routes=4, stops_per_route=15,
                               geofence_radius_m=75.0, miss_rate=0.0)
        await persist_world(db_session, world)

        reading = await measure_geofence_calibration(db_session)

        assert reading.is_readable, "60 stops should clear the 30-stop floor"
        assert reading.coverage == pytest.approx(1.0)
        # Lead = crossing-to-tap = radius/speed plus the driver's own delay,
        # which is uniform(5, 75) so contributes ~40s at the median.
        expected = world.expected_lead_seconds + 40
        assert reading.lead_p50_seconds == pytest.approx(expected, abs=15)

    async def test_a_bigger_radius_shows_up_as_a_longer_lead(self, db_session):
        """The property that makes the report useful for choosing a radius: the
        number it prints has to move when the radius moves, and in the right
        direction."""
        tight = generate_world(seed=13, shops=8, routes=3, stops_per_route=15,
                               geofence_radius_m=25.0, miss_rate=0.0)
        await persist_world(db_session, tight)
        tight_reading = await measure_geofence_calibration(db_session)

        wide = generate_world(seed=13, shops=8, routes=3, stops_per_route=15,
                              geofence_radius_m=400.0, miss_rate=0.0)
        await persist_world(db_session, wide, seed=2)
        both = await measure_geofence_calibration(db_session)

        # The wide world's stops drag the combined median up.
        assert both.lead_p50_seconds > tight_reading.lead_p50_seconds

    async def test_missing_crossings_show_up_as_coverage_not_as_wrong_dwell(self, db_session):
        """A fence that does not fire must reduce coverage, not quietly bias
        the dwell - the tap fallback keeps the stop in the sample."""
        world = generate_world(seed=14, shops=10, routes=4, stops_per_route=15, miss_rate=0.35)
        await persist_world(db_session, world)

        reading = await measure_geofence_calibration(db_session)

        assert 0.5 < reading.coverage < 0.8, f"coverage {reading.coverage:.2f}"
        assert any("permission" in note for note in reading.notes)
        assert reading.completed_stops == len(world.all_stops)
