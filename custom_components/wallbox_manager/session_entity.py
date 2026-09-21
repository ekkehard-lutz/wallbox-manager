"""Thin active-or-last session projections with persistent per-scope identities."""

import json
from datetime import UTC, datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .core.telemetry import Quantity
from .entity import StationEntity, device_info
from .session_ledger import scope_parts

SENSOR_KEYS = (
    "id",
    "started",
    "ended",
    "duration",
    "power",
    "energy",
    "start_meter",
    "end_meter",
)


@callback
def setup_session_entities(hass, entry, async_add_entities, *, binary=False):
    runtime = entry.runtime_data.state
    seen = set()

    @callback
    def changed():
        from homeassistant.helpers import device_registry as dr

        entities = []
        for session in runtime.sessions.latest:
            if session.scope in seen:
                continue
            seen.add(session.scope)
            dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                **device_info(
                    entry.entry_id, session.station_id, runtime.get(session.station_id)
                ),
            )
            if binary:
                entities.append(
                    SessionActive(runtime, entry.entry_id, session.scope, "active")
                )
            else:
                entities.extend(
                    SessionSensor(runtime, entry.entry_id, session.scope, key)
                    for key in SENSOR_KEYS
                )
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(runtime.sessions.subscribe(changed))
    changed()


class SessionEntity(StationEntity):
    _attr_entity_category = None

    def __init__(self, runtime, entry_id, scope, key):
        self.scope = scope
        self.key = key
        station, evse, connector = scope_parts(scope)
        super().__init__(
            runtime, entry_id, runtime.sessions.get(scope).station_id, f"session_{key}"
        )
        self._attr_unique_id = f"{entry_id}:session:" + json.dumps(
            [station, evse, connector, key], separators=(",", ":")
        )
        self._attr_translation_placeholders = {
            "scope": f"EVSE {evse}" + (f" / {connector}" if connector else "")
        }

    @property
    def session(self):
        return self.runtime.sessions.get(self.scope)

    @property
    def available(self):
        return self.session is not None

    @property
    def extra_state_attributes(self):
        return {
            "station_id": self.station.value,
            "evse_id": self.session.evse_id.value,
            "connector_id": self.session.connector_id.value
            if self.session.connector_id
            else None,
            "external_transaction_id": self.session.external_transaction_id,
            "start_known": self.session.start_known,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.sessions.subscribe(self._session_changed))
        if self.key in ("duration", "power"):
            self.async_on_remove(
                async_track_time_interval(self.hass, self._tick, timedelta(seconds=1))
            )

    @callback
    def _session_changed(self):
        self.async_write_ha_state()

    @callback
    def _tick(self, now):
        if self.session.active:
            self.async_write_ha_state()


class SessionActive(SessionEntity, BinarySensorEntity):
    @property
    def is_on(self):
        return self.session.active


class SessionSensor(SessionEntity, SensorEntity):
    def __init__(self, runtime, entry_id, scope, key):
        super().__init__(runtime, entry_id, scope, key)
        if key in ("started", "ended"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "duration":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "s"
        elif key == "power":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_native_unit_of_measurement = "W"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key in ("energy", "start_meter", "end_meter"):
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"

    @property
    def native_value(self):
        session = self.session
        now = datetime.now(UTC)
        match self.key:
            case "id":
                return session.session_id
            case "started":
                return session.started_at
            case "ended":
                return session.ended_at
            case "duration":
                return session.duration(now)
            case "power":
                if not session.active:
                    return 0
                # Persistence does not turn old power into a live observation.
                snapshot = self.runtime.get(session.station_id)
                if snapshot is None or not snapshot.connected:
                    return None
                observation = self.runtime.sessions.measurement(
                    session.scope, Quantity.POWER, now
                )
                if (
                    observation is None
                    or not observation.fresh(now)
                    or observation.observed_at < session.started_at
                    or observation.observed_at != session.power_at
                    or observation.value != session.current_power_w
                ):
                    return None
                return (
                    float(session.current_power_w)
                    if session.current_power_w is not None
                    else None
                )
            case _:
                value = getattr(
                    session,
                    {
                        "energy": "energy_charged_wh",
                        "start_meter": "energy_start_wh",
                        "end_meter": "energy_end_wh",
                    }[self.key],
                )
                return float(value / 1000) if value is not None else None
