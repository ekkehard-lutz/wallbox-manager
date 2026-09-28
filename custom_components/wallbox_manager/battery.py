"""Durable, compare-before-restore ownership of one shared home battery reserve."""

import asyncio
import logging
import math
from datetime import UTC, datetime
from fractions import Fraction

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
        self.diagnostics = {}
        self.last_error = None

    async def load(self, *, preserve=False):
        self.record = await self.store.async_load()
        self.recovering = bool(self.record)
        # Restore a prior override even if options changed.
        if not preserve or (self.record and self.record["entity"] != self.reserve):
            await self.update(None)

    def resume(self):
        """Ownership and live charging were verified by the existing coordinator."""
        self.recovering = False
        if self.record:
            self.session = True
            self.status = "preserved"

    def read(self, entity, *, fresh=False):
        state = self.hass.states.get(entity)
        if state is None:
            raise ValueError("entity unavailable")
        if fresh:
            at = getattr(state, "last_reported", state.last_updated)
            if not 0 <= (datetime.now(UTC) - at).total_seconds() <= 90:
                raise ValueError("stale SOC")
        expiry = state.attributes.get("valid_until")
        if expiry and datetime.fromisoformat(expiry) <= datetime.now(UTC):
            raise ValueError("expired reserve/SOC evidence")
        if state.attributes.get("restored"):
            raise ValueError("restored entity is not live evidence")
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
        self.diagnostics["write_count"] = self.diagnostics.get("write_count", 0) + 1
        self.diagnostics["last_written_reserve"] = value
        await self.hass.services.async_call(
            domain, "set_value", {"entity_id": entity, "value": value}, blocking=True
        )

    def desired(self, requested, original):
        soc = self.read(self.soc, fresh=True)
        state = self.hass.states.get(self.reserve)
        step = Fraction(str(state.attributes.get("step", 1)))
        minimum = Fraction(str(state.attributes.get("min", 0)))
        maximum = Fraction(str(state.attributes.get("max", 100)))
        if step <= 0 or minimum > maximum:
            raise ValueError("invalid reserve step/range")
        bounded = min(Fraction(str(requested)), Fraction(str(soc)), maximum)
        value = minimum + ((bounded - minimum) // step) * step
        temporary = max(original, float(value))
        self.diagnostics.update(
            original_reserve=original,
            profile_reserve=requested,
            battery_soc=soc,
            desired_reserve=temporary,
        )
        return temporary

    async def update(self, requested):
        async with self.lock:
            self.diagnostics["profile_reserve"] = requested
            self.diagnostics["restoration_pending"] = requested is None and bool(
                self.record
            )
            if self.recovering:
                requested = None
            try:
                if self.record:
                    entity = self.record["entity"]
                    current = self.read(entity)
                    self.diagnostics["observed_reserve"] = current
                    pending = self.record.get("pending")
                    if pending is not None:
                        self.record = {
                            k: v for k, v in self.record.items() if k != "pending"
                        }
                        if current == pending:
                            self.record["temporary"] = pending
                        await self.store.async_save(self.record)
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
                        if current != self.record["original"]:
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
                if self.record:
                    if self.status not in ("active", "preserved"):
                        return
                    temporary = self.desired(requested, self.record["original"])
                    if temporary == self.record["temporary"]:
                        return
                    self.record = {**self.record, "pending": temporary}
                    await self.store.async_save(self.record)
                    self.status = "write_unconfirmed"
                    await self.write(self.reserve, temporary)
                    if self.read(self.reserve) == temporary:
                        self.record = {
                            k: v for k, v in self.record.items() if k != "pending"
                        }
                        self.record["temporary"] = temporary
                        self.status = "active"
                        if temporary == self.record["original"]:
                            self.record = None
                            self.status = "unchanged"
                        await self.store.async_save(self.record)
                    return
                if (self.session and self.status != "unchanged") or not self.configured:
                    return
                self.session = (
                    True  # One attempt per actual charging episode, including errors.
                )
                original = self.read(self.reserve)
                self.diagnostics["original_reserve"] = original
                if requested <= original:
                    self.status = "unchanged"
                    return
                temporary = self.desired(requested, original)
                self.diagnostics["observed_reserve"] = original
                if temporary <= original:
                    self.status = "unchanged"
                    return
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
                self.diagnostics["reason"] = str(exc)
                if self.last_error != str(exc):
                    _LOGGER.warning("Battery reserve operation failed: %s", exc)
                self.last_error = str(exc)
            finally:
                self.diagnostics["restoration_pending"] = requested is None and bool(
                    self.record
                )
