"""Minimal passive Wallbox Manager lifecycle, one independent entry per station."""

from dataclasses import dataclass
from importlib import import_module

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import DEFAULT_HOST, DEFAULT_PORT, PLATFORMS
from .entity import device_info
from .migration import migrate
from .session import document
from .settings import REGULATION, Settings
from .store import State


@dataclass
class EntryRuntime:
    state: State
    settings: Settings
    server: object
    session_store: Store


async def async_setup(hass, config):
    from .frontend import async_setup_assets

    await async_setup_assets(hass)
    return True


async def async_setup_entry(hass, entry):
    from . import session
    from .frontend import async_setup_assets

    profiles_store = Store(hass, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    profiles = await profiles_store.async_load() or {}
    binding = migrate(hass, entry, profiles)
    state = State(binding)
    settings = Settings(hass, entry, binding, profiles_store, profiles)
    session_store = await session.load(hass, entry.entry_id, state)
    transport = await hass.async_add_executor_job(import_module, ".ocpp", __package__)

    def update_device():
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, **device_info(entry, state)
        )

    update_device()
    server = transport.Listener(
        state,
        entry.data.get("host", DEFAULT_HOST),
        entry.data.get("port", DEFAULT_PORT),
        update_device,
        options=lambda: entry.options,
    )
    entry.runtime_data = EntryRuntime(state, settings, server, session_store)
    try:
        await server.start()
    except OSError as exc:
        raise ConfigEntryNotReady("Cannot bind OCPP listener") from exc
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await async_setup_assets(hass)
    except BaseException:
        await server.stop()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise

    async def options_updated(hass, updated_entry):
        # Options are passive preferences; no reload, reconnect or control timer.
        for key in REGULATION:
            if key in updated_entry.options:
                value = updated_entry.options[key]
                if settings.values.get(key) != value:
                    settings.values[key] = value
                    for listener in tuple(settings.listeners.get(key, ())):
                        listener()

    entry.async_on_unload(entry.add_update_listener(options_updated))

    async def stop(event):
        await server.stop()
        if state.session:
            await session_store.async_save(session.document(state))

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.server.stop()
    if entry.runtime_data.state.session:
        await entry.runtime_data.session_store.async_save(
            document(entry.runtime_data.state)
        )
    return True


async def async_migrate_entry(hass, entry):
    if entry.version in (1, 2):
        hass.config_entries.async_update_entry(
            entry,
            version=3,
            data={"host": DEFAULT_HOST, "port": DEFAULT_PORT, **entry.data},
        )
    return entry.version == 3
