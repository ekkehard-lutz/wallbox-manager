"""Load the current PV journal and distinguish missing from damaged history."""

import os
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

MISSING_JOURNAL = object()


async def load_journal(hass, store):
    """Distinguish Store's ambiguous None from absence of on-disk history."""
    try:
        data = await store.async_load()
        if data is not None:
            return data
        if not await hass.async_add_executor_job(journal_exists, Path(store.path)):
            return MISSING_JOURNAL
    except (
        HomeAssistantError,
        OSError,
        TypeError,
        KeyError,
        ValueError,
        NotImplementedError,
    ):
        # Invalid wrapper, unsupported version or unreadable storage.
        pass
    return None


def journal_exists(path):
    try:
        with os.scandir(path.parent) as entries:
            return any(
                entry.name == path.name
                or entry.name.startswith(f"{path.name}.corrupt.")
                for entry in entries
            )
    except FileNotFoundError:
        return False
