"""Read-only discovery diagnostics and scoped runtime observations.

Entity description/category pattern adapted from pinned lbbrhzn/ocpp sensor.py,
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See THIRD_PARTY_NOTICES.md.
"""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory

from .core.capabilities import EvidenceState
from .core.telemetry import STATE_OPTIONS, Quantity
from .entity import StationEntity, async_setup_station_entities
from .observation_entity import ObservationEntity
from .session_entity import setup_session_entities

DESCRIPTIONS = tuple(
    SensorEntityDescription(
        key=key, translation_key=key, entity_category=EntityCategory.DIAGNOSTIC
    )
    for key in (
        "protocol_version",
        "connection_generation",
        "boot_generation",
        "revision",
    )
) + (
    SensorEntityDescription(
        key="discovery",
        translation_key="discovery",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in EvidenceState],
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    from .capability_entity import setup_capability_entities

    setup_capability_entities(hass, entry, async_add_entities)
    from .authority_entity import setup_authority_entities

    setup_authority_entities(
        hass,
        entry,
        async_add_entities,
        "control_authority",
        lambda station: AuthoritySensor(
            entry.runtime_data.state, entry.entry_id, station
        ),
    )
    setup_session_entities(hass, entry, async_add_entities, binary=False)
    async_setup_station_entities(
        hass,
        entry,
        async_add_entities,
        lambda runtime, entry_id, station: [
            DiagnosticSensor(runtime, entry_id, station, description)
            for description in DESCRIPTIONS
        ],
        observation_factory=lambda runtime, entry_id, channel: [
            ObservationSensor(runtime, entry_id, channel)
        ],
    )


class DiagnosticSensor(StationEntity, SensorEntity):
    def __init__(self, runtime, entry_id, station, description):
        super().__init__(runtime, entry_id, station, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if self.snapshot is None:
            return None
        match self.entity_description.key:
            case "protocol_version":
                return self.snapshot.protocol_version
            case "connection_generation":
                return self.snapshot.token.connection_generation
            case "boot_generation":
                return self.snapshot.token.boot_generation
            case "revision":
                return self.snapshot.capabilities.revision
            case "discovery":
                return self.snapshot.discovery.state.value

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.entity_description.key == "discovery" and self.snapshot is not None:
            attrs.update(
                reason=self.snapshot.discovery.reason,
                source=self.snapshot.discovery.source,
                observed_at=self.snapshot.discovery.observed_at.isoformat(),
            )
        return attrs


class ObservationSensor(ObservationEntity, SensorEntity):
    def __init__(self, runtime, entry_id, channel):
        super().__init__(runtime, entry_id, channel)
        quantity = channel.quantity
        if quantity in STATE_OPTIONS:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [state.value for state in STATE_OPTIONS[quantity]]
        else:
            prefix = quantity.value.split("_")[0]
            device_class, unit = {
                "voltage": (SensorDeviceClass.VOLTAGE, "V"),
                "current": (SensorDeviceClass.CURRENT, "A"),
                "power": (SensorDeviceClass.POWER, "W"),
                "energy": (SensorDeviceClass.ENERGY, "kWh"),
            }[prefix]
            self._attr_device_class = device_class
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = (
                SensorStateClass.TOTAL_INCREASING
                if quantity == Quantity.ENERGY
                else SensorStateClass.MEASUREMENT
            )

    @property
    def native_value(self):
        observation = self.observation
        if observation is None or observation.value is None:
            return None
        if self.channel.quantity in STATE_OPTIONS:
            return observation.value.value
        value = observation.value
        if self.channel.quantity == Quantity.ENERGY:
            value /= 1000
        return float(value)


class AuthoritySensor(StationEntity, SensorEntity):
    """Observed authority; desired charging permission is a separate entity."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = None
    _attr_options = ["unknown", "local", "remote"]

    def __init__(self, runtime, entry_id, station):
        super().__init__(runtime, entry_id, station, "control_authority")

    @property
    def available(self):
        return bool(self.snapshot and self.snapshot.connected)

    @property
    def native_value(self):
        return self.runtime.authority(self.station).value

    @property
    def extra_state_attributes(self):
        observation = self.snapshot.authority if self.snapshot else None
        return {
            **super().extra_state_attributes,
            "source": observation.source if observation else None,
            "observed_at": observation.observed_at.isoformat() if observation else None,
        }
