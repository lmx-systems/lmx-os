"""
A client's own delivery record as CSV (docs/ROADMAP.md F7).

The test that carries this file is **`test_a_rating_comment_cannot_smuggle_a_formula`**.
Excel and Sheets execute a cell beginning `=`, `+`, `-`, `@`, a tab or a carriage return
when the file is opened, and since `F13` one of these columns is free text written by an
**unauthenticated stranger** holding a tracking link. A recipient typing
`=HYPERLINK("http://evil","CLICK")` into a rating gets it run on the distributor's
machine. That is not a spreadsheet quirk, it is a delivery mechanism, and this export is
where the two halves meet.

The rest is about not quietly changing the data on the way out: numbers must survive as
numbers (a guard that prefixed `-15` would break the arithmetic the export exists for),
timestamps must be unambiguous, and the file must contain everything rather than a page.
"""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.client_routes import export_my_orders_csv
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.client_user import CLIENT_MEMBER_ROLE
from app.models.delivery_rating import RECIPIENT, DeliveryRating
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.reporting.csv_export import COLUMNS, safe_cell

pytestmark = pytest.mark.integration


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
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _order(db_session, hub_id, client_id, shop_id, **overrides):
    now = datetime.now(timezone.utc)
    fields = dict(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"LMX-{uuid.uuid4().hex[:6]}",
        source_order_ref=f"WO-{uuid.uuid4().hex[:6]}",
        source_system="client_portal",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now,
        weight_units=1,
        status=OrderStatus.delivered,
        requested_at=now - timedelta(hours=3),
        delivered_at=now,
        delivery_address="900 Congress Ave, Austin TX",
        delivery_contact_name="Dana Whitfield",
        fee_cents=1_800,
    )
    fields.update(overrides)
    order = Order(**fields)
    db_session.add(order)
    await db_session.commit()
    return order


def _authed(client_id, role=CLIENT_MEMBER_ROLE) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@example.com",
        name="Alex",
        role=role,
    )


async def _export(db_session, client_id) -> list[list[str]]:
    """Drive the endpoint and parse the streamed body back into rows."""
    response = await export_my_orders_csv(client=_authed(client_id), session=db_session)
    body = ""
    async for chunk in response.body_iterator:
        body += chunk
    return list(csv.reader(io.StringIO(body)))


# ---------------------------------------------------------------------------
# Formula injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dangerous", ["=1+1", "+1", "-1+1", "@SUM(A1)", "\tcmd", "\rcmd"])
def test_every_formula_prefix_is_neutralised(dangerous):
    guarded = safe_cell(dangerous)
    assert guarded.startswith("'")
    # And the original is still readable - an export that silently alters an address is
    # worse than one showing a stray quote.
    assert guarded[1:] == dangerous


def test_ordinary_text_is_untouched():
    for benign in ["900 Congress Ave", "Dana Whitfield", "WO-4471", "", "a=b"]:
        assert safe_cell(benign) == benign


async def test_a_rating_comment_cannot_smuggle_a_formula(db_session):
    """The case this guard exists for.

    A rating comment is written by an unauthenticated stranger holding a tracking link
    (F13). Without the guard, they choose what executes when the distributor opens their
    own export.
    """
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    db_session.add(
        DeliveryRating(
            order_id=order.id,
            rated_by=RECIPIENT,
            score=1,
            comment='=HYPERLINK("http://evil.example","CLICK")',
            first_submitted_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    rows = await _export(db_session, client_id)
    comment = rows[1][COLUMNS.index("recipient_comment")]
    assert comment.startswith("'="), "a recipient must not choose what a spreadsheet runs"
    assert "HYPERLINK" in comment, "and the text must still be readable"


async def test_an_address_cannot_smuggle_a_formula(db_session):
    """The client's own free text, for the same reason - and a distributor's own staff
    typing a leading `-` in an address is the accidental version of this."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _order(db_session, hub_id, client_id, shop_id, delivery_address="=cmd|'/c calc'!A1")

    rows = await _export(db_session, client_id)
    assert rows[1][COLUMNS.index("delivery_address")].startswith("'=")


async def test_numbers_are_not_guarded(db_session):
    """A guard that prefixed a negative number would turn `-15` into text and break the
    arithmetic the export exists to enable. Numbers are formatted by us and never
    user-controlled, so they pass through."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _order(db_session, hub_id, client_id, shop_id, fee_cents=1_800)

    rows = await _export(db_session, client_id)
    assert rows[1][COLUMNS.index("fee_cents")] == "1800"
    assert rows[1][COLUMNS.index("delivery_attempts")] == "1"


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------


async def test_the_header_is_the_declared_column_list(db_session):
    _, client_id, _ = await _seed(db_session)
    rows = await _export(db_session, client_id)
    assert rows[0] == COLUMNS


async def test_an_empty_history_still_returns_a_header(db_session):
    """A zero-byte file looks like a failure. A header with no rows is an answer."""
    _, client_id, _ = await _seed(db_session)
    rows = await _export(db_session, client_id)
    assert rows == [COLUMNS]


async def test_the_export_is_scoped_to_the_caller(db_session):
    """The property that would be worst to get wrong: another distributor's orders."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _order(db_session, hub_id, client_id, shop_id, external_order_ref="MINE-1")

    other_hub, other_client, other_shop = await _seed(db_session)
    await _order(db_session, other_hub, other_client, other_shop, external_order_ref="THEIRS-1")

    rows = await _export(db_session, client_id)
    refs = [r[COLUMNS.index("lmx_reference")] for r in rows[1:]]
    assert refs == ["MINE-1"]


async def test_everything_is_exported_not_a_page(db_session):
    """`GET /client/orders` is paged at 50 because a screen must be bounded. An export is
    a ledger, and silently dropping rows from one is the failure it exists to avoid."""
    hub_id, client_id, shop_id = await _seed(db_session)
    for i in range(120):
        await _order(
            db_session,
            hub_id,
            client_id,
            shop_id,
            external_order_ref=f"LMX-{i:04d}",
            requested_at=datetime.now(timezone.utc) - timedelta(hours=i + 1),
        )

    rows = await _export(db_session, client_id)
    assert len(rows) == 121, "120 orders plus the header"


async def test_rows_are_oldest_first(db_session):
    """An export is read as a ledger, and appending to a previous one should line up."""
    hub_id, client_id, shop_id = await _seed(db_session)
    base = datetime.now(timezone.utc)
    await _order(
        db_session, hub_id, client_id, shop_id, external_order_ref="NEW",
        requested_at=base - timedelta(hours=1),
    )
    await _order(
        db_session, hub_id, client_id, shop_id, external_order_ref="OLD",
        requested_at=base - timedelta(days=5),
    )

    rows = await _export(db_session, client_id)
    assert [r[COLUMNS.index("lmx_reference")] for r in rows[1:]] == ["OLD", "NEW"]


async def test_timestamps_are_iso_utc(db_session):
    """A spreadsheet re-parsing a locale-formatted date is how an export silently changes
    the data it was meant to preserve."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _order(db_session, hub_id, client_id, shop_id)

    rows = await _export(db_session, client_id)
    delivered = rows[1][COLUMNS.index("delivered_at_utc")]
    parsed = datetime.fromisoformat(delivered)
    assert parsed.tzinfo is not None, "an ambiguous timestamp is worse than none"


async def test_the_promised_column_matches_what_billing_credits_against(db_session):
    """One computation again: the export, the portal and the invoice resolve the promise
    through `app/sla/commitment.py`, so a client cannot reconcile their own spreadsheet
    against their statement and find two different targets."""
    from app.sla.commitment import delivery_commitment, terms_for_client

    hub_id, client_id, shop_id = await _seed(db_session)
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()
    order = await _order(db_session, hub_id, client_id, shop_id)

    rows = await _export(db_session, client_id)
    exported = rows[1][COLUMNS.index("promised_delivery_by_utc")]

    fresh = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one()
    terms = await terms_for_client(db_session, client_id)
    expected = delivery_commitment(fresh, terms.get(fresh.sla_tier)).promised_delivery_by
    assert exported == expected.isoformat()


async def test_an_unknown_client_is_refused_before_streaming_begins(db_session):
    """Once a StreamingResponse has started, an exception cannot become a clean status -
    the caller would get a truncated file behind a 200 already on the wire."""
    with pytest.raises(HTTPException) as exc:
        await export_my_orders_csv(client=_authed(uuid.uuid4()), session=db_session)
    assert exc.value.status_code == 404
