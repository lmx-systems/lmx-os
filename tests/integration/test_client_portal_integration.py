"""
Client portal auth + admin onboarding (Phase 8) against real Postgres.
Calls the route functions directly, same pattern as
tests/integration/test_driver_app_integration.py.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import onboard_client
from app.api.client_routes import get_my_order, get_my_profile, list_my_orders, login
from app.client_auth.dependencies import AuthedClient
from app.client_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS
from app.client_auth.tokens import decode_token
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.admin import ClientOnboardingBody, RateOnboardingInput, ShopOnboardingInput
from app.schemas.client_auth import ClientLoginBody

pytestmark = pytest.mark.integration


async def _seed_hub(db_session) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Onboarding Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    return hub_id


def _onboarding_body(hub_id: uuid.UUID, email: str = "ap@customerwarehouse.example") -> ClientOnboardingBody:
    return ClientOnboardingBody(
        hub_id=str(hub_id),
        name="Customer Warehouse",
        pos_system="flat_file",
        shops=[
            ShopOnboardingInput(
                name="Main Branch", address="1 Distribution Way", lat=34.06, lng=-118.24,
                external_ref="CW-SHOP-1", phone="+15555550100",
            )
        ],
        rates=[
            RateOnboardingInput(sla_tier="T2", rate_per_drop_cents=1_800),
            RateOnboardingInput(sla_tier="HOT_SHOT", rate_per_drop_cents=4_500),
        ],
        portal_email=email,
        portal_password="correct horse battery staple",
    )


async def test_onboard_client_creates_client_shop_rates_and_portal_login(db_session):
    hub_id = await _seed_hub(db_session)

    result = await onboard_client(_onboarding_body(hub_id), session=db_session)

    client = await db_session.get(Client, uuid.UUID(result.client_id))
    assert client.name == "Customer Warehouse"
    assert client.portal_email == "ap@customerwarehouse.example"
    # Never stores the plaintext password.
    assert client.portal_password_hash != "correct horse battery staple"
    assert client.portal_password_hash is not None

    assert len(result.shop_ids) == 1
    shop = await db_session.get(Shop, uuid.UUID(result.shop_ids[0]))
    assert shop.name == "Main Branch"
    assert shop.client_id == client.id

    rates_result = await db_session.execute(
        select(ClientRate).where(ClientRate.client_id == client.id)
    )
    rates_by_tier = {r.sla_tier: r.rate_per_drop_cents for r in rates_result.scalars().all()}
    assert rates_by_tier == {"T2": 1_800, "HOT_SHOT": 4_500}


async def test_onboard_client_rejects_duplicate_portal_email(db_session):
    hub_id = await _seed_hub(db_session)
    await onboard_client(_onboarding_body(hub_id, email="dupe@example.com"), session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await onboard_client(_onboarding_body(hub_id, email="dupe@example.com"), session=db_session)
    assert exc_info.value.status_code == 409


async def test_onboard_client_rejects_unknown_sla_tier(db_session):
    hub_id = await _seed_hub(db_session)
    body = _onboarding_body(hub_id, email="badtier@example.com")
    body.rates = [RateOnboardingInput(sla_tier="T99", rate_per_drop_cents=100)]

    with pytest.raises(HTTPException) as exc_info:
        await onboard_client(body, session=db_session)
    assert exc_info.value.status_code == 422


async def test_client_login_succeeds_with_correct_credentials_and_issues_a_usable_token(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    result = await onboard_client(_onboarding_body(hub_id, email="login-ok@example.com"), session=db_session)

    token = await login(
        ClientLoginBody(email="login-ok@example.com", password="correct horse battery staple"),
        session=db_session,
    )
    assert decode_token(token.access_token) == result.client_id


async def test_client_login_rejects_wrong_password(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    await onboard_client(_onboarding_body(hub_id, email="login-bad-pw@example.com"), session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await login(
            ClientLoginBody(email="login-bad-pw@example.com", password="wrong password"),
            session=db_session,
        )
    assert exc_info.value.status_code == 401


async def test_client_login_rejects_unknown_email(db_session, real_redis_client):
    with pytest.raises(HTTPException) as exc_info:
        await login(
            ClientLoginBody(email="nobody@example.com", password="whatever"),
            session=db_session,
        )
    assert exc_info.value.status_code == 401


async def test_client_login_is_rate_limited_after_too_many_attempts(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    await onboard_client(_onboarding_body(hub_id, email="rate-limited@example.com"), session=db_session)

    for _ in range(MAX_LOGIN_ATTEMPTS):
        with pytest.raises(HTTPException) as exc_info:
            await login(
                ClientLoginBody(email="rate-limited@example.com", password="wrong password"),
                session=db_session,
            )
        assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await login(
            ClientLoginBody(email="rate-limited@example.com", password="wrong password"),
            session=db_session,
        )
    assert exc_info.value.status_code == 429


async def test_client_login_rate_limit_resets_after_a_successful_login(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    await onboard_client(_onboarding_body(hub_id, email="reset-on-success@example.com"), session=db_session)

    for _ in range(MAX_LOGIN_ATTEMPTS - 1):
        with pytest.raises(HTTPException) as exc_info:
            await login(
                ClientLoginBody(email="reset-on-success@example.com", password="wrong password"),
                session=db_session,
            )
        assert exc_info.value.status_code == 401

    # One correct login before the cap resets the counter...
    await login(
        ClientLoginBody(email="reset-on-success@example.com", password="correct horse battery staple"),
        session=db_session,
    )

    # ...so a fresh run of wrong attempts starts from zero again instead of
    # immediately 429ing.
    with pytest.raises(HTTPException) as exc_info:
        await login(
            ClientLoginBody(email="reset-on-success@example.com", password="wrong password"),
            session=db_session,
        )
    assert exc_info.value.status_code == 401


async def _onboard_and_authed(db_session, email: str) -> tuple[AuthedClient, uuid.UUID, uuid.UUID]:
    hub_id = await _seed_hub(db_session)
    result = await onboard_client(_onboarding_body(hub_id, email=email), session=db_session)
    shop_id = uuid.UUID(result.shop_ids[0])
    return AuthedClient(client_id=result.client_id), uuid.UUID(result.client_id), shop_id


async def test_get_my_profile_returns_client_details(db_session):
    authed, client_id, _shop_id = await _onboard_and_authed(db_session, "profile@example.com")
    profile = await get_my_profile(client=authed, session=db_session)
    assert profile.client_id == str(client_id)
    assert profile.name == "Customer Warehouse"
    assert profile.portal_email == "profile@example.com"


async def test_list_and_get_my_orders_scoped_to_this_client(db_session):
    authed, client_id, shop_id = await _onboard_and_authed(db_session, "orders@example.com")

    client_row = await db_session.get(Client, client_id)
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=client_row.hub_id,
        client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-CLIENT-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.delivered, requested_at=now,
        delivery_address="500 Client St", delivery_contact_name="R. Ortiz",
        fee_cents=1_800,
    )
    db_session.add(order)
    await db_session.commit()

    orders = await list_my_orders(client=authed, session=db_session)
    assert len(orders) == 1
    assert orders[0].order_id == str(order.id)
    assert orders[0].shop_name == "Main Branch"
    assert orders[0].fee_cents == 1_800
    assert orders[0].delivered_at is not None  # status is already "delivered"

    detail = await get_my_order(str(order.id), client=authed, session=db_session)
    assert detail.delivery_address == "500 Client St"
    assert detail.delivery_contact_name == "R. Ortiz"


async def test_get_my_order_404s_for_another_clients_order(db_session):
    authed_a, client_a_id, shop_a_id = await _onboard_and_authed(db_session, "clienta@example.com")
    _authed_b, client_b_id, shop_b_id = await _onboard_and_authed(db_session, "clientb@example.com")

    now = datetime.now(timezone.utc)
    order_b = Order(
        hub_id=(await db_session.get(Client, client_b_id)).hub_id,
        client_id=client_b_id, shop_id=shop_b_id,
        external_order_ref="ORD-CLIENT-B-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.held, requested_at=now,
    )
    db_session.add(order_b)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await get_my_order(str(order_b.id), client=authed_a, session=db_session)
    assert exc_info.value.status_code == 404
