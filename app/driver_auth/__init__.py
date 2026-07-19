"""
Real per-driver authentication for the driver app (phone + OTP -> JWT
session), distinct from app/security.py's SharedSecretAuthMiddleware, which
is a single shared secret for everything else and explicitly not real
per-user auth (see its docstring). See docs/NEXT_STEPS.md item 12.
"""
