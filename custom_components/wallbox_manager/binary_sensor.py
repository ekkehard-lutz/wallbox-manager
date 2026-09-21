"""Connection diagnostics and reported operational state, without controls."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .core.telemetry import Quantity, state_flag
from .entity import StationEntity, async_setup_station_entities
from .observation_entity import ObservationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_setup_station_entities(
        hass,
        entry,
        async_add_entities,
        lambda runtime, entry_id, station: [
            ConnectedSensor(runtime, entry_id, station)
        ],
        observation_factory=state_entities,
    )


class ConnectedSensor(StationEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime, entry_id, station):
        super().__init__(runtime, entry_id, station, "connected")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.snapshot is not None and self.snapshot.connected


STATE_FLAGS = {
    Quantity.CONNECTOR_STATE: ("available", "occupied"),
    Quantity.CHARGING_STATE: ("vehicle_connected", "charging_active"),
}


def state_entities(runtime, entry_id, channel):
    return [
        StateBinarySensor(runtime, entry_id, channel, flag)
        for flag in STATE_FLAGS.get(channel.quantity, ())
    ]


class StateBinarySensor(ObservationEntity, BinarySensorEntity):
    def __init__(self, runtime, entry_id, channel, flag):
        super().__init__(runtime, entry_id, channel, flag)
        self.flag = flag
        self._attr_device_class = {
            "occupied": BinarySensorDeviceClass.OCCUPANCY,
            "vehicle_connected": BinarySensorDeviceClass.PLUG,
            "charging_active": BinarySensorDeviceClass.BATTERY_CHARGING,
        }.get(flag)

    @property
    def is_on(self):
        observation = self.observation
        return state_flag(observation.value if observation else None, self.flag)
