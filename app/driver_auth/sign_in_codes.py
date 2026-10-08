"""Sign-in codes issued from the ops console - how a driver's phone signs in.

Replaces the four-digit code texted through Twilio. Ops creates every driver
already, so ops is the natural one to hand over the credential: a dispatcher
shows the driver a QR code at the hub, or reads out ten characters, and the app
trades it for the same device-bound session as before. See
app/models/driver_sign_in_code.py for what is stored and why.

**Ten characters from a 31-letter alphabet**, about 50 bits. The texted code
was four digits, which five guesses a code and three codes per five minutes put
within reach in about a day. These are not, even before the per-address limit
below. The alphabet leaves out 0, O, 1, I and L, which get misread when a code
is read aloud or copied off a screen.

**Stored as a keyed hash.** HMAC-SHA256 under the driver session secret, with a
label so the same secret signing tokens cannot be confused with this use. A
plain hash of a short code could be reversed by trying every code; a keyed one
cannot without the secret.

**Redeemed atomically.** One UPDATE claims the code only if it is unused,
unsuperseded and unexpired, so two phones racing the same code cannot both
sign in.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.driver_sign_in_code import DriverSignInCode
from app.redis_client import get_client, timed_operation

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 10

# Long enough to issue a code at onboarding and have the driver install the app
# that evening; short enough that a code written on a sticky note stops working
# the next day. Issuing a new one is one click.
CODE_TTL = timedelta(hours=24)

# What a QR code carries: a marker and the code, never a server address. A QR
# that set the server could point a driver's phone at somebody else's.
QR_PREFIX = "LMX-SIGNIN:"

# Failed attempts per address. A wrong code is cheap to try and the code space is
# large, so this is about noise and scripted sweeps rather than the odds of a hit.
# Only failures count: a hub's drivers often share one network address, and a
# morning of sign-ins at the depot must not lock them out.
MAX_FAILED_ATTEMPTS_PER_IP = 20
ATTEMPT_WINDOW_SECONDS = 15 * 60


@dataclass(frozen=True)
class IssuedCode:
    code: str
    expires_at: datetime

    @property
    def display(self) -> str:
        """The code in groups of five, which is how a person reads it out."""
        return f"{self.code[:5]}-{self.code[5:]}"

    @property
    def qr_payload(self) -> str:
        return f"{QR_PREFIX}{self.code}"


class SignInAttemptsExceeded(Exception):
    pass


def normalize(raw: str) -> str:
    """What the driver typed or scanned, as the code it stands for.

    Accepts the QR payload, the grouped display form, lower case, and spaces.
    Anything left that is not in the alphabet makes it not a code.
    """
    text = raw.strip()
    if text.upper().startswith(QR_PREFIX):
        text = text[len(QR_PREFIX):]
    return "".join(ch for ch in text.upper() if ch not in " -")


def code_hmac(code: str) -> str:
    key = settings.driver_jwt_secret.encode()
    return hmac.new(key, f"driver-sign-in-code:{code}".encode(), hashlib.sha256).hexdigest()


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


async def issue_code(
    session: AsyncSession, driver_id: uuid.UUID, issued_by_ops_user_id: uuid.UUID | None
) -> IssuedCode:
    """A fresh code for this driver. Any older code nobody used stops working.

    Flushes, does not commit: the caller owns the transaction, so onboarding can
    create the driver and the first code together.
    """
    now = datetime.now(timezone.utc)
    await retire_open_codes(session, driver_id, now)
    code = _new_code()
    issued = IssuedCode(code=code, expires_at=now + CODE_TTL)
    session.add(
        DriverSignInCode(
            driver_id=driver_id,
            code_hmac=code_hmac(code),
            issued_by_ops_user_id=issued_by_ops_user_id,
            expires_at=issued.expires_at,
        )
    )
    await session.flush()
    return issued


async def retire_open_codes(session: AsyncSession, driver_id: uuid.UUID, now: datetime) -> None:
    """Every code for this driver that nobody has used stops working."""
    await session.execute(
        update(DriverSignInCode)
        .where(
            DriverSignInCode.driver_id == driver_id,
            DriverSignInCode.redeemed_at.is_(None),
            DriverSignInCode.superseded_at.is_(None),
        )
        .values(superseded_at=now)
    )


async def redeem_code(session: AsyncSession, raw: str, device_id: str) -> uuid.UUID | None:
    """The driver this code signs in, claiming the code for this device.

    None for anything that is not a live code: unknown, used, superseded or
    expired all look the same to the caller, so a wrong guess learns nothing.
    Flushes, does not commit: the caller commits the claim together with the
    device it signs in.
    """
    code = normalize(raw)
    if len(code) != CODE_LENGTH or any(ch not in ALPHABET for ch in code):
        return None
    now = datetime.now(timezone.utc)
    claimed = await session.execute(
        update(DriverSignInCode)
        .where(
            DriverSignInCode.code_hmac == code_hmac(code),
            DriverSignInCode.redeemed_at.is_(None),
            DriverSignInCode.superseded_at.is_(None),
            DriverSignInCode.expires_at > now,
        )
        .values(redeemed_at=now, redeemed_device_id=device_id)
        .returning(DriverSignInCode.driver_id)
    )
    return claimed.scalar_one_or_none()


def _attempts_key(caller_ip: str) -> str:
    return f"driver_auth:sign_in_attempts:{caller_ip}"


async def charge_attempt(caller_ip: str) -> None:
    """Count one sign-in attempt from this address; refuse past the cap.

    Charged before the code is checked, so an address over the cap is refused
    without learning whether its code was good. A successful sign-in then hands
    the charge back (`refund_attempt`), so only failures accumulate.
    """
    key = _attempts_key(caller_ip)
    async with timed_operation("driver_auth.sign_in_attempts"):
        pipe = get_client().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, ATTEMPT_WINDOW_SECONDS, nx=True)
        count, _ = await pipe.execute()
    if count > MAX_FAILED_ATTEMPTS_PER_IP:
        raise SignInAttemptsExceeded(
            f"Too many sign-in attempts - try again in {ATTEMPT_WINDOW_SECONDS // 60} minutes"
        )


async def refund_attempt(caller_ip: str) -> None:
    """A sign-in that worked doesn't count against its address."""
    async with timed_operation("driver_auth.sign_in_attempts"):
        await get_client().decr(_attempts_key(caller_ip))


async def code_history(session: AsyncSession, driver_id: uuid.UUID) -> list[DriverSignInCode]:
    """Every code issued for this driver, newest first. Never the code itself."""
    result = await session.execute(
        select(DriverSignInCode)
        .where(DriverSignInCode.driver_id == driver_id)
        .order_by(DriverSignInCode.created_at.desc())
    )
    return list(result.scalars().all())
