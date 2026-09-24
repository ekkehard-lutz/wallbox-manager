"""Stable EVSE intent entities, separate from execution readiness."""

import json
from datetime import UTC, datetime

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.restore_state import RestoreEntity

from .core.models import ConnectorId, EvseId, StationId
from .entity import device_info


def control_id(entry_id, target, key):
    return f"{entry_id}:control:" + json.dumps(
        [target.station.value, target.evse.value, target.value, key],
        separators=(",", ":"),
    )


@callback
def setup_control_entities(hass, entry, add_entities, factory):
    runtime = entry.runtime_data.state
    control = entry.runtime_data.control
    seen = set()

    @callback
    def add(target):
        if target not in seen:
            seen.add(target)
            add_entities(factory(control, entry.entry_id, target))

    @callback
    def changed(snapshot):
        if snapshot.protocol_version == "2.1":
            for target in snapshot.connectors:
                if (
                    target.value.isascii()
                    and target.value.isdecimal()
                    and int(target.value) > 0
                    and str(int(target.value)) == target.value
                ):
                    add(target)

    entry.async_on_unload(runtime.subscribe(changed))
    # Persisted registry identity keeps offline entities present after reload.
    prefix = f"{entry.entry_id}:control:"
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if entity.unique_id.startswith(prefix):
            try:
                station, evse, connector, _ = json.loads(
                    entity.unique_id[len(prefix) :]
                )
                add(ConnectorId(EvseId(StationId(station), evse), connector))
            except ValueError, TypeError:
                continue
    for snapshot in runtime.stations:
        changed(snapshot)


class ControlEntity(RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_available = True

    def __init__(self, control, entry_id, target, key):
        self.control = control
        self.target = target
        self.entry_id = entry_id
        self.key = key
        self._expire = None
        self._attr_unique_id = control_id(entry_id, target, key)
        self._attr_translation_key = key
        self._attr_translation_placeholders = {
            "scope": f"EVSE {target.evse.value} / {target.value}"
        }

    @property
    def device_info(self):
        return device_info(
            self.entry_id,
            self.target.station,
            self.control.runtime.get(self.target.station),
        )

    @property
    def intent(self):
        return self.control.intent(self.target)

    @property
    def extra_state_attributes(self):
        return {
            "evse_id": self.target.evse.value,
            "connector_id": self.target.value,
            "state_represents": "desired_intent",
            **self.control.attributes(self.target),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        if previous is not None:
            try:
                self.restore_state(previous)
            except ValueError, TypeError:
                pass  # Unknown/unavailable or obsolete state cannot authorize work.
        self.async_on_remove(self._cancel_expiry)
        self._schedule_expiry()
        self.async_on_remove(self.control.subscribe(self._changed))
        self.async_on_remove(self.control.runtime.subscribe(self._telemetry))
        self.async_on_remove(
            self.control.runtime.sessions.subscribe(self._session_changed)
        )

    def restore_state(self, previous):
        self.restore_value(previous.state)

    @callback
    def _cancel_expiry(self):
        if self._expire is not None:
            self._expire()
            self._expire = None

    @callback
    def _schedule_expiry(self):
        self._cancel_expiry()
        snapshot = self.control.runtime.get(self.target.station)
        now = datetime.now(UTC)
        deadlines = (
            [
                o.valid_until
                for o in snapshot.observations
                if o.channel.scope == self.target
                and o.channel.quantity.value.startswith("voltage_")
                and o.valid_until is not None
                and o.valid_until > now
            ]
            if snapshot and snapshot.connected
            else []
        )
        if snapshot and snapshot.connected:
            deadlines.extend(
                o.valid_until
                for o in snapshot.enabled
                if o.scope == self.target and o.valid_until > now
            )
            deadlines.extend(
                o.valid_until
                for o in snapshot.physical_phases
                if o.scope == self.target and o.valid_until > now
            )
        if deadlines:
            self._expire = async_track_point_in_utc_time(
                self.hass, self._expired, min(deadlines)
            )

    @callback
    def _expired(self, now):
        self._expire = None
        self.async_write_ha_state()
        self._schedule_expiry()

    @callback
    def _session_changed(self):
        self.async_write_ha_state()

    @callback
    def _changed(self, target):
        if target == self.target:
            self.async_write_ha_state()

    @callback
    def _telemetry(self, snapshot):
        if snapshot.token.station == self.target.station:
            self._schedule_expiry()
            self.async_write_ha_state()
