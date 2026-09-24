"""Installation-wide, fail-closed profile ownership and explicit activation."""

import asyncio
import json

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .control.commands import CommandReason, CommandResult, CommandStatus, ControlArea
from .core.authority import ControlAuthority
from .core.models import ConnectorId, EvseId, StationId
from .entity import station_identifier


def identity(entry_id, target):
    return json.dumps(
        [entry_id, target.station.value, target.evse.value, target.value],
        separators=(",", ":"),
    )


def failure(reason):
    return CommandResult(
        CommandStatus.TEMPORARILY_REJECTED,
        ControlArea.AUTHORITY,
        CommandReason.BUSY,
        reason,
    )


class ProfileOwnership:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(
            hass, 1, "wallbox_manager.active_wallbox", atomic_writes=True
        )
        self.active_wallbox = None
        self.ready = False
        self.transition = False
        self.status = "take_control_required"
        self.entries = {}
        self.listeners = set()
        self.lock = asyncio.Lock()
        self.loaded = False

    async def load(self):
        async with self.lock:
            if not self.loaded:
                data = await self.store.async_load() or {}
                self.active_wallbox = data.get("active_wallbox")
                self.loaded = True

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def publish(self):
        for listener in tuple(self.listeners):
            listener()
        for _, control in self.entries.values():
            for target in tuple(control.intents):
                control.publish(target)

    def register(self, entry, control):
        self.entries[entry.entry_id] = (entry, control)
        control.ownership = self
        control.entry_id = entry.entry_id
        unsubscribe = control.runtime.subscribe(lambda snapshot: self.changed())

        @callback
        def device_changed(event):
            self.publish()

        unsubscribe_device = self.hass.bus.async_listen(
            "device_registry_updated", device_changed
        )
        self.publish()

        def remove():
            unsubscribe()
            unsubscribe_device()
            self.entries.pop(entry.entry_id, None)
            self.changed()

        return remove

    def resolve(self, key):
        try:
            entry_id, station, evse, connector = json.loads(key)
            target = ConnectorId(EvseId(StationId(station), evse), connector)
            return self.entries[entry_id][1], target
        except ValueError, TypeError, KeyError:
            return None, None

    def inventory(self):
        items = {}
        for entry_id, (entry, control) in self.entries.items():
            targets = {
                t
                for s in control.runtime.stations
                if s.protocol_version == "2.1"
                for t in s.connectors
            }
            for subentry in entry.subentries.values():
                if subentry.subentry_type == "wallbox":
                    data = subentry.data
                    targets.add(
                        ConnectorId(
                            EvseId(StationId(data["station"]), data["evse"]),
                            data["connector"],
                        )
                    )
            for target in sorted(
                targets, key=lambda t: (t.station.value, t.evse.value, t.value)
            ):
                state = control.runtime.get(target.station)
                device = dr.async_get(self.hass).async_get_device(
                    identifiers={(DOMAIN, station_identifier(entry_id, target.station))}
                )
                display_name = (
                    (device.name_by_user or device.name)
                    if device
                    else target.station.value
                )
                items[identity(entry_id, target)] = {
                    "display_name": display_name,
                    "name": (f"{display_name} / {target.evse.value} / {target.value}"),
                    "connected": bool(
                        state and state.connected and target in state.connectors
                    ),
                    "station_id": target.station.value,
                    "entry_id": entry_id,
                }
        return items

    def permits(self, control, target):
        state = control.runtime.get(target.station)
        return bool(
            self.ready
            and not self.transition
            and self.active_wallbox == identity(control.entry_id, target)
            and state
            and state.connected
            and target in state.connectors
            and control.runtime.authority(target.station) == ControlAuthority.REMOTE
        )

    def changed(self):
        control, target = self.resolve(self.active_wallbox)
        if self.ready and (control is None or not self.permits(control, target)):
            self.ready = False
            self.status = "take_control_required"
            if control and hasattr(control, "profiles"):
                control.profiles.invalidate(target)
                control.profiles.sessions_changed()
        self.publish()

    async def activate_station(self, control, station):
        state = control.runtime.get(station)
        if state is None or len(state.connectors) != 1:
            return failure("select_connector")
        return await self.activate(identity(control.entry_id, state.connectors[0]))

    async def activate(self, key):
        async with self.lock:
            new, target = self.resolve(key)
            if (
                new is None
                or key not in self.inventory()
                or not self.inventory()[key]["connected"]
            ):
                self.status = "wallbox_unavailable"
                self.publish()
                return failure(self.status)
            self.transition = True
            self.ready = False
            self.status = "switching"
            for _, control in self.entries.values():
                for t in tuple(control.intents):
                    if hasattr(control, "profiles"):
                        control.profiles.invalidate(t)
                    else:
                        control.intent(t).generation += 1
            self.publish()
            try:
                old, previous = self.resolve(self.active_wallbox)
                if self.active_wallbox:
                    if old is None:
                        return self.failed("previous_wallbox_unavailable")
                    authority = old.runtime.authority(previous.station)
                    if authority == ControlAuthority.REMOTE:
                        result = await old.request_enabled(previous, False)
                        if (
                            not result
                            or result.status != CommandStatus.APPLIED
                            or old.runtime.enabled(previous) is not False
                        ):
                            return self.failed("previous_off_unconfirmed")
                    elif authority != ControlAuthority.LOCAL:
                        return self.failed("previous_authority_unknown")
                    # LOCAL is an independent load. Never reacquire it to stop it.
                for _, control in self.entries.values():
                    if hasattr(control, "profiles"):
                        await control.profiles.reconcile_battery()
                        if control.profiles.battery.record:
                            return self.failed("battery_restore_pending")
                result = await new._take_control(target.station)
                if not result or result.status != CommandStatus.APPLIED:
                    return self.failed("takeover_failed", result)
                state = new.runtime.get(target.station)
                token, revision = state.token, state.authority_revision

                def confirmed():
                    state = new.runtime.get(target.station)
                    return (
                        not new._closed
                        and new.runtime.current(token)
                        and state.authority_revision == revision
                        and new.runtime.authority(target.station)
                        == ControlAuthority.REMOTE
                        and new.runtime.enabled(target) is False
                    )

                if not confirmed():
                    return self.failed("off_unconfirmed")
                await self.store.async_save({"active_wallbox": key})
                self.active_wallbox = key
                if not confirmed():
                    return self.failed("takeover_stale")
                self.ready = True
                self.status = "ready"
                return result
            except Exception:
                self.status = "transition_failed"
                raise
            finally:
                self.transition = False
                self.publish()

    def failed(self, status, result=None):
        self.status = status
        return result or failure(status)


async def async_get_ownership(hass):
    if "wallbox_manager_ownership" not in hass.data:
        hass.data["wallbox_manager_ownership"] = ProfileOwnership(hass)
    site = hass.data["wallbox_manager_ownership"]
    await site.load()
    return site
