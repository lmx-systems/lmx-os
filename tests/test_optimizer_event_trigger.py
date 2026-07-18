from unittest.mock import AsyncMock, patch

import pytest

from app.optimizer.event_trigger import _run_cycle
from app.schemas.optimizer import OptimizationResult


@pytest.mark.asyncio
async def test_run_cycle_invokes_dispatch_optimizer_service_for_the_hub():
    fake_result = OptimizationResult(
        hub_id="hub-1",
        assignments=[],
        unassigned_stop_ids=[],
        engine="stub_nearest_neighbor",
        duration_seconds=0.01,
        over_budget=False,
    )
    with patch("app.optimizer.event_trigger.DispatchOptimizerService") as mock_service_cls:
        mock_service_cls.return_value.run_cycle = AsyncMock(return_value=fake_result)

        await _run_cycle("hub-1")

        mock_service_cls.return_value.run_cycle.assert_awaited_once_with("hub-1")
