"""Installation-wide, fail-closed profile ownership and explicit activation."""

import asyncio
import json
from dataclasses import asdict

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
        self.record = None
        self.context = None
        self.suspended = set()
        self.recovery_tasks = {}

    async def load(self):
        async with self.lock:
            if not self.loaded:
                data = await self.store.async_load() or {}
                if not isinstance(data, dict):
                    data = {}
                self.active_wallbox = data.get("active_wallbox")
                record = data.get("ownership")
                if (
                    isinstance(record, dict)
                    and record.get("version") == 1
                    and record.get("key") == self.active_wallbox
                    and type(record.get("enabled_intent")) is bool
                    and isinstance(record.get("station_identity"), dict)
                    and set(record["station_identity"])
                    == {"vendor", "model", "serial", "firmware"}
                    and all(
                        v is None or isinstance(v, str)
                        for v in record["station_identity"].values()
                    )
                ):
                    self.record = record
                    self.status = "awaiting_ownership_evidence"
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
        self.suspended.discard(entry.entry_id)
        self.entries[entry.entry_id] = (entry, control)
        control.ownership = self
        control.entry_id = entry.entry_id
        unsubscribe = control.runtime.subscribe(lambda snapshot: self.changed())
        unsubscribe_control = control.subscribe(
            lambda target: self.checkpoint(control, target)
        )

        @callback
        def device_changed(event):
            self.publish()

        unsubscribe_device = self.hass.bus.async_listen(
            "device_registry_updated", device_changed
        )
        self.changed()

        def remove():
            unsubscribe()
            unsubscribe_control()
            unsubscribe_device()
            if self.entries.get(entry.entry_id, (None, None))[1] is control:
                self.entries.pop(entry.entry_id, None)
                self.changed()
            self.recovery_tasks.pop(control, None)

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
            and self.context == (state.token, state.authority_revision)
            and self.record is not None
            and asdict(state.identity) == self.record["station_identity"]
        )

    def saved(self):
        return {"active_wallbox": self.active_wallbox, "ownership": self.record}

    def revoke(self, reason):
        self.ready = False
        self.record = None
        self.context = None
        self.status = reason
        self.store.async_delay_save(self.saved, 0)

    async def permission_intent(self, control, target, enabled):
        if self.permits(control, target) and self.record:
            self.record = {**self.record, "enabled_intent": enabled}
            await self.store.async_save(self.saved())

    def checkpoint(self, control, target):
        profile = getattr(control, "profiles", None)
        if (
            not self.record
            or not profile
            or profile.closed
            or (
                self.status == "restored_ownership"
                and control not in self.recovery_tasks
            )
            or (target in profile.recoveries and not profile.recoveries[target].done())
            or not self.permits(control, target)
        ):
            return
        session = control.runtime.sessions.get(target)
        record = {
            **self.record,
            "continuation": profile.pv_ongoing.get(target, False),
            "battery_allowed": profile.pv_battery.get(target, False),
            "transaction": session.external_transaction_id
            if session and session.active
            else None,
        }
        if record != self.record:
            self.record = record
            self.store.async_delay_save(self.saved, 0)

    async def suspend(self, control):
        """A deliberate HA teardown preserves history, but revokes live execution."""
        self.suspended.add(control.entry_id)
        if self.active_wallbox and self.resolve(self.active_wallbox)[0] is control:
            if self.record and self.ready:
                _, target = self.resolve(self.active_wallbox)
                profile = control.profiles
                session = control.runtime.sessions.get(target)
                self.record = {
                    **self.record,
                    "continuation": profile.pv_ongoing.get(target, False),
                    "battery_allowed": profile.pv_battery.get(target, False),
                    "transaction": session.external_transaction_id
                    if session and session.active
                    else None,
                }
            self.ready = False
            self.context = None
            await self.store.async_save(self.saved())

    def changed(self):
        control, target = self.resolve(self.active_wallbox)
        if self.transition:
            self.publish()
            return
        if control and control.entry_id in self.suspended:
            # Teardown disconnects are expected; an actually observed Local
            # transition still revokes history, even during shutdown.
            state = control.runtime.get(target.station)
            if (
                state
                and state.connected
                and control.runtime.authority(target.station) == ControlAuthority.LOCAL
            ):
                self.revoke("ownership_rejected_local")
            self.publish()
            return
        if self.ready and (control is None or not self.permits(control, target)):
            self.revoke("ownership_invalidated")
            if control and hasattr(control, "profiles"):
                control.profiles.invalidate(target)
                control.profiles.sessions_changed()
        elif not self.ready and self.record and control:
            state = control.runtime.get(target.station)
            if state and state.connected:
                authority = control.runtime.authority(target.station)
                if authority == ControlAuthority.LOCAL:
                    self.revoke("ownership_rejected_local")
                elif (
                    state.authority is not None and authority == ControlAuthority.REMOTE
                ):
                    if (
                        target not in state.connectors
                        or asdict(state.identity) != self.record["station_identity"]
                    ):
                        self.revoke("ownership_rejected_identity")
                    elif control.runtime.enabled(target) is not None:
                        self.context = (state.token, state.authority_revision)
                        self.ready = True
                        self.status = "restored_ownership"
        if (
            self.ready
            and self.record
            and self.status == "restored_ownership"
            and control
        ):
            profile = getattr(control, "profiles", None)
            if (
                profile
                and profile.recovery_ready
                and not profile.closed
                and control not in self.recovery_tasks
            ):
                self.recovery_tasks[control] = profile.recoveries[target] = (
                    self.hass.async_create_background_task(
                        profile.recover(target, dict(self.record)), "Profile recovery"
                    )
                )
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
            if self.record is not None:
                self.record = None
                await self.store.async_save(self.saved())
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
                record = {
                    "version": 1,
                    "key": key,
                    "station_identity": asdict(state.identity),
                    "enabled_intent": False,
                }
                await self.store.async_save(
                    {"active_wallbox": key, "ownership": record}
                )
                self.active_wallbox = key
                if not confirmed():
                    return self.failed("takeover_stale")
                self.record = record
                self.context = (token, revision)
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
