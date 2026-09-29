"""Durable, compare-before-restore ownership of one shared home battery reserve."""

import asyncio
import logging
import math
from datetime import UTC, datetime
from fractions import Fraction

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .diagnostics import diagnostic_event

CONFIRMATION_TIMEOUT = 10

_LOGGER = logging.getLogger(__name__)


class BatteryReserve:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
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
        self._ignored_confirmation = None
        self._last_transition = None

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
            try:
                confirmed = self.read(self.record["entity"]) == self.record.get(
                    "pending", self.record["temporary"]
                )
            except ValueError, TypeError:
                confirmed = False
            self.status = "preserved" if confirmed else "write_unconfirmed"

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

    def transition(self, status, **fields):
        self.status = status
        transition = (status, fields)
        if transition != self._last_transition:
            diagnostic_event(self.entry, "battery_reserve", stage=status, **fields)
            self._last_transition = transition
        if status in ("active", "restored", "unchanged"):
            self.last_error = None
            self.diagnostics.pop("reason", None)

    async def write_confirmed(self, entity, value):
        """Subscribe before dispatch; serialize targets and bound the async wait.

        The durable journal remains the source of truth after timeout/reload.
        Matching late evidence is reconciled by update, without reissuing writes.
        """
        changed = asyncio.Event()

        def confirmed():
            try:
                return self.read(entity) == value
            except ValueError, TypeError:
                return False

        @callback
        def observed(event):
            if confirmed():
                changed.set()

        remove = async_track_state_change_event(self.hass, [entity], observed)
        try:
            self.transition("confirmation_pending", entity=entity, target=value)
            diagnostic_event(
                self.entry,
                "battery_reserve",
                stage="write_requested",
                entity=entity,
                target=value,
            )
            async with asyncio.timeout(CONFIRMATION_TIMEOUT):
                await self.write(entity, value)
                while not confirmed():
                    changed.clear()
                    await changed.wait()
            diagnostic_event(
                self.entry,
                "battery_reserve",
                stage="confirmed",
                entity=entity,
                target=value,
            )
            return True
        except TimeoutError:
            if confirmed():
                diagnostic_event(
                    self.entry,
                    "battery_reserve",
                    stage="confirmed",
                    entity=entity,
                    target=value,
                    service_timeout=True,
                )
                return True
            self.transition("write_unconfirmed", entity=entity, target=value)
            self.diagnostics["reason"] = "confirmation_timeout"
            diagnostic_event(
                self.entry,
                "battery_reserve",
                stage="confirmation_timeout",
                entity=entity,
                target=value,
            )
            return False
        finally:
            remove()

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
                    original = self.record["original"]
                    current = self.read(entity)
                    self.diagnostics["observed_reserve"] = current
                    if (
                        requested is None
                        and self.failed_restore
                        and current != original
                        and current
                        in [
                            self.record["temporary"],
                            *self.record.get("superseded", []),
                        ]
                    ):
                        return
                    pending = self.record.get("pending")
                    if pending is not None:
                        if current == pending:
                            self.record["temporary"] = pending
                            self.record.pop("pending")
                            self.record.pop("pending_request", None)
                            self.transition("active", target=pending, late=True)
                            await self.store.async_save(self.record)
                        elif current not in (
                            self.record["temporary"],
                            *self.record.get("superseded", []),
                        ):
                            self.transition("external_change", observed=current)
                            self.record = None
                            self.failed_restore = False
                            self.recovering = False
                            await self.store.async_save(None)
                            if requested is None:
                                self.session = False
                            return
                        elif requested is not None and (
                            requested == self.record.get("pending_request", requested)
                            or current != self.record["temporary"]
                        ):
                            ignored = (pending, current)
                            if self._ignored_confirmation != ignored:
                                diagnostic_event(
                                    self.entry,
                                    "battery_reserve",
                                    stage="nonmatching_confirmation_ignored",
                                    target=pending,
                                    observed=current,
                                )
                                self._ignored_confirmation = ignored
                            # Retain the target across timeout and later evidence.
                            # Never acknowledge an older target for a newer write.
                            return
                        else:
                            diagnostic_event(
                                self.entry,
                                "battery_reserve",
                                stage="superseded",
                                target=pending,
                                requested=requested,
                            )
                            self.record["superseded"] = list(
                                dict.fromkeys(
                                    [*self.record.get("superseded", []), pending]
                                )
                            )
                            self.record.pop("pending")
                            self.record.pop("pending_request", None)
                            await self.store.async_save(self.record)
                            if requested is not None:
                                self.transition("active")
                    if (
                        requested is not None
                        and current == self.record["temporary"]
                        and self.status in ("error", "write_unconfirmed")
                    ):
                        self.transition("active", target=current, late=True)
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
                        if requested is None and current == original:
                            self.transition("restored", late=True)
                    elif requested is None:
                        if self.failed_restore:
                            return
                        self.failed_restore = True
                        if current != self.record["original"]:
                            if not await self.write_confirmed(
                                entity, self.record["original"]
                            ):
                                return
                        self.record = None
                        self.failed_restore = False
                        self.recovering = False
                        await self.store.async_save(None)
                        self.transition("restored")
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
                    self.record = {
                        **self.record,
                        "pending": temporary,
                        "pending_request": requested,
                    }
                    await self.store.async_save(self.record)
                    if await self.write_confirmed(self.reserve, temporary):
                        self.record = {
                            k: v
                            for k, v in self.record.items()
                            if k not in ("pending", "pending_request")
                        }
                        self.record["temporary"] = temporary
                        self.transition("active", target=temporary)
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
                if await self.write_confirmed(self.reserve, temporary):
                    self.transition("active", target=temporary)
            except Exception as exc:
                self.transition("error", reason=str(exc))
                self.diagnostics["reason"] = str(exc)
                if self.last_error != str(exc):
                    _LOGGER.warning("Battery reserve operation failed: %s", exc)
                self.last_error = str(exc)
            finally:
                self.diagnostics["restoration_pending"] = requested is None and bool(
                    self.record
                )
