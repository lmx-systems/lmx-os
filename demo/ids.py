"""
Fixed IDs shared by seed_demo_data.py and send_demo_order.py so the two
scripts always agree on which Hub/Client/Shop/Driver they're talking
about, and so re-running the demo doesn't create duplicate rows.

Deterministic (uuid5, not uuid4) on purpose: the same "lmx-demo-*" name
always produces the same UUID, so these constants never drift even if
someone regenerates this file from scratch.
"""
import uuid

_NAMESPACE = uuid.UUID("d6f6a7b0-6b7a-4c2b-9f3a-2f5c6a1e9d10")  # arbitrary, fixed

HUB_ID = uuid.uuid5(_NAMESPACE, "lmx-demo-hub")
CLIENT_ID = uuid.uuid5(_NAMESPACE, "lmx-demo-client")
SHOP_ID = uuid.uuid5(_NAMESPACE, "lmx-demo-shop")
DRIVER_ID = uuid.uuid5(_NAMESPACE, "lmx-demo-driver")

# Matches "ShipToNum" in demo/epicor_sample_order.json - this is how a real
# Epicor payload gets matched back to a shop_profiles row (see
# app/ingestion/service.py's shop lookup by external_ref).
SHOP_EXTERNAL_REF = "SHOP-0042"

SOURCE_SYSTEM = "epicor"
