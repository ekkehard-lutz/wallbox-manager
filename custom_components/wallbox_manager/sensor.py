"""Authentic measurements, reported status, diagnostics and passive sessions."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_interval

from .entity import WallboxEntity
from .session import KEYS, view

STATES = {
    "connector_state": [
        "unknown",
        "available",
        "occupied",
        "reserved",
        "unavailable",
        "faulted",
    ],
    "charging_state": [
        "unknown",
        "idle",
        "connected",
        "preparing",
        "charging",
        "suspended_vehicle",
        "suspended_station",
        "finishing",
    ],
}
DIAGNOSTICS = (
    "protocol_version",
    "connection_generation",
    "boot_generation",
    "revision",
    "discovery",
    "control_authority",
    "physical_phases",
)


async def async_setup_entry(hass, entry, add):
    import json

    state = entry.runtime_data.state
    seen = set()

    def add_key(key):
        if key in seen or not state.accepts(key[1]):
            return
        seen.add(key)
        add([Reading(entry, *key)])

    state.add_sensor = add_key
    state.add_session = lambda scope: [
        add_key(("session", tuple(scope), role)) for role in KEYS[:-1]
    ]
    entry.async_on_unload(lambda: setattr(state, "add_session", lambda scope: None))
    entry.async_on_unload(lambda: setattr(state, "add_sensor", None))
    for role in DIAGNOSTICS:
        add_key(("station", (state.binding[0], None, None), role))
    for key in state.observations:
        if key[0] in ("observation", "capability"):
            add_key(key)
    for registered in er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    ):
        for family in ("observation", "capability", "session"):
            prefix = f"{entry.entry_id}:{family}:"
            if (
                registered.unique_id.startswith(prefix)
                and registered.domain == "sensor"
            ):
                parts = json.loads(registered.unique_id[len(prefix) :])
                add_key((family, tuple(parts[:3]), parts[3]))
    # Fixed single-connector session identity exists even before the first transaction.
    scopes = set()
    if state.session:
        scopes.add(tuple(state.session["scope"]))
    for scope in scopes:
        for role in KEYS[:-1]:
            add_key(("session", scope, role))


class Reading(WallboxEntity, SensorEntity):
    def __init__(self, entry, family, scope, role):
        super().__init__(entry, family, scope, role)
        self._refresh = None
        self._attr_entity_category = (
            EntityCategory.DIAGNOSTIC
            if family in ("station", "capability") and role != "control_authority"
            else None
        )
        if role in STATES or role in ("control_authority", "discovery"):
            self._attr_device_class = "enum"
            self._attr_options = (
                ["unknown", "advertised", "verified", "unsupported", "degraded"]
                if role == "discovery"
                else STATES.get(role, ["unknown", "local", "remote"])
            )
        if family == "observation" and role not in STATES:
            prefix = role.split("_")[0]
            self._attr_device_class, self._attr_native_unit_of_measurement = {
                "voltage": ("voltage", "V"),
                "current": ("current", "A"),
                "power": ("power", "W"),
                "energy": ("energy", "kWh"),
            }[prefix]
            self._attr_state_class = (
                "total_increasing" if role == "energy" else "measurement"
            )
        elif family == "capability" and "current" in role and role != "zero_current":
            self._attr_native_unit_of_measurement = "A"
        elif family == "session":
            self._attr_device_class = {
                "started": "timestamp",
                "ended": "timestamp",
                "duration": "duration",
                "power": "power",
                "energy": "energy",
                "start_meter": "energy",
                "end_meter": "energy",
            }.get(role)
            self._attr_native_unit_of_measurement = {
                "duration": "s",
                "power": "W",
                "energy": "kWh",
                "start_meter": "kWh",
                "end_meter": "kWh",
            }.get(role)
            if role == "power":
                self._attr_state_class = "measurement"

    @property
    def available(self):
        if self.family == "observation" and self.role == "charging_state":
            return self.observed.charging_value(self.scope) is not None
        if self.family == "session":
            session = self.observed.session
            if not session or tuple(session["scope"]) != self.scope:
                return False
            if session["ended_at"] is not None or self.role not in (
                "power",
                "duration",
            ):
                return True
            return view(self.observed, datetime.now(UTC))[self.role] is not None
        if self.family == "station" and self.role in (
            "protocol_version",
            "connection_generation",
            "boot_generation",
            "revision",
            "discovery",
        ):
            return True
        if self.role == "physical_phases":
            return self.observed.last_observed_value(self.key) is not None
        return super().available

    @property
    def native_value(self):
        state = self.observed
        if self.family == "observation" and self.role == "charging_state":
            return state.charging_value(self.scope)
        if self.family == "session":
            return view(state, datetime.now(UTC))[self.role] if self.available else None
        if self.family == "station":
            match self.role:
                case "connection_generation":
                    return state.generation[0]
                case "boot_generation":
                    return state.generation[1]
                case "protocol_version":
                    return "2.1" if state.connected else None
        if self.role == "physical_phases":
            return state.last_observed_value(self.key)
        return state.value(self.key)

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.role in STATES:
            attrs.update(
                state_fresh=self.available,
                state_represents="last_known_observation",
                physical_observability="reported"
                if self.available
                else "awaiting_observation",
                cp_observability="unverified",
                physical_limit_since=None,
            )
        if (
            self.family == "observation"
            and self.role == "charging_state"
            and self.native_value == "idle"
            and self.observed.value(self.key) is None
        ):
            attrs.update(
                source="wbm:registered_available",
                connection_generation=self.observed.generation[0],
                boot_generation=self.observed.generation[1],
                state_represents="display_projection",
                physical_observability="inferred_from_connector_status",
            )
        if self.family == "capability":
            observation = self.observed.observations.get(self.key)
            attrs["evidence"] = observation.status if observation else "unknown"
        if self.family == "session" and self.available:
            session = self.observed.session
            active = session["ended_at"] is None
            attrs.update(
                session_id=session["session_id"],
                external_transaction_id=session["external_transaction_id"],
                session_active=view(self.observed, datetime.now(UTC))["active"],
                start_known=session["started_at"] is not None,
                valid_until=None,
            )
            if self.role == "power":
                attrs["power_availability_reason"] = (
                    "completed"
                    if not active
                    else "valid"
                    if self.native_value is not None
                    else "awaiting_observation"
                )
            if self.role == "power" and (channel := session.get("power_channel")):
                observation = self.observed.observations.get(
                    (channel[0], tuple(channel[1]), channel[2])
                )
                if observation:
                    attrs["source"] = observation.source
            elif self.role == "energy":
                attrs["source"] = session.get("energy_source")
            if self.role in ("start_meter", "end_meter"):
                acquired = session.get(self.role + "_at")
                event_at = session.get(
                    "started_at" if self.role == "start_meter" else "ended_at"
                )
                attrs.update(
                    measured_at=acquired,
                    event_at=event_at,
                    boundary_approximation=(acquired != event_at)
                    if acquired and event_at
                    else None,
                )
            if self.role == "duration":
                attrs.update(
                    started_at=session["started_at"], duration_valid_until=None
                )
        if self.role == "physical_phases":
            attrs["state_represents"] = "last_known_observation"
        if self.role == "discovery":
            attrs["initialization"] = self.entry.runtime_data.server.initialization
        return attrs

    def refresh(self):
        if self._refresh:
            self._refresh()
            self._refresh = None
        if (
            self.role == "duration"
            and self.available
            and self.observed.session
            and self.observed.session["ended_at"] is None
            and self.observed.session.get("generation")
            == list(self.observed.generation)
            and self.observed.connected
        ):
            self._refresh = async_track_time_interval(
                self.hass, self.tick, timedelta(seconds=60)
            )
        self.async_write_ha_state()

    @callback
    def tick(self, now):
        self.async_write_ha_state()

    def cancel_refresh(self):
        if self._refresh:
            self._refresh()
            self._refresh = None

    async def async_added_to_hass(self):
        if self.family == "session" and self.role == "duration":
            await SensorEntity.async_added_to_hass(self)
            self.async_on_remove(self.observed.watch(self.key, self.refresh))
            self.async_on_remove(self.cancel_refresh)
            self.refresh()
        else:
            await super().async_added_to_hass()
