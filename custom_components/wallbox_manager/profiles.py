"""Event driven Grid profile; primitive controls remain the execution boundary."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from fractions import Fraction

from homeassistant.core import callback
from homeassistant.helpers.storage import Store

from .control.commands import CommandStatus
from .core.authority import ControlAuthority
from .core.telemetry import Channel, Quantity
from .core.values import scalar

_LOGGER = logging.getLogger(__name__)


def target_key(target):
    return json.dumps([target.station.value, target.evse.value, target.value])


class GridProfiles:
    """Own profile settings and bounded detection tasks, never acquire authority."""

    def __init__(self, hass, entry, control, battery):
        self.hass, self.control, self.battery = hass, control, battery
        self.store = Store(hass, 1, f"wallbox_manager.{entry.entry_id}.profiles")
        self.settings = {}
        self.tasks = {}
        self.epochs = {}
        self.status = {}
        self.closed = False
        self.wait = asyncio.sleep
        self.unsubscribe = control.runtime.subscribe(self.changed)
        self.session_unsubscribe = control.runtime.sessions.subscribe(
            self.sessions_changed
        )
        self.battery_task = None
        self.battery_dirty = False
        self.suppressed = set()
        self.unsubscribe_battery = hass.bus.async_listen(
            "state_changed", self.battery_changed
        )

    async def load(self):
        stored = await self.store.async_load() or {}
        for key, value in stored.items():
            try:
                if value.get("profile") != "NETZ":
                    continue
                power, reserve = scalar(value["power_kw"]), scalar(value["min_soc"])
                if power <= 100 and reserve <= 100:
                    self.settings[key] = value
            except ValueError, TypeError, KeyError:
                continue

    def setting(self, target):
        return self.settings.setdefault(
            target_key(target), {"profile": "NETZ", "power_kw": 11, "min_soc": 20}
        )

    def attributes(self, target):
        return {
            "profile_status": self.status.get(target, "idle"),
            "battery_configured": self.battery.configured,
            "battery_status": self.battery.status,
            "actual_charging": self.active(target),
        }

    @callback
    def battery_changed(self, event):
        entity = event.data.get("entity_id")
        recovery = self.battery.record.get("entity") if self.battery.record else None
        if entity not in (self.battery.reserve, self.battery.soc, recovery):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            new
            and new.state not in ("unknown", "unavailable")
            and (old is None or old.state in ("unknown", "unavailable"))
        ):
            self.battery.failed_restore = False
        self.sessions_changed()

    def active(self, target):
        state = self.control.runtime.get(target.station)
        if not state or not state.connected:
            return False
        if not any(
            s.active and s.scope == target for s in self.control.runtime.sessions.latest
        ):
            return False
        from .protocols.ocpp.v21.control_runtime import actively_charging

        return actively_charging(state, target)

    def invalidate(self, target):
        self.epochs[target] = self.epochs.get(target, 0) + 1
        task = self.tasks.pop(target, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        self.control.intent(target).generation += 1
        self.status[target] = "idle"

    async def select(self, target, profile):
        if profile != "NETZ":
            raise ValueError("unsupported profile")
        # Reselecting is an explicit safe stop too; future profiles use this boundary.
        self.invalidate(target)
        self.suppressed.add(target)
        epoch = self.epochs[target]
        await self.control.request_enabled(target, False)
        if self.epochs[target] != epoch:
            return
        self.setting(target)["profile"] = profile
        await self.save()
        await self.reconcile_battery(exclude=target)
        self.control.publish(target)

    async def set_value(self, target, field, value):
        value = scalar(value)
        if field not in ("power_kw", "min_soc") or value > 100:
            raise ValueError("invalid profile setting")
        self.setting(target)[field] = float(value)
        if field == "power_kw":
            self.invalidate(target)
            self.control._edit(target, {"target_w": value * 1000})
        epoch = self.epochs.get(target, 0)
        await self.save()
        if (
            field == "power_kw"
            and self.epochs.get(target, 0) == epoch
            and target not in self.suppressed
            and self.control.runtime.enabled(target) is True
        ):
            await self.start(target)
        self.control.publish(target)

    async def permission(self, target, enabled):
        self.invalidate(target)
        epoch = self.epochs[target]
        if enabled:
            self.suppressed.discard(target)
        else:
            self.suppressed.add(target)
        self.control.intent(target).profile_modes = None
        self.control._edit(
            target, {"target_w": Fraction(str(self.setting(target)["power_kw"])) * 1000}
        )
        result = await self.control.request_enabled(target, enabled)
        if self.epochs[target] != epoch:
            return result
        if enabled and result and result.status == CommandStatus.APPLIED:
            self.launch(target)
        if not enabled:
            await self.reconcile_battery(exclude=target)
        return result

    async def start(self, target):
        epoch = self.epochs.get(target, 0)
        self.control.intent(target).profile_modes = None
        await self.control.apply_stored(target)
        if self.epochs.get(target, 0) == epoch:
            self.launch(target)

    def launch(self, target):
        epoch = self.epochs.get(target, 0)
        intent = self.control.intent(target)
        if (
            not intent.solver_result
            or not intent.command_result
            or intent.command_result.status != CommandStatus.APPLIED
        ):
            return
        self.tasks[target] = self.hass.async_create_background_task(
            self.sequence(target, epoch), "Grid observation"
        )

    def valid(self, target, epoch):
        return (
            not self.closed
            and self.epochs.get(target, 0) == epoch
            and self.control.runtime.enabled(target) is True
            and self.control.runtime.authority(target.station)
            == ControlAuthority.REMOTE
        )

    async def sequence(self, target, epoch):
        tried = set()
        try:
            while self.valid(target, epoch):
                intent = self.control.intent(target)
                self.status[target] = (
                    "phase_lockout" if intent.phase_retry else "observing"
                )
                self.control.publish(target)
                applied_at = datetime.now(UTC)
                generation = intent.generation
                await self.wait(60)
                if not self.valid(target, epoch) or intent.generation != generation:
                    return
                if intent.phase_retry:
                    await self.control.apply_stored(target)
                    if (
                        not intent.command_result
                        or intent.command_result.status != CommandStatus.APPLIED
                    ):
                        return
                    continue
                point = intent.solver_result.point if intent.solver_result else None
                if not point or not point.charging:
                    break
                count = point.mode.count
                tried.add(count)
                state = self.control.runtime.get(target.station)

                def current(phase, state=state, applied_at=applied_at):
                    sample = state.observation(
                        Channel(target, Quantity(f"current_l{phase}"))
                    )
                    return (
                        sample.value
                        if (
                            sample
                            and sample.fresh(datetime.now(UTC))
                            and applied_at <= sample.observed_at <= datetime.now(UTC)
                        )
                        else None
                    )

                if count > 1:
                    values = [current(p) for p in range(2, count + 1)]
                    if any(v is None or v >= 3 for v in values):
                        break
                    intent.profile_modes = (1,)
                else:
                    measured = current(1)
                    if (
                        measured is None
                        or abs(point.current_a - measured) <= 2
                        or any(n > 1 for n in tried)
                    ):
                        break
                    intent.profile_modes = (2, 3)
                    _, candidate, blocked = self.control.resolve(target)
                    if (
                        blocked
                        or not candidate
                        or not candidate.point
                        or abs(
                            candidate.point.offered_power_w - intent.request.target_w
                        )
                        >= abs(
                            measured * point.phase_voltages_v[0]
                            - intent.request.target_w
                        )
                    ):
                        intent.profile_modes = (1,)
                        break
                await self.control.apply_stored(target)
                if (
                    not intent.command_result
                    or intent.command_result.status != CommandStatus.APPLIED
                ):
                    return
            self.status[target] = "complete"
        except asyncio.CancelledError:
            raise
        except Exception:
            self.status[target] = "error"
            _LOGGER.exception("Grid profile observation failed")
        finally:
            self.control.publish(target)

    def changed(self, snapshot):
        for target in tuple(self.tasks):
            if target.station == snapshot.token.station and (
                not snapshot.connected
                or self.control.runtime.enabled(target) is not True
                or self.control.runtime.authority(target.station)
                != ControlAuthority.REMOTE
            ):
                self.invalidate(target)
        self.sessions_changed()

    def sessions_changed(self):
        self.battery_dirty = True
        if not self.closed and (self.battery_task is None or self.battery_task.done()):
            self.battery_task = self.hass.async_create_background_task(
                self.battery_events(), "Grid battery reserve"
            )

    async def battery_events(self):
        while self.battery_dirty and not self.closed:
            self.battery_dirty = False
            await self.reconcile_battery()

    async def reconcile_battery(self, exclude=None):
        requests = [
            self.setting(t)["min_soc"]
            for s in self.control.runtime.stations
            for t in s.connectors
            if t != exclude
            and t not in self.suppressed
            and self.active(t)
            and self.control.runtime.enabled(t) is True
            and self.control.runtime.authority(t.station) == ControlAuthority.REMOTE
        ]
        await self.battery.update(max(requests) if requests else None)
        for target in self.control.intents:
            self.control.publish(target)

    async def save(self):
        await self.store.async_save(self.settings)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.unsubscribe_battery()
        self.unsubscribe()
        self.session_unsubscribe()
        tasks = list(self.tasks.values())
        for target in tuple(self.tasks):
            self.invalidate(target)
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.battery_task:
            await self.battery_task
        await self.battery.update(None)
        await self.save()
