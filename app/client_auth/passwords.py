"""
Password hashing for the client portal (Phase 8) - Client.portal_password_hash.

bcrypt, not a hand-rolled scheme: it's a well-reviewed, purpose-built
password hash (salted automatically, tunable work factor) rather than a
generic fast hash like sha256 that would make offline brute-forcing cheap.
This is the only place in the codebase that ever touches a plaintext
client password - app/api/admin_routes.py (onboarding) and
app/api/client_routes.py (login) both go through these two functions
rather than calling bcrypt directly.
"""
from __future__ import annotations

import bcrypt


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash - never crash a login attempt over it, just
        # treat it as a non-match.
        return False
