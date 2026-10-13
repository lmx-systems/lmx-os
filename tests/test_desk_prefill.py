"""DE-15's desk rules, on invented sites: no real address appears here."""
import math
import uuid

from app.record.desk_prefill import (
    CITY_CORE_UNITS_PER_SQ_MI,
    SETTING_RADIUS_M,
    Place,
    StreetAddress,
    evidence_for_site,
    housing_units_per_sq_mi,
    kind_from_places,
    normalize_street,
    parse_site_address,
    parse_street_line,
    setting_from_density,
    shares_address_from_places,
)

_CIRCLE_SQ_MI = math.pi * SETTING_RADIUS_M**2 / 2_589_988.11


def _shop(category="automotive_repair", confidence=0.9, status="open", alternates=()):
    return Place(category=category, alternates=tuple(alternates), confidence=confidence, operating_status=status)


class TestAddresses:
    def test_a_site_address_becomes_number_street_and_zip(self):
        assert parse_site_address("100 Example Avenue, Sometown, NJ 07000") == StreetAddress("100", "example ave", "07000")

    def test_a_suite_and_a_number_range_name_the_same_building(self):
        assert parse_street_line("100-104 Example Ave Suite 3", "07000") == StreetAddress("100", "example ave", "07000")

    def test_numbered_highways_meet_whatever_they_are_called(self):
        names = ["Route 46", "US Highway 46", "Rt. 46 West", "US-46", "NJ-46", "State Route 46", "Rte 46 E"]
        assert {normalize_street(n) for n in names} == {"route 46"}

    def test_the_usual_abbreviations_fold(self):
        assert normalize_street("North Example Boulevard") == normalize_street("N. Example Blvd")

    def test_a_different_house_number_is_a_different_building(self):
        assert parse_street_line("100 Example Ave", "07000") != parse_street_line("102 Example Ave", "07000")

    def test_no_number_or_no_zip_names_no_building(self):
        assert parse_site_address("Example Avenue, Sometown, NJ 07000") is None
        assert parse_site_address("100 Example Avenue, Sometown, NJ") is None
        assert parse_street_line("100 Example Ave", None) is None

    def test_a_street_that_merely_contains_state_is_not_a_highway(self):
        assert normalize_street("State Street") == "state st"


class TestKind:
    def test_a_repair_shop_and_a_tire_shop_are_one_kind(self):
        assert kind_from_places([_shop("automotive_repair"), _shop("tire_shop")]) == "independent_repair_or_tire"

    def test_two_kinds_at_one_address_leave_it_unknown(self):
        assert kind_from_places([_shop("auto_dealer"), _shop("auto_body_shop")]) is None

    def test_a_place_with_no_kind_does_not_decide_it(self):
        assert kind_from_places([_shop("auto_body_shop"), _shop("restaurant")]) == "body_shop"

    def test_nothing_mapped_is_unknown_not_other(self):
        assert kind_from_places([_shop("restaurant")]) is None
        assert kind_from_places([]) is None

    def test_a_gas_station_has_bays_only_when_it_is_also_a_repair_shop(self):
        assert kind_from_places([_shop("gas_station")]) is None
        assert kind_from_places([_shop("gas_station", alternates=["automotive_repair"])]) == "gas_station_with_bays"

    def test_used_car_lots_are_not_dealership_counters(self):
        assert kind_from_places([_shop("used_auto_dealer")]) is None

    def test_closed_or_doubtful_places_do_not_count(self):
        assert kind_from_places([_shop("auto_body_shop", status="permanently_closed")]) is None
        assert kind_from_places([_shop("auto_body_shop", confidence=0.2)]) is None


class TestSharesAddress:
    def test_two_businesses_share_it(self):
        assert shares_address_from_places([_shop(), _shop("restaurant")]) is True

    def test_one_listed_business_is_unknown_not_no(self):
        """One listing does not show one tenant: unlisted neighbours are common."""
        assert shares_address_from_places([_shop()]) is None

    def test_none_listed_is_unknown(self):
        assert shares_address_from_places([]) is None
        assert shares_address_from_places([_shop(confidence=0.1)]) is None

    def test_one_business_listed_twice_is_one_tenant(self):
        twice = [Place("automotive_repair", (), 0.9, "open", 40.0, -74.0), Place("automotive_repair", (), 0.8, "open", 40.0001, -74.0)]
        assert shares_address_from_places(twice) is None

    def test_two_kinds_side_by_side_are_two_tenants(self):
        pair = [Place("automotive_repair", (), 0.9, "open", 40.0, -74.0), Place("auto_body_shop", (), 0.9, "open", 40.0001, -74.0)]
        assert shares_address_from_places(pair) is True

    def test_an_atm_is_not_a_tenant(self):
        assert shares_address_from_places([_shop(), _shop("atm")]) is None


class TestSetting:
    def test_the_thresholds(self):
        assert setting_from_density(CITY_CORE_UNITS_PER_SQ_MI) == "city_core"
        assert setting_from_density(CITY_CORE_UNITS_PER_SQ_MI - 1) == "suburb"
        assert setting_from_density(102) == "suburb"
        assert setting_from_density(101) is None

    def test_only_blocks_within_the_radius_count(self):
        lat, lng = 40.0, -74.0
        near = (lat + 0.005, lng, 1000)  # about 560 m north
        far = (lat + 0.02, lng, 5000)  # about 2.2 km north
        density = housing_units_per_sq_mi(lat, lng, [near, far])
        assert math.isclose(density, 1000 / _CIRCLE_SQ_MI)


class TestEvidence:
    address = StreetAddress("100", "example ave", "07000")

    def test_an_address_public_data_does_not_know_gets_nothing(self):
        ev = evidence_for_site(uuid.uuid4(), self.address, [], [], [(40.0, -74.0, 10_000)])
        assert not ev.matched
        assert (ev.kind_of_site, ev.setting, ev.shares_address) == (None, None, None)

    def test_a_found_site_gets_its_kind_and_setting(self):
        blocks = [(40.0, -74.0, int(CITY_CORE_UNITS_PER_SQ_MI * _CIRCLE_SQ_MI) + 1)]
        shop = Place("auto_body_shop", (), 0.9, "open", 40.0, -74.0, "07000")
        ev = evidence_for_site(uuid.uuid4(), self.address, [(40.0, -74.0)], [shop], blocks)
        assert ev.matched
        assert (ev.kind_of_site, ev.setting, ev.shares_address) == ("body_shop", "city_core", None)

    def test_places_locate_a_site_the_address_points_miss(self):
        place = Place("auto_parts_store", (), 0.9, "open", 40.0, -74.0, "07000")
        blocks = [(40.0, -74.0, 500)]
        ev = evidence_for_site(uuid.uuid4(), self.address, [], [place], blocks)
        assert (ev.kind_of_site, ev.setting) == ("parts_store_counter", "suburb")

    def test_a_place_listed_in_another_zip_counts_on_the_same_stretch_of_street(self):
        near = Place("auto_body_shop", (), 0.9, "open", 40.001, -74.0, "07999")  # about 110 m away
        ev = evidence_for_site(uuid.uuid4(), self.address, [(40.0, -74.0)], [near], [])
        assert ev.kind_of_site == "body_shop"

    def test_a_namesake_street_in_another_town_does_not(self):
        elsewhere = Place("auto_body_shop", (), 0.9, "open", 40.2, -74.0, "07999")  # about 22 km away
        ev = evidence_for_site(uuid.uuid4(), self.address, [(40.0, -74.0)], [elsewhere], [])
        assert ev.kind_of_site is None
        assert ev.shares_address is None

    def test_without_an_address_point_another_zip_cannot_be_checked(self):
        elsewhere = Place("auto_body_shop", (), 0.9, "open", 40.0, -74.0, "07999")
        ev = evidence_for_site(uuid.uuid4(), self.address, [], [elsewhere], [])
        assert not ev.matched
