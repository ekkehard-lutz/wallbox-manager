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
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_interval,
)

from .core.telemetry import Quantity
from .entity import StationEntity, device_info, scope_attributes
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
        self._refresh = None
        self._refresh_deadline = None
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
        self._session_view = self.session_view()

    @property
    def session(self):
        return self.runtime.sessions.get(self.scope)

    def runtime_view(self, snapshot):
        if snapshot is None or snapshot.token.station != self.station:
            return None
        metadata = (
            snapshot.token,
            snapshot.connected,
            snapshot.identity,
            snapshot.connectors,
        )
        quantity = {"power": Quantity.POWER, "energy": Quantity.ENERGY}.get(self.key)
        return metadata, tuple(
            o
            for o in snapshot.observations
            if quantity is not None
            and o.channel.quantity == quantity
            and o.channel.scope in (self.scope, self.session.evse_id)
        )

    @property
    def available(self):
        return self.session is not None

    def power_status(self, now):
        session = self.session
        snapshot = self.runtime.get(session.station_id)
        if session.active and (snapshot is None or not snapshot.connected):
            return "disconnected", None
        return self.runtime.sessions.power_status(self.scope, now)

    @property
    def extra_state_attributes(self):
        energy = (
            self.runtime.sessions.measurement(
                self.scope,
                Quantity.POWER if self.key == "power" else Quantity.ENERGY,
                datetime.now(UTC),
            )
            if self.key in ("power", "energy")
            else None
        )
        now = datetime.now(UTC)
        return {
            **super().extra_state_attributes,
            **(
                {
                    "duration_sampled_at": now.isoformat(),
                    "duration_valid_until": (now + timedelta(seconds=90)).isoformat()
                    if self.session.active
                    else None,
                }
                if self.key == "duration"
                else {}
            ),
            "session_id": self.session.session_id,
            **scope_attributes(self.runtime, self.entry_id, self.scope),
            "session_active": self.session.active,
            **(
                {"power_availability_reason": self.power_status(now)[0]}
                if self.key == "power"
                else {}
            ),
            # Source timing from the already selected reading, not another poll.
            # PV freshness validates both source acquisition and HA report time.
            **(
                {
                    "observed_at": energy.observed_at.isoformat(),
                    "received_at": energy.received_at.isoformat(),
                }
                if self.key == "power"
                and self.session.active
                and energy
                and energy.observed_at == self.session.power_at
                and energy.value == self.session.current_power_w
                else {}
            ),
            "valid_until": energy.valid_until.isoformat()
            if self.key in ("energy", "power")
            and energy
            and energy.observed_at
            == (
                self.session.power_at if self.key == "power" else self.session.energy_at
            )
            else None,
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
        self.async_on_remove(self._cancel_refresh)
        self._schedule_refresh()

    def session_view(self):
        session = self.session
        fields = {
            "started": ("started_at",),
            "ended": ("ended_at",),
            "duration": ("started_at", "ended_at"),
            "power": ("current_power_w", "power_at", "power_valid_until"),
            "energy": ("energy_charged_wh", "energy_at"),
            "start_meter": ("energy_start_wh",),
            "end_meter": ("energy_end_wh",),
        }.get(self.key, ())
        return (
            session.session_id,
            session.active,
            session.start_known,
            tuple(getattr(session, field) for field in fields),
            self.runtime.sessions.measurement(
                self.scope,
                Quantity.POWER if self.key == "power" else Quantity.ENERGY,
                datetime.now(UTC),
            )
            if self.key in ("power", "energy")
            else None,
        )

    @callback
    def _cancel_refresh(self):
        if self._refresh:
            self._refresh()
        self._refresh = self._refresh_deadline = None

    @callback
    def _schedule_refresh(self):
        if self.key not in ("duration", "power"):
            return
        now = datetime.now(UTC)
        deadline = None
        if self.session.active:
            if self.key == "duration":
                deadline = "active"
            elif self.snapshot and self.snapshot.connected:
                _, sample = self.power_status(now)
                if sample and sample.valid_until and sample.valid_until > now:
                    deadline = sample.valid_until
        if deadline == self._refresh_deadline:
            return
        self._cancel_refresh()
        self._refresh_deadline = deadline
        if deadline == "active":
            self._refresh = async_track_time_interval(
                self.hass, self._tick, timedelta(seconds=1)
            )
        elif deadline is not None:
            self._refresh = async_track_point_in_utc_time(
                self.hass, self._power_expired, deadline
            )

    @callback
    def _power_expired(self, now):
        self._refresh = self._refresh_deadline = None
        self._session_view = self.session_view()
        self.async_write_ha_state()
        self._schedule_refresh()

    @callback
    def _changed(self, snapshot):
        if snapshot.token.station == self.station:
            before = self.runtime_view(self.snapshot)
            self.snapshot = snapshot
            self._session_changed(
                before is None or before[0] != self.runtime_view(snapshot)[0]
            )

    @callback
    def _session_changed(self, force=False):
        view = self.session_view()
        if force or view != self._session_view:
            self._session_view = view
            self._schedule_refresh()
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
                reason, _ = self.power_status(now)
                if reason == "completed":
                    return 0
                if reason != "valid":
                    return None
                return float(session.current_power_w)
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
