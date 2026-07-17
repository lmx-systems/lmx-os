"""Maps source_system -> adapter instance. Add MAM/ASA here as they ship."""
from app.ingestion.adapters.base import BaseIngestionAdapter
from app.ingestion.adapters.epicor import EpicorAdapter
from app.ingestion.adapters.generic import GenericFlatFileAdapter

ADAPTERS: dict[str, BaseIngestionAdapter] = {
    "epicor": EpicorAdapter(),
    "flat_file": GenericFlatFileAdapter(),
}


def get_adapter(source_system: str) -> BaseIngestionAdapter:
    try:
        return ADAPTERS[source_system]
    except KeyError as exc:
        raise ValueError(
            f"No ingestion adapter registered for source_system={source_system!r}. "
            f"Available: {list(ADAPTERS)}"
        ) from exc
