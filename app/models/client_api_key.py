"""
API keys a client's own system authenticates with (docs/ROADMAP.md F4 / LMX Link
T5, docs/ORDER_API.md).

**The gap this closes.** `POST /ingestion/{hub_id}/{client_id}/{source_system}`
describes itself as *"the webhook target you'd register with a client's POS"* - but
it sits behind `OpsUserAuthMiddleware`, so that was only true if you handed the POS
an LMX **ops** login, which can also run dispatch cycles, read the whole fleet and
reach `/admin`. There was no credential in this system that meant "may submit orders
for exactly one client, and nothing else". Now there is.

**Stored as a hash, unlike the webhook signing secret**, and the asymmetry is the
point rather than an inconsistency:

  outbound webhook secret  we must SIGN with it, so we need the plaintext forever.
  inbound API key          we only ever VERIFY it, so we never need the plaintext
                           again - and a database disclosure then leaks no usable
                           credential.

SHA-256 rather than bcrypt/argon2 deliberately. Those exist to make *low-entropy*
secrets expensive to guess; this is 32 random bytes, where brute force is hopeless
regardless, and a slow KDF on every inbound order would be latency spent for
nothing.

`token_prefix` is stored separately so a client can tell their keys apart in the
portal after the full value is gone - which is what makes rotation usable, since
revoking the wrong key is otherwise a coin flip.
"""
import hashlib
import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# A recognisable prefix, on purpose. Secret-scanning services (GitHub's included)
# match on known key shapes, so a distinctive prefix is what lets a key committed
# to a public repo get flagged instead of sitting there. It also makes an
# accidentally-logged credential obvious at a glance rather than looking like a
# request id.
KEY_PREFIX = "lmxk_live_"

# How much of the key is kept in the clear for display. Enough to distinguish two
# keys, far too little to reconstruct one.
DISPLAY_PREFIX_LENGTH = len(KEY_PREFIX) + 6


def mint_api_key() -> tuple[str, str, str]:
    """(full token to show once, sha256 hash to store, display prefix)."""
    token = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return token, hash_api_key(token), token[:DISPLAY_PREFIX_LENGTH]


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ClientApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_api_keys"

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )

    # Indexed and unique: every inbound order is authenticated by hashing the
    # presented key and looking it up here, so this is the hot path.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # **Rotation depends on this.** A client with two keys needs to know which one
    # their system is actually using before revoking the other, and "when was this
    # last used" is the only honest answer. Written on use, best-effort.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
