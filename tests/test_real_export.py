"""The export loaders and the two analyses that run on them.

**Every fixture here is invented**, with invented towns and invented postcodes.
`CLAUDE.md`'s naming rule covers tests and fixtures as much as prose, and the
real files are gitignored because this repository is public. A test that needed
the design partner's book would also be a test that only ran on one laptop.
"""
import csv
from pathlib import Path

import pytest

from ml.prd.batch_value import (
    MEASURE_BATCH_SIZE,
    MEASURE_SHARE_BATCHED,
    batch_value_by_node_class,
    check_the_stated_finding,
    where_the_spread_actually_is,
)
from ml.prd.trip_cost import compute
from ml.real.export import DetailStop, TimingStop, load_detail, load_timing, usability

TIMING_COLUMNS = [
    "receiver_id", "receiver_name", "route_id", "driver_id", "stop_seq",
    "stops_total", "created", "dispatched", "arrived", "departed", "dwell_sec",
    "hold_sec", "order_to_door_sec", "invoices_at_stop", "zip",
]
DETAIL_COLUMNS = [
    "receiver_id", "receiver_name", "route_id", "driver_id", "stop_seq",
    "arrived", "dwell_sec", "travel_sec", "revenue", "pieces", "weight",
]


def _write(path: Path, columns: list[str], rows: list[dict]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
    return path


@pytest.fixture
def timing_file(tmp_path) -> Path:
    rows = []
    for index in range(40):
        minute = index * 7
        rows.append(
            {
                "receiver_id": f"{100 + index % 8}/0",
                "receiver_name": ["Springfield Auto Repair", "Fairview Body Shop",
                                  "Northgate Parts Supply", "Lakeside Warehouse"][index % 4],
                "route_id": f"Manifest #{9000 + index // 4}",
                "driver_id": f"D{index % 3}",
                "stop_seq": index % 4 + 1,
                "stops_total": 4,
                "created": f"2026-05-04 {8 + minute // 60:02d}:{minute % 60:02d}:00",
                "dispatched": "2026-05-04 11:00:00",
                "arrived": "2026-05-04 11:30:00",
                "departed": "2026-05-04 11:34:00",
                "dwell_sec": 240,
                # Half created after dispatch, like the real book.
                "hold_sec": -600 if index % 2 else 600,
                "order_to_door_sec": 3600,
                "invoices_at_stop": 1,
                "zip": "99001" if index % 3 else "99002",
            }
        )
    return _write(tmp_path / "timing.csv", TIMING_COLUMNS, rows)


@pytest.fixture
def detail_file(tmp_path) -> Path:
    rows = []
    for index in range(30):
        rows.append(
            {
                "receiver_id": f"{200 + index % 6}/0",
                "receiver_name": "Springfield Auto Repair",
                "route_id": "Manifest #7001",
                "driver_id": "D9",
                "stop_seq": index + 1,
                "arrived": f"2026-05-0{index % 5 + 1} 09:{index % 60:02d}:00",
                "dwell_sec": 60 + index * 11,
                "travel_sec": 300 + index * 20,
                # A third unbilled, as in the real file.
                "revenue": "" if index % 3 == 0 else f"${20 + index * 9}.50 ",
                "pieces": index % 4,
                # Present on every row and always zero - the real failure mode.
                "weight": 0,
            }
        )
    return _write(tmp_path / "detail.csv", DETAIL_COLUMNS, rows)


class TestTheLoadersRefuseToCarryNames:
    def test_a_stop_has_no_field_that_could_hold_an_account_name(self):
        """Structural, not a convention. A dataclass without the field cannot
        leak it into a log, a repr or a test failure message."""
        for model in (TimingStop, DetailStop):
            fields = set(model.__dataclass_fields__)
            assert "receiver_name" not in fields
            assert "name" not in fields
            assert "node_class" in fields

    def test_the_name_is_read_and_turned_into_a_class(self, timing_file):
        stops, _ = load_timing(timing_file)
        classes = {s.node_class for s in stops}
        assert "shop" in classes
        assert "body_shop" in classes

    def test_an_unrecognised_name_becomes_unknown_not_a_guess(self, tmp_path):
        path = _write(
            tmp_path / "t.csv", TIMING_COLUMNS,
            [{"receiver_id": "1/0", "receiver_name": "Zzyzx Holdings LLC",
              "created": "2026-05-04 08:00:00", "zip": "99001", "route_id": "R",
              "driver_id": "D", "dwell_sec": 1, "hold_sec": 1,
              "arrived": "2026-05-04 09:00:00"}],
        )
        stops, _ = load_timing(path)
        assert stops[0].node_class == "unknown"


class TestCoverageSeparatesPresentFromInformative:
    def test_a_column_of_zeroes_reads_as_populated_and_uninformative(self, detail_file):
        """The real export's weight column is filled in on every row and is
        always zero. One coverage number would have called it perfect."""
        _, cover = load_detail(detail_file)
        assert cover.share("weight") == 1.0
        assert cover.informative_share("weight") == 0.0

    def test_a_blank_column_reads_as_absent(self, detail_file):
        _, cover = load_detail(detail_file)
        assert cover.share("revenue") < 1.0

    def test_the_summary_calls_out_an_always_zero_column(self, detail_file):
        _, cover = load_detail(detail_file)
        assert "always zero" in str(cover)


class TestUsabilityIsAVerdictNotATable:
    def test_it_reports_the_weight_column_as_unusable(self, timing_file, detail_file):
        timing, _ = load_timing(timing_file)
        detail, _ = load_detail(detail_file)
        verdicts = {v.model: v for v in usability(timing, detail)}
        assert verdicts["M5 modality fit"].supported is False

    def test_a_single_driver_blocks_driver_effects(self, timing_file, detail_file):
        timing, _ = load_timing(timing_file)
        detail, _ = load_detail(detail_file)
        verdicts = {v.model: v for v in usability(timing, detail)}
        assert verdicts["M1 dwell, driver effects"].supported is False

    def test_in_flight_insertion_is_read_as_such_not_as_corruption(self, timing_file):
        """A negative hold is a dispatcher adding an order to a truck that had
        already left. Half the real book looks like this and it is the ceiling
        the roadmap names, not a parsing error."""
        stops, _ = load_timing(timing_file)
        inserted = [s for s in stops if s.was_inserted_in_flight]
        assert len(inserted) == len(stops) // 2


class TestBatchValue:
    def test_the_sweep_does_not_split_orders_that_arrived_together(self, timing_file):
        """The bug fixed buckets have: two orders four minutes apart landing on
        opposite sides of a boundary and reported as unbatchable."""
        stops, _ = load_timing(timing_file)
        results = batch_value_by_node_class(stops, windows=(0, 60))
        for result in results.values():
            assert result.at(MEASURE_BATCH_SIZE, 60) >= result.at(MEASURE_BATCH_SIZE, 0)

    def test_a_longer_hold_never_batches_fewer_orders(self, timing_file):
        stops, _ = load_timing(timing_file)
        results = batch_value_by_node_class(stops, windows=(15, 30, 60, 120))
        for result in results.values():
            curve = [result.at(MEASURE_SHARE_BATCHED, w) for w in (15, 30, 60, 120)]
            assert curve == sorted(curve)

    def test_a_class_with_too_few_docks_is_reported_untestable(self, timing_file):
        """The finding PRD-1 is defined by rests on warehouse and transfer. If
        the export holds one warehouse and no transfers, the honest output is
        'not testable', not a percentage computed from one dock."""
        stops, _ = load_timing(timing_file)
        results = batch_value_by_node_class(stops)
        verdicts = {r.node_class: r for r in check_the_stated_finding(results)}
        assert "transfer" in verdicts
        assert "not testable" in verdicts["transfer"].verdict

    def test_thin_classes_are_excluded_from_the_spread(self, timing_file):
        stops, _ = load_timing(timing_file)
        results = batch_value_by_node_class(stops)
        spread = {row[0] for row in where_the_spread_actually_is(results)}
        for node_class in spread:
            assert results[node_class].receivers >= 5


class TestTripCost:
    def test_a_rate_is_required(self, detail_file):
        stops, _ = load_detail(detail_file)
        with pytest.raises(ValueError):
            compute(stops, rate_per_hour=0)

    def test_unbilled_stops_are_separated_not_dropped(self, detail_file):
        """A third of the real file carries no revenue. A ratio would divide by
        zero and a filter would quietly lose a third of the book."""
        stops, _ = load_detail(detail_file)
        report = compute(stops, rate_per_hour=45.0)
        assert report.unbilled
        assert all(s.revenue for s in report.billed)

    def test_cost_rises_with_the_rate(self, detail_file):
        stops, _ = load_detail(detail_file)
        cheap = compute(stops, rate_per_hour=30.0).summary()
        dear = compute(stops, rate_per_hour=90.0).summary()
        assert dear["loss_making_stops"] >= cheap["loss_making_stops"]

    def test_the_summary_says_the_cost_is_a_lower_bound(self, detail_file):
        """There is no distance anywhere in the export, so fuel, wear and the
        vehicle are all missing. Every figure understates the loss."""
        stops, _ = load_detail(detail_file)
        assert "lower_bound" in " ".join(compute(stops, rate_per_hour=45.0).summary())

    def test_the_route_cut_refuses_when_it_would_say_nothing(self, detail_file):
        from ml.prd.trip_cost import by_route_size

        stops, _ = load_detail(detail_file)
        result = by_route_size(compute(stops, rate_per_hour=45.0))
        assert result["usable"] is False
        assert "REC-5" in result["reason"]
