"""DE-8: test sites are drawn per region and group, once, and never in New Jersey.

D-TEST's draw against the migrated schema. The refusal on the training side is
`tests/test_modality_splits.py`.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.identity import resolve_location
from app.models.site_draw import SiteDraw
from app.record.site_draws import DrawRefused, draw_test_sites, frozen_sites_document

pytestmark = pytest.mark.integration


async def _sites(db_session, n, *, region="TX", kind="body_shop", setting="suburb"):
    sites = []
    for _ in range(n):
        location = await resolve_location(
            db_session, address=f"{uuid.uuid4().int % 90000 + 100} Oak Street, Austin, TX"
        )
        location.region, location.kind_of_site, location.setting = region, kind, setting
        sites.append(location)
    await db_session.flush()
    return sites


async def test_each_full_block_of_a_group_gives_exactly_one_test_site(db_session):
    await _sites(db_session, 8)
    await _sites(db_session, 4, kind="dealership_counter", setting="city_core")

    report = await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")

    assert report.drawn == 12
    assert report.test_by_group[("body_shop", "suburb")] == 2
    assert report.test_by_group[("dealership_counter", "city_core")] == 1
    assert report.test == 3


async def test_a_site_waits_until_its_group_is_known(db_session):
    await _sites(db_session, 2)
    await _sites(db_session, 3, setting=None)

    report = await draw_test_sites(db_session, region="TX", share=0.5, drawn_by="test")

    assert (report.drawn, report.waiting_for_group) == (2, 3)


async def test_a_site_is_drawn_once_and_later_draws_continue_the_blocks(db_session):
    await _sites(db_session, 3)
    first = await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")
    before = {
        d.location_id: d.is_test for d in (await db_session.scalars(select(SiteDraw))).all()
    }

    await _sites(db_session, 5)
    second = await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")

    draws = (await db_session.scalars(select(SiteDraw))).all()
    assert (first.drawn, second.drawn, second.already_drawn) == (3, 5, 3)
    assert {d.location_id: d.is_test for d in draws if d.location_id in before} == before
    assert sorted(d.position for d in draws) == list(range(8))
    # Two full blocks of four across the two runs: exactly two test sites.
    assert sum(d.is_test for d in draws) == 2


async def test_a_region_keeps_the_share_it_was_first_drawn_at(db_session):
    await _sites(db_session, 2)
    await draw_test_sites(db_session, region="TX", share=0.2, drawn_by="test")

    with pytest.raises(DrawRefused, match="drawn at a share of 0.2"):
        await draw_test_sites(db_session, region="TX", share=0.3, drawn_by="test")


async def test_new_jersey_is_never_drawn(db_session):
    sites = await _sites(db_session, 2, region="NJ")

    with pytest.raises(DrawRefused, match="D-NJ"):
        await draw_test_sites(db_session, region="NJ", share=0.2, drawn_by="test")

    # And not by any other path: the table refuses it too.
    db_session.add(
        SiteDraw(
            location_id=sites[0].id, region="NJ", kind_of_site="body_shop", setting="suburb",
            position=0, is_test=True, share=0.2, salt="x", drawn_by="test",
        )
    )
    with pytest.raises(IntegrityError, match="ck_test_site_draws_not_nj"):
        await db_session.flush()


async def test_the_export_lists_test_sites_only(db_session):
    await _sites(db_session, 4)
    await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")

    document = await frozen_sites_document(db_session)

    assert len(document["test_sites"]) == 1
    assert document["test_sites"][0]["group"] == ["body_shop", "suburb"]
