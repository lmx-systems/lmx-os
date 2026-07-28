"""
Client portal auth + admin onboarding + multi-user client accounts
(docs/ROADMAP.md C4) against real Postgres. Calls the route functions
directly, same pattern as tests/integration/test_driver_app_integration.py.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import onboard_client
from app.api.client_routes import (
    create_my_client_user,
    get_my_order,
    get_my_profile,
    list_my_client_users,
    list_my_orders,
    login,
    update_my_client_user,
)
from app.client_auth.dependencies import AuthedClient, get_current_client, require_client_admin
from app.client_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS
from app.client_auth.tokens import decode_token, issue_token
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.client_user import CLIENT_ADMIN_ROLE, CLIENT_MEMBER_ROLE, ClientUser
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.admin import ClientOnboardingBody, RateOnboardingInput, ShopOnboardingInput
from app.schemas.client_auth import ClientLoginBody, ClientUserCreateBody, ClientUserUpdateBody

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


async def _admin_user_for(db_session, client_id: uuid.UUID) -> ClientUser:
    result = await db_session.execute(
        select(ClientUser).where(
            ClientUser.client_id == client_id, ClientUser.role == CLIENT_ADMIN_ROLE
        )
    )
    return result.scalars().first()


async def _authed_from_user(user: ClientUser) -> AuthedClient:
    return AuthedClient(
        client_id=str(user.client_id),
        client_user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )


async def test_onboard_client_creates_client_shop_rates_and_first_admin_user(db_session):
    hub_id = await _seed_hub(db_session)

    result = await onboard_client(_onboarding_body(hub_id), session=db_session)

    client = await db_session.get(Client, uuid.UUID(result.client_id))
    assert client.name == "Customer Warehouse"

    # The portal login now lives in its own table as an admin user, not
    # inline on Client (C4).
    admin = await _admin_user_for(db_session, client.id)
    assert admin is not None
    assert admin.email == "ap@customerwarehouse.example"
    assert admin.role == CLIENT_ADMIN_ROLE
    assert admin.is_active is True
    # Never stores the plaintext password.
    assert admin.password_hash != "correct horse battery staple"
    assert admin.password_hash is not None

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
    claims = decode_token(token.access_token)
    # sub is now the user, but the token carries client_id + role too.
    assert claims.client_id == result.client_id
    assert claims.role == CLIENT_ADMIN_ROLE


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


async def test_client_login_rejects_a_deactivated_user(db_session, real_redis_client):
    hub_id = await _seed_hub(db_session)
    result = await onboard_client(_onboarding_body(hub_id, email="deactivated@example.com"), session=db_session)
    admin = await _admin_user_for(db_session, uuid.UUID(result.client_id))
    admin.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await login(
            ClientLoginBody(email="deactivated@example.com", password="correct horse battery staple"),
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
    admin = await _admin_user_for(db_session, uuid.UUID(result.client_id))
    return await _authed_from_user(admin), uuid.UUID(result.client_id), shop_id


async def test_get_my_profile_returns_company_and_signed_in_user(db_session):
    authed, client_id, _shop_id = await _onboard_and_authed(db_session, "profile@example.com")
    profile = await get_my_profile(client=authed, session=db_session)
    assert profile.client_id == str(client_id)
    assert profile.name == "Customer Warehouse"
    assert profile.email == "profile@example.com"
    assert profile.role == CLIENT_ADMIN_ROLE


async def test_list_and_get_my_orders_scoped_to_this_client(db_session):
    authed, client_id, shop_id = await _onboard_and_authed(db_session, "orders@example.com")

    client_row = await db_session.get(Client, client_id)
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=client_row.hub_id,
        client_id=client_id, shop_id=shop_id,
        external_order_ref="ORD-CLIENT-1", source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.delivered, requested_at=now, delivered_at=now,
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


# ---- Multi-user client accounts (C4) ----


async def test_admin_can_create_a_member_who_can_then_log_in(db_session, real_redis_client):
    admin_authed, client_id, _shop_id = await _onboard_and_authed(db_session, "admin@team.example")

    created = await create_my_client_user(
        ClientUserCreateBody(
            email="member@team.example", name="Ops Contact", password="another-strong-pass", role="member"
        ),
        client=admin_authed,
        session=db_session,
    )
    assert created.role == CLIENT_MEMBER_ROLE
    assert created.is_active is True

    users = await list_my_client_users(client=admin_authed, session=db_session)
    assert {u.email for u in users} == {"admin@team.example", "member@team.example"}

    token = await login(
        ClientLoginBody(email="member@team.example", password="another-strong-pass"),
        session=db_session,
    )
    claims = decode_token(token.access_token)
    assert claims.client_id == str(client_id)
    assert claims.role == CLIENT_MEMBER_ROLE


async def test_member_is_forbidden_from_user_management(db_session):
    member = AuthedClient(
        client_id=str(uuid.uuid4()), client_user_id=str(uuid.uuid4()),
        email="m@x.example", name="M", role=CLIENT_MEMBER_ROLE,
    )
    with pytest.raises(HTTPException) as exc_info:
        await require_client_admin(client=member)
    assert exc_info.value.status_code == 403


async def test_create_user_rejects_a_duplicate_email(db_session):
    admin_authed, _client_id, _shop_id = await _onboard_and_authed(db_session, "dup-admin@team.example")
    with pytest.raises(HTTPException) as exc_info:
        await create_my_client_user(
            ClientUserCreateBody(
                email="dup-admin@team.example", name="Clash", password="x" * 8, role="member"
            ),
            client=admin_authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_admin_cannot_manage_another_clients_user(db_session):
    admin_a, _client_a, _shop_a = await _onboard_and_authed(db_session, "a-admin@team.example")
    admin_b, client_b, _shop_b = await _onboard_and_authed(db_session, "b-admin@team.example")
    b_user = await _admin_user_for(db_session, client_b)

    with pytest.raises(HTTPException) as exc_info:
        await update_my_client_user(
            str(b_user.id),
            ClientUserUpdateBody(is_active=False),
            client=admin_a,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


async def test_cannot_deactivate_or_demote_the_last_admin(db_session):
    admin_authed, client_id, _shop_id = await _onboard_and_authed(db_session, "sole-admin@team.example")
    sole_admin = await _admin_user_for(db_session, client_id)

    with pytest.raises(HTTPException) as exc_info:
        await update_my_client_user(
            str(sole_admin.id),
            ClientUserUpdateBody(is_active=False),
            client=admin_authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 409

    with pytest.raises(HTTPException) as exc_info:
        await update_my_client_user(
            str(sole_admin.id),
            ClientUserUpdateBody(role="member"),
            client=admin_authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_last_admin_can_be_demoted_once_a_second_admin_exists(db_session):
    admin_authed, client_id, _shop_id = await _onboard_and_authed(db_session, "first-admin@team.example")

    # Promote a second admin, then the first can be safely demoted.
    second = await create_my_client_user(
        ClientUserCreateBody(email="second-admin@team.example", name="Second", password="x" * 8, role="admin"),
        client=admin_authed,
        session=db_session,
    )
    assert second.role == CLIENT_ADMIN_ROLE

    first_admin = await _admin_user_for(db_session, client_id)
    # _admin_user_for returns *an* admin; pick the original explicitly.
    result = await db_session.execute(
        select(ClientUser).where(
            ClientUser.client_id == client_id, ClientUser.email == "first-admin@team.example"
        )
    )
    first_admin = result.scalar_one()
    updated = await update_my_client_user(
        str(first_admin.id),
        ClientUserUpdateBody(role="member"),
        client=admin_authed,
        session=db_session,
    )
    assert updated.role == CLIENT_MEMBER_ROLE


async def test_deactivating_a_user_revokes_their_session_immediately(db_session):
    admin_authed, client_id, _shop_id = await _onboard_and_authed(db_session, "revoke-admin@team.example")
    member = await create_my_client_user(
        ClientUserCreateBody(email="revoke-me@team.example", name="Temp", password="x" * 8, role="member"),
        client=admin_authed,
        session=db_session,
    )

    # A valid session token for the member...
    token = issue_token(member.client_user_id, str(client_id), member.role)
    authed = await get_current_client(authorization=f"Bearer {token}", session=db_session)
    assert authed.client_user_id == member.client_user_id

    # ...stops working the instant they're deactivated, without waiting for
    # the JWT to expire (dependencies.py re-checks is_active every request).
    await update_my_client_user(
        member.client_user_id,
        ClientUserUpdateBody(is_active=False),
        client=admin_authed,
        session=db_session,
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_current_client(authorization=f"Bearer {token}", session=db_session)
    assert exc_info.value.status_code == 401
