"""What the client portal's responses rely on ingestion to have done.

Portal orders are LMX-owned, so ingestion classifies every one before a response
is built. The columns are nullable only because EXTERNAL orders exist, and a
portal order that arrived unclassified should fail as exactly that - not as a
validation error from the response model, which names neither the order nor
the invariant.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.api.client_routes import _classified
from app.models.order import Order, SLATier


def test_a_classified_order_reports_its_tier_and_collect_by():
    due = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    assert _classified(Order(sla_tier=SLATier.T2, hold_deadline=due)) == ("T2", due)


@pytest.mark.parametrize("tier, due", [(None, datetime(2026, 9, 30, tzinfo=timezone.utc)), (SLATier.T1, None)])
def test_an_unclassified_order_fails_as_one(tier, due):
    order = Order(id=uuid.uuid4(), sla_tier=tier, hold_deadline=due)
    with pytest.raises(RuntimeError, match="unclassified"):
        _classified(order)
