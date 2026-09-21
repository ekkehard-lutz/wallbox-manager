"""Read-only discovery diagnostics; no electrical measurements.

Entity description/category pattern adapted from pinned lbbrhzn/ocpp sensor.py,
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See THIRD_PARTY_NOTICES.md.
"""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.entity import EntityCategory

from .core.capabilities import EvidenceState
from .entity import StationEntity, async_setup_station_entities

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
    async_setup_station_entities(
        hass,
        entry,
        async_add_entities,
        lambda runtime, entry_id, station: [
            DiagnosticSensor(runtime, entry_id, station, description)
            for description in DESCRIPTIONS
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
