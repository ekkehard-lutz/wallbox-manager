"""Durable, compare-before-restore ownership of one shared home battery reserve."""

import asyncio
import logging
import math

from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)


class BatteryReserve:
    def __init__(self, hass, entry):
        self.hass = hass
        self.reserve = entry.options.get("min_soc_speicher")
        self.soc = entry.options.get("soc_speicher_aktuell")
        self.configured = bool(self.reserve and self.soc)
        self.store = Store(
            hass, 1, f"wallbox_manager.{entry.entry_id}.battery", atomic_writes=True
        )
        self.record = None
        self.status = "idle"
        self.lock = asyncio.Lock()
        self.session = False
        self.failed_restore = False
        self.recovering = False

    async def load(self):
        self.record = await self.store.async_load()
        self.recovering = bool(self.record)
        # Restore a prior override even if options changed.
        await self.update(None)

    def read(self, entity):
        state = self.hass.states.get(entity)
        if state is None:
            raise ValueError("entity unavailable")
        value = float(state.state)
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("invalid SOC")
        return value

    async def write(self, entity, value):
        domain = entity.split(".", 1)[0]
        state = self.hass.states.get(entity)
        if (
            domain not in ("number", "input_number")
            or not self.hass.services.has_service(domain, "set_value")
            or state is None
        ):
            raise ValueError("reserve entity is not writable")
        if (
            not float(state.attributes.get("min", 0))
            <= value
            <= float(state.attributes.get("max", 100))
        ):
            raise ValueError("reserve outside entity range")
        await self.hass.services.async_call(
            domain, "set_value", {"entity_id": entity, "value": value}, blocking=True
        )

    async def update(self, requested):
        async with self.lock:
            if self.recovering:
                requested = None
            try:
                if self.record:
                    entity = self.record["entity"]
                    current = self.read(entity)
                    if current == self.record["original"] and requested is not None:
                        return  # Unconfirmed or externally reverted: never reassert.
                    if current != self.record["temporary"]:
                        # External changes win for the rest of this session.
                        if current != self.record["original"]:
                            self.status = "external_change"
                        self.record = None
                        self.failed_restore = False
                        self.recovering = False
                        await self.store.async_save(None)
                    elif requested is None:
                        if self.failed_restore:
                            return
                        self.failed_restore = True
                        await self.write(entity, self.record["original"])
                        if self.read(entity) != self.record["original"]:
                            raise ValueError("restoration unconfirmed")
                        self.record = None
                        self.failed_restore = False
                        self.recovering = False
                        await self.store.async_save(None)
                        self.status = "restored"
                        self.failed_restore = False
                if requested is None:
                    self.session = False
                    return
                if self.record or self.session or not self.configured:
                    return
                self.session = (
                    True  # One attempt per actual charging episode, including errors.
                )
                original, soc = self.read(self.reserve), self.read(self.soc)
                if requested <= original:
                    self.status = "unchanged"
                    return
                temporary = min(requested, soc)
                # Persist before dispatch: a crash after the write remains recoverable.
                self.record = {
                    "entity": self.reserve,
                    "original": original,
                    "temporary": temporary,
                }
                await self.store.async_save(self.record)
                await self.write(self.reserve, temporary)
                self.status = (
                    "active"
                    if self.read(self.reserve) == temporary
                    else "write_unconfirmed"
                )
            except Exception as exc:
                self.status = "error"
                _LOGGER.warning("Battery reserve operation failed: %s", exc)
