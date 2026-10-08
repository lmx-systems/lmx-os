"""
Real per-driver authentication for the driver app (a sign-in code from the
ops console -> device-bound JWT session), a separate auth domain from app/ops_auth/'s real per-account
ops-dashboard auth and app/client_auth/'s client-portal auth - a token
issued for one must never be valid as another. See docs/NEXT_STEPS.md
item 12.
"""
