"""Connection diagnostic only; no charging-state or control entities."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .entity import StationEntity, async_setup_station_entities


async def async_setup_entry(hass, entry, async_add_entities):
    async_setup_station_entities(
        hass,
        entry,
        async_add_entities,
        lambda runtime, entry_id, station: [
            ConnectedSensor(runtime, entry_id, station)
        ],
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
