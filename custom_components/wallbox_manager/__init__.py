"""Wallbox Manager lifecycle, one independent OCPP entry per station."""

import asyncio
from dataclasses import dataclass, field
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
    grid: object
    battery: object
    pv: object
    _shutdown: asyncio.Task[None] | None = field(init=False, default=None, repr=False)

    async def close(self):
        if self._shutdown is None:
            self._shutdown = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._shutdown)
        except asyncio.CancelledError as cancelled:
            # The lifecycle caller may go away while a durable write is in
            # progress. Keep the old runtime owned until cleanup settles.
            try:
                await asyncio.shield(self._shutdown)
            except Exception as error:
                raise cancelled from error
            raise

    async def _close(self):
        errors = []
        # Fence dispatch and release the socket before the final journal save.
        for close in (
            self.grid.close,
            self.battery.close,
            self.server.stop,
            self.pv.close,
        ):
            try:
                await close()
            except (Exception, asyncio.CancelledError) as error:
                errors.append(error)
        if self.state.session:
            try:
                await self.session_store.async_save(document(self.state))
            except (Exception, asyncio.CancelledError) as error:
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Wallbox Manager shutdown failed", errors)


async def async_setup(hass, config):
    from .frontend import async_setup_assets

    await async_setup_assets(hass)
    return True


async def async_setup_entry(hass, entry):
    from . import session
    from .frontend import async_setup_assets

    profiles_store = Store(
        hass, 1, f"wallbox_manager.{entry.entry_id}.profiles", atomic_writes=True
    )
    profiles = await profiles_store.async_load() or {}
    binding = migrate(hass, entry, profiles)
    state = State(binding)
    settings = Settings(hass, entry, binding, profiles_store, profiles)
    await settings.initialize_grid()
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
    from .grid import Grid
    from .grid_battery import GridBattery
    from .pv import PV

    settings.state = state
    grid = server.grid = Grid(server, settings)
    if state.session:
        grid.known_transaction = state.session.get("external_transaction_id")
    battery = GridBattery(hass, entry, grid)
    pv = grid.pv = PV(hass, entry, grid)
    runtime = entry.runtime_data = EntryRuntime(
        state, settings, server, session_store, grid, battery, pv
    )
    forwarding = False
    try:
        await pv.start()
        await battery.start()
        try:
            await server.start()
        except OSError as exc:
            raise ConfigEntryNotReady("Cannot bind OCPP listener") from exc
        forwarding = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await async_setup_assets(hass)
    except BaseException as setup_error:
        try:
            try:
                await runtime.close()
            finally:
                if forwarding:
                    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        except (Exception, asyncio.CancelledError) as cleanup_error:
            raise setup_error from cleanup_error
        raise

    async def options_updated(hass, updated_entry):
        battery.changed()
        pv.options_updated()
        # Options update held preferences without reconnecting the transport.
        for key in REGULATION:
            if key in updated_entry.options:
                value = updated_entry.options[key]
                if settings.values.get(key) != value:
                    settings.values[key] = value
                    for listener in tuple(settings.listeners.get(key, ())):
                        listener()

    entry.async_on_unload(entry.add_update_listener(options_updated))

    async def stop(event):
        await runtime.close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.close()
    return True


async def async_migrate_entry(hass, entry):
    if entry.version in (1, 2):
        hass.config_entries.async_update_entry(
            entry,
            version=3,
            data={"host": DEFAULT_HOST, "port": DEFAULT_PORT, **entry.data},
        )
    return entry.version == 3
