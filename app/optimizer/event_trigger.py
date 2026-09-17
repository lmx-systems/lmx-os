"""
Binds the generic HubEventBus (app/events/bus.py) to the Dispatch
Optimizer, so a cycle runs off real events - order held, driver status
change - instead of only the manual POST /optimizer/{hub_id}/run-cycle
endpoint (docs/ARCHITECTURE.md, "Recommended next steps" item 6). Event
producers (app/api/routes.py, app/ingestion/router.py) import
`dispatch_event_bus` from here and call `.publish(hub_id, event_type)`
right after the state change that makes a new cycle worth running.

All three of the design doc's triggers now have producers. "Stop completed"
was the last one outstanding - this docstring said component 7 (the driver
app) was not built, which stopped being true when `DRV-1` shipped, and
`app/api/driver_routes.py`'s complete-stop endpoint publishes it. Whether
the cycle it triggers actually beats the dispatcher is `DEC-4`, measured in
`app/reporting/insertion.py`.
"""
from __future__ import annotations

import structlog

from app.events.bus import HubEventBus
from app.optimizer.service import DispatchOptimizerService

logger = structlog.get_logger(__name__)


async def _run_cycle(hub_id: str) -> None:
    result = await DispatchOptimizerService().run_cycle(hub_id)
    logger.info(
        "dispatch_event_triggered_cycle",
        hub_id=hub_id,
        assigned_count=len(result.assignments),
        unassigned_count=len(result.unassigned_stop_ids),
        engine=result.engine,
        over_budget=result.over_budget,
    )


dispatch_event_bus = HubEventBus(_run_cycle)
