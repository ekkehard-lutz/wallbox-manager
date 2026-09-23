"""Wallbox Manager entry lifecycle; imports leave the pure core dependency-free.

Listener setup/unload pattern adapted from lbbrhzn/ocpp __init__.py at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See THIRD_PARTY_NOTICES.md. Runtime is entry-owned, with observation and manual
intent platforms. Entities use the generic control boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .control.runtime import ControlRuntime
    from .protocols.ocpp.common.transport import CentralSystem
    from .runtime import Runtime
    from .session_storage import SessionStorage

from .const import DEFAULT_HOST, DEFAULT_PORT

PLATFORMS = ("binary_sensor", "sensor", "switch", "number", "select")


@dataclass
class EntryRuntime:
    state: Runtime
    server: CentralSystem
    sessions: SessionStorage
    control: ControlRuntime


type WallboxManagerConfigEntry = ConfigEntry[EntryRuntime]


async def async_setup_entry(
    hass: HomeAssistant, entry: WallboxManagerConfigEntry
) -> bool:
    """Start listening immediately; an offline charger is not a setup failure."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.exceptions import ConfigEntryNotReady

    # Library/schema module imports may read files; keep them off the HA loop.
    transport = await hass.async_add_executor_job(
        import_module, ".protocols.ocpp.common.transport", __package__
    )
    from .runtime import Runtime

    state = Runtime()
    server = transport.CentralSystem(
        state,
        entry.data.get("host", DEFAULT_HOST),
        entry.data.get("port", DEFAULT_PORT),
    )
    from .session_storage import SessionStorage

    storage = SessionStorage(hass, entry.entry_id, state.sessions)
    await storage.load()
    try:
        await server.start()
    except OSError as exc:
        await storage.close()
        raise ConfigEntryNotReady("Cannot bind OCPP listener") from exc
    from .control.reference import ConfiguredReference
    from .protocols.ocpp.v21.control_runtime import create_control_runtime

    source = ConfiguredReference(entry.options)
    control = create_control_runtime(state, server, source)
    entry.runtime_data = EntryRuntime(state, server, storage, control)

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        control.close()
        await server.stop()
        await storage.close()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise

    async def shutdown(event):
        control.close()
        await server.stop()
        await storage.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WallboxManagerConfigEntry
) -> bool:
    """Close the listener and join owned sessions before completing unload."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.runtime_data.control.close()
    await entry.runtime_data.server.stop()
    await entry.runtime_data.sessions.close()
    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: WallboxManagerConfigEntry
) -> bool:
    """Add endpoint defaults to the earlier empty scaffold entry."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            data={"host": DEFAULT_HOST, "port": DEFAULT_PORT, **entry.data},
            version=2,
        )
    return entry.version == 2
