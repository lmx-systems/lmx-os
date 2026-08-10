"""
CSV manifest upload (docs/LMX_LINK_PLAN.md T3) against real Postgres + Redis.

T3's exit criterion: *"a 40-row manifest imports, with bad rows reported and good rows
dispatched"*. `test_a_forty_row_manifest_imports_with_bad_rows_reported` is that
criterion, written down.

**Two properties carry most of the weight here, and both are about not losing orders:**

  - every line of the file comes back exactly once, as an order or as an error. A
    dispatcher who uploads 40 lines and gets 38 deliveries with no account of the other
    two has lost orders they still believe are coming.
  - line numbers are the dispatcher's, counting the header, because they are looking at
    the same file in a spreadsheet.

The parser's generosity about headers is the third: a manifest arrives with whatever
its source system emits, and demanding one exact spelling turns a five-second import
into a support conversation.
"""
import io
import uuid

import pytest
from fastapi import HTTPException, UploadFile

from app.api.client_routes import upload_order_manifest
from app.client_auth.dependencies import AuthedClient
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.manifest import (
    MAX_ROWS,
    ManifestUnreadable,
    parse_manifest,
)
from app.models.client import Client
from app.models.hub import Hub
from app.models.shop import Shop

pytestmark = pytest.mark.integration

PICKUP = "1200 E 6th St, Austin TX"


class _Geocoder(BaseGeocoder):
    provider_name = "fake"

    def __init__(self, unresolvable: set[str] | None = None) -> None:
        self.unresolvable = unresolvable or set()

    async def geocode(self, address: str) -> GeocodeResult | None:
        if any(bad in address for bad in self.unresolvable):
            return None
        return GeocodeResult(lat=30.26, lng=-97.74, display_name=address, provider="fake")


@pytest.fixture
def geocoder(monkeypatch):
    """Swapped at the client-routes seam, since the endpoint resolves it per call."""
    import app.api.client_routes as routes

    holder = {"geocoder": _Geocoder()}
    monkeypatch.setattr(routes, "get_geocoder", lambda: holder["geocoder"])
    return holder


async def _seed(db_session):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Design Partner",
            pos_system="client_portal",
            signup_status="active",
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address=PICKUP,
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


def _authed(client_id) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@distributor.test",
        name="Dana",
        role="admin",
    )


def _upload(text: str, filename: str = "manifest.csv") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(text.encode("utf-8")))


async def _post(db_session, client_id, shop_id, text: str):
    return await upload_order_manifest(
        file=_upload(text),
        deadline="today",
        pickup_shop_id=str(shop_id),
        pickup_address=None,
        client=_authed(client_id),
        session=db_session,
    )


# ---------------------------------------------------------------------------
# T3's exit criterion
# ---------------------------------------------------------------------------


async def test_a_forty_row_manifest_imports_with_bad_rows_reported(
    db_session, real_redis_client, geocoder
):
    """**T3's exit criterion, written down.** 40 rows in, good ones dispatched, bad ones
    reported by line number, nothing silently dropped."""
    hub_id, client_id, shop_id = await _seed(db_session)
    geocoder["geocoder"] = _Geocoder(unresolvable={"NOWHERE"})

    lines = ["Order Number,Delivery Address,Customer Name"]
    for index in range(40):
        # Two rows the geocoder can't place, and one with no address at all.
        if index == 7:
            address = "NOWHERE at all"
        elif index == 19:
            address = "NOWHERE either"
        elif index == 31:
            address = ""
        else:
            address = f"{100 + index} Congress Ave, Austin TX"
        lines.append(f"INV-{1000 + index},{address},Contact {index}")

    result = await _post(db_session, client_id, shop_id, "\n".join(lines))

    # Every line accounted for, exactly once.
    assert len(result.results) == 40
    assert len({r.line_number for r in result.results}) == 40
    assert result.accepted == 37
    assert result.failed == 3

    # And the failures are the ones we planted, by spreadsheet line number.
    failed_lines = {r.line_number for r in result.results if r.error}
    assert failed_lines == {9, 21, 33}
    assert all(r.order is not None for r in result.results if r.line_number not in failed_lines)


async def test_line_numbers_are_the_ones_the_dispatcher_sees(
    db_session, real_redis_client, geocoder
):
    """Counting the header and starting at 1. "Row 4" that means the fifth line is worse
    than no line number."""
    hub_id, client_id, shop_id = await _seed(db_session)

    result = await _post(
        db_session,
        client_id,
        shop_id,
        "Delivery Address\n,\n900 Congress Ave, Austin TX\n",
    )

    # The blank-ish line is row 2 in a spreadsheet; the real one is row 3.
    assert {r.line_number for r in result.results} == {3}


async def test_results_come_back_in_file_order(db_session, real_redis_client, geocoder):
    """Parse errors and ingestion failures interleave, and a dispatcher reads down their
    file - so a report ordered by "errors first" makes them hunt."""
    hub_id, client_id, shop_id = await _seed(db_session)
    text = "Delivery Address\n1 A St, Austin TX\n\n\n2 B St, Austin TX\n"
    # A row with a header but no address between two good ones.
    text = "Delivery Address\n1 A St, Austin TX\n,\n2 B St, Austin TX\n"

    result = await _post(db_session, client_id, shop_id, text)

    assert [r.line_number for r in result.results] == sorted(
        r.line_number for r in result.results
    )


# ---------------------------------------------------------------------------
# Reading whatever the source system emitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Delivery Address",
        "delivery address",
        "Ship To Address",
        "Customer Address",
        "DESTINATION",
        "address",
    ],
)
def test_the_address_column_is_matched_generously(header):
    """A manifest arrives with whatever its source system emits. Demanding one exact
    spelling turns a five-second import into a support conversation."""
    parsed = parse_manifest(f"{header}\n900 Congress Ave\n")

    assert len(parsed.rows) == 1
    assert parsed.rows[0].drop_address == "900 Congress Ave"


def test_a_more_specific_column_wins_over_a_generic_one():
    """A file with both `Delivery Address` and `Address` must use the delivery one."""
    parsed = parse_manifest("Address,Delivery Address\nbilling st,900 Congress Ave\n")

    assert parsed.rows[0].drop_address == "900 Congress Ave"
    assert parsed.column_mapping["drop_address"] == "Delivery Address"


def test_two_columns_meaning_the_same_thing_is_refused_rather_than_guessed():
    """**Mapping one arbitrarily would send every delivery to the wrong column,
    silently.** A whole-file refusal is recoverable; forty wrong deliveries are not."""
    with pytest.raises(ManifestUnreadable, match="Two columns"):
        parse_manifest("Delivery Address,delivery address\na,b\n")


def test_semicolon_and_tab_separated_files_are_read():
    """European exports use semicolons, and plenty of "CSV" files are tab-separated."""
    for text in (
        "Delivery Address;Order Number\n900 Congress Ave;INV-1\n",
        "Delivery Address\tOrder Number\n900 Congress Ave\tINV-1\n",
    ):
        parsed = parse_manifest(text)
        assert parsed.rows[0].drop_address == "900 Congress Ave"
        assert parsed.rows[0].reference == "INV-1"


def test_the_column_mapping_is_reported_back():
    """So a dispatcher can see we read "Ship To Address" as the destination, rather than
    wondering why every delivery is going to one place."""
    parsed = parse_manifest("Ship To Address,PO Number,Ship To Name\na,b,c\n")

    assert parsed.column_mapping == {
        "drop_address": "Ship To Address",
        "reference": "PO Number",
        "drop_contact_name": "Ship To Name",
    }


def test_a_file_with_no_address_column_is_a_whole_file_failure():
    """Not forty identical row errors - the dispatcher has one thing to fix, and
    reporting it per row buries it."""
    with pytest.raises(ManifestUnreadable, match="delivery address column"):
        parse_manifest("Order Number,Customer\nINV-1,Acme\n")


def test_an_empty_or_headerless_file_is_refused_clearly():
    for text in ("", "   \n"):
        with pytest.raises(ManifestUnreadable, match="empty"):
            parse_manifest(text)
    with pytest.raises(ManifestUnreadable, match="no rows"):
        parse_manifest("Delivery Address\n")


def test_trailing_blank_lines_are_not_reported_as_errors():
    """Every export produces them, and a report full of phantom failures trains people
    to ignore the report."""
    parsed = parse_manifest("Delivery Address\n900 Congress Ave\n\n\n")

    assert len(parsed.rows) == 1
    assert parsed.errors == []


def test_a_row_with_no_address_is_an_error_not_a_silent_skip():
    """A line the dispatcher typed and we discarded is an order they think is coming."""
    parsed = parse_manifest("Delivery Address,Order Number\n,INV-9\n")

    assert parsed.rows == []
    assert parsed.errors[0].line_number == 2
    assert "No delivery address" in parsed.errors[0].message


def test_an_oversized_file_is_refused_before_it_is_parsed():
    huge = "Delivery Address\n" + ("x" * 300 + "\n") * 2000
    with pytest.raises(ManifestUnreadable, match="too large"):
        parse_manifest(huge)


def test_rows_beyond_the_cap_are_reported_not_truncated_silently():
    """A row limit that drops the tail without saying so is the worst version of this
    feature - reported once, rather than as thousands of identical errors."""
    text = "Delivery Address\n" + "".join(
        f"{n} Congress Ave\n" for n in range(MAX_ROWS + 50)
    )
    parsed = parse_manifest(text)

    assert len(parsed.rows) == MAX_ROWS
    assert len(parsed.errors) == 1
    assert "limited to" in parsed.errors[0].message


def test_an_over_long_optional_field_is_truncated_not_rejected():
    """Failing a whole delivery over a reference nothing routes on would be the wrong
    trade."""
    parsed = parse_manifest("Delivery Address,Order Number\n900 Congress Ave," + "R" * 400 + "\n")

    assert len(parsed.rows[0].reference) == 120


# ---------------------------------------------------------------------------
# It goes through the same ingestion as everything else
# ---------------------------------------------------------------------------


async def test_manifest_orders_reach_the_hold_queue(db_session, real_redis_client, geocoder):
    """§1.1: a new adapter must not need a new way for orders to be created. This one
    parses to the same rows the paste path takes and calls the same function."""
    from app.batch_queue.store import HoldQueueStore

    hub_id, client_id, shop_id = await _seed(db_session)

    await _post(
        db_session,
        client_id,
        shop_id,
        "Delivery Address\n900 Congress Ave, Austin TX\n901 Congress Ave, Austin TX\n",
    )

    held = await HoldQueueStore().get_all(str(hub_id))
    assert len(held) == 2


async def test_a_reference_from_the_file_is_kept(db_session, real_redis_client, geocoder):
    """It is the id the dispatcher will quote when they call about this delivery."""
    hub_id, client_id, shop_id = await _seed(db_session)

    result = await _post(
        db_session,
        client_id,
        shop_id,
        "Order Number,Delivery Address\nINV-4242,900 Congress Ave, Austin TX\n",
    )

    assert result.results[0].order.reference == "INV-4242"


async def test_a_row_with_no_reference_still_imports(db_session, real_redis_client, geocoder):
    """Plenty of manifests have no order number column at all."""
    hub_id, client_id, shop_id = await _seed(db_session)

    result = await _post(
        db_session, client_id, shop_id, "Delivery Address\n900 Congress Ave, Austin TX\n"
    )

    assert result.accepted == 1
    assert result.results[0].order.reference


async def test_a_non_text_file_is_refused_clearly(db_session, real_redis_client, geocoder):
    hub_id, client_id, shop_id = await _seed(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await upload_order_manifest(
            file=UploadFile(filename="scan.png", file=io.BytesIO(b"\x89PNG\r\n\x1a\n\xff\xfe\xfd")),
            deadline="today",
            pickup_shop_id=str(shop_id),
            pickup_address=None,
            client=_authed(client_id),
            session=db_session,
        )
    assert exc_info.value.status_code == 422


async def test_a_windows_encoded_file_is_read(db_session, real_redis_client, geocoder):
    """Excel on Windows still writes cp1252, and one smart quote in a contact name
    should not be a support ticket."""
    hub_id, client_id, shop_id = await _seed(db_session)
    text = "Delivery Address,Ship To Name\n900 Congress Ave, Austin TX,O\u2019Brien\n"

    result = await upload_order_manifest(
        file=UploadFile(filename="m.csv", file=io.BytesIO(text.encode("cp1252"))),
        deadline="today",
        pickup_shop_id=str(shop_id),
        pickup_address=None,
        client=_authed(client_id),
        session=db_session,
    )

    assert result.accepted == 1


async def test_a_utf8_bom_does_not_become_part_of_the_first_header(
    db_session, real_redis_client, geocoder
):
    """Excel writes one, and a BOM stuck to "Delivery Address" makes the column
    unmatchable - which would surface as "we couldn't find a delivery address column" on
    a file that plainly has one."""
    hub_id, client_id, shop_id = await _seed(db_session)
    text = "Delivery Address\n900 Congress Ave, Austin TX\n"

    result = await upload_order_manifest(
        file=UploadFile(filename="m.csv", file=io.BytesIO(b"\xef\xbb\xbf" + text.encode())),
        deadline="today",
        pickup_shop_id=str(shop_id),
        pickup_address=None,
        client=_authed(client_id),
        session=db_session,
    )

    assert result.accepted == 1
