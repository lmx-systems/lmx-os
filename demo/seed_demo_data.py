"""
Seeds the one Hub / Client / Shop / Driver the investor demo needs so
demo/epicor_sample_order.json can actually be ingested and assigned -
without this, the ingestion endpoint would 404 with ShopNotFoundError
and there'd be no driver for the optimizer to assign to.

Idempotent: safe to run more than once (e.g. right before every demo) -
it checks for existing rows before inserting, and re-upserts the driver's
Redis fleet state either way so the driver always comes back "available"
even if a previous demo run left it "en_route".

Usage:
    python -m demo.seed_demo_data

Requires DATABASE_URL / REDIS_URL to point at the stack you want to seed
(defaults in app/config.py match `docker compose up`'s port mappings, so
this works unmodified against a local docker-compose stack).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.driver_document import (
    REQUIRED_DOC_TYPES,
    REVIEW_VERIFIED,
    DriverDocument,
)
from app.models.hub import Hub
from app.models.ops_user import OpsUser
from app.models.shop import Shop
from app.schemas.fleet import DriverLocation, DriverState
from demo.ids import CLIENT_ID, DRIVER_ID, HUB_ID, SHOP_EXTERNAL_REF, SHOP_ID

# Hub is downtown; the shop and driver sit a couple miles apart within it -
# close enough that the stub nearest-neighbor optimizer assigns the driver to
# the order in one cycle, which is the point of the demo.
#
# **Austin, because that is where the deliveries are.** These were Atlanta until
# somebody noticed the fleet map: `run_full_loop`'s manifest carries Austin drop
# addresses, which geocode to 30.26/-97.74, so the demo showed a driver about a
# thousand kilometres from every stop on his route. The "couple miles apart"
# above stopped being true the day that manifest was written, and the map is on
# the first screen an investor looks at.
#
# Austin is also the deliberate choice `scripts/seed_austin_world.py` explains:
# the design partner is nowhere near Texas, so a synthetic row can never be
# mistaken for a real one (`CLAUDE.md`'s naming rule).
HUB_LAT, HUB_LNG = 30.2672, -97.7431
SHOP_LAT, SHOP_LNG = 30.2729, -97.7513
DRIVER_LAT, DRIVER_LNG = 30.2785, -97.7460


async def _get_or_create(session: AsyncSession, model, id_, **fields):
    """Create the row, or bring an existing one up to date.

    **It used to return an existing row untouched**, which made "safe to re-run"
    mean *skips* rather than *converges* - so moving the demo hub from Atlanta to
    Austin changed this file and nothing in the database, and the fleet map went
    on showing a driver a thousand kilometres from his own stops. A seeder whose
    second run cannot correct its first is a seeder you have to remember to
    delete around.

    Only the fields named here are written, so anything a demo run has since put
    on the row - a driver's shift state, a shop's location link - is left alone.
    """
    existing = await session.get(model, id_)
    if existing:
        changed = [
            name
            for name, value in fields.items()
            if getattr(existing, name, None) != value
        ]
        for name in changed:
            setattr(existing, name, fields[name])
        if changed:
            await session.commit()
        return existing, False
    row = model(id=id_, **fields)
    session.add(row)
    await session.commit()
    return row, True


async def _seed_compliance_documents(session_factory) -> None:
    """Put a verified licence and insurance on the demo driver (`R4`).

    **Not a bypass of the compliance gate - the gate's own happy path.** `R4`
    refuses to put a driver on shift until every required document is on file,
    reviewed by a named ops user, and unexpired, and that refusal is correct and
    worth demonstrating. What it is not is something to hit by accident: without
    these rows the driver app stops at the documents screen before a demo ever
    reaches a delivery, and `run_full_loop` has been printing "seed reviewed
    driver documents and R4 will let the clock start" on every run.

    It matters beyond the app blocking. No shift means no `driver_shift_event`,
    which means `REC-2` has no wage to attribute and `app/record/cost.py`
    reports a day of drops it cannot cost - so the whole cost-per-drop half of
    the story is unavailable until this exists.

    **Attributed to a real ops user**, because `reviewed_by_ops_user_id` is a
    foreign key and the column's own comment says why: *"an unattributed
    compliance decision is not much better than no decision - if a driver turns
    out to have been cleared on a bad document, the question 'who cleared it'
    has to have an answer."* A demo should not be the thing that makes that
    answer null.
    """
    expires = date.today() + timedelta(days=365)
    async with session_factory() as session:
        reviewer = (
            await session.execute(select(OpsUser).order_by(OpsUser.created_at).limit(1))
        ).scalar_one_or_none()
        if reviewer is None:
            print(
                "  (no ops user exists yet, so documents were not seeded - run "
                "scripts/create_ops_user.py, then this again)"
            )
            return

        for doc_type in REQUIRED_DOC_TYPES:
            existing = (
                await session.execute(
                    select(DriverDocument).where(
                        DriverDocument.driver_id == DRIVER_ID,
                        DriverDocument.doc_type == doc_type,
                    )
                )
            ).scalar_one_or_none()
            document = existing or DriverDocument(driver_id=DRIVER_ID, doc_type=doc_type)
            document.claimed_expires_at = expires
            # The only expiry any gate may act on, which is why it is set
            # separately from the claimed one rather than copied blindly in
            # real life.
            document.verified_expires_at = expires
            document.review_status = REVIEW_VERIFIED
            document.reviewed_at = datetime.now(timezone.utc)
            document.reviewed_by_ops_user_id = uuid.UUID(str(reviewer.id))
            # **Not None.** `evaluate_driver_documents` treats a row with no
            # `file_url` as *missing*, not as pending - "a row with nothing
            # uploaded against it ... means we hold no evidence", which is the
            # case the old gate scored as a pass. Seeding the row without this
            # produced two verified documents and a driver the gate still
            # refused, which is the check being right and the seed being wrong.
            #
            # A marker rather than a fabricated image: the stub upload client
            # issues exactly this shape when no storage is configured, and a
            # demo should not manufacture a photograph of a licence.
            document.file_url = f"local-capture://demo/{doc_type}-not-a-real-document"
            if existing is None:
                session.add(document)
        await session.commit()
    print(f"  Docs   : licence + insurance verified to {expires.isoformat()}")


async def seed() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        hub, hub_created = await _get_or_create(
            session, Hub, HUB_ID,
            name="LMX Demo Hub",
            # Austin, matching the coordinates and the manifest's addresses.
            # It was America/New_York, which the hold windows and the nightly
            # job both read - a demo hub whose clock disagrees with its own
            # geography is a quiet way to produce deadlines nobody can explain.
            timezone="America/Chicago",
            lat=HUB_LAT, lng=HUB_LNG,
        )
        client, client_created = await _get_or_create(
            session, Client, CLIENT_ID,
            hub_id=HUB_ID, name="Demo Auto Parts Distributor", pos_system="epicor",
        )
        shop, shop_created = await _get_or_create(
            session, Shop, SHOP_ID,
            client_id=CLIENT_ID, name="Demo Auto Parts - Midtown", address="123 Peachtree St, Atlanta, GA",
            lat=SHOP_LAT, lng=SHOP_LNG, external_ref=SHOP_EXTERNAL_REF,
        )
        driver, driver_created = await _get_or_create(
            session, Driver, DRIVER_ID,
            hub_id=HUB_ID, name="Demo Driver - Jordan P.", phone="+14045550100",
            vehicle_capacity_units=5,
        )

    await engine.dispose()

    # Redis fleet state - always re-upserted "available" so the demo works
    # even if a prior run left the driver mid-route.
    await _seed_compliance_documents(session_factory)

    fleet_state = FleetStateManager()
    await fleet_state.upsert_driver_state(
        DriverState(
            driver_id=str(DRIVER_ID), hub_id=str(HUB_ID), status="available",
            capacity_units=5, load_units=0,
        )
    )
    await fleet_state.update_driver_location(
        DriverLocation(
            driver_id=str(DRIVER_ID), lat=DRIVER_LAT, lng=DRIVER_LNG,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        str(HUB_ID),
    )

    print("Demo data ready:")
    print(f"  Hub    : {HUB_ID}  ({'created' if hub_created else 'already existed'})")
    print(f"  Client : {CLIENT_ID}  ({'created' if client_created else 'already existed'})")
    print(f"  Shop   : {SHOP_ID}  external_ref={SHOP_EXTERNAL_REF}  ({'created' if shop_created else 'already existed'})")
    print(f"  Driver : {DRIVER_ID}  status=available  ({'created' if driver_created else 'already existed'})")
    print()
    print("Next: python -m demo.send_demo_order")


if __name__ == "__main__":
    asyncio.run(seed())
