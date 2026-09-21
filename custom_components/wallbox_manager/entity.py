"""Push-only station diagnostics, independent of protocol adapter objects.

DeviceInfo, diagnostic categorization and subscription/removal patterns adapted
from lbbrhzn/ocpp sensor.py and __init__.py at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See THIRD_PARTY_NOTICES.md. Generic Runtime replaces direct charge-point access.
"""

import json
from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityCategory

from .const import DOMAIN
from .core.events import StationSnapshot
from .core.models import ConnectorId, EvseId, StationId
from .core.telemetry import Channel, Quantity
from .runtime import Runtime


def station_identifier(entry_id: str, station: StationId) -> str:
    """Stable across runtime restarts; separate identical IDs on other listeners."""
    return f"{entry_id}:{station.value}"


def device_info(entry_id: str, station: StationId, snapshot: StationSnapshot | None):
    info = DeviceInfo(
        identifiers={(DOMAIN, station_identifier(entry_id, station))},
        name=station.value,
    )
    if snapshot is not None:
        for key, value in (
            ("manufacturer", snapshot.identity.vendor),
            ("model", snapshot.identity.model),
            ("sw_version", snapshot.identity.firmware),
            ("serial_number", snapshot.identity.serial),
        ):
            if value is not None:
                info[key] = value
    return info


def observation_unique_id(entry_id, channel, projection=None):
    scope = channel.scope
    evse = (
        scope.evse
        if isinstance(scope, ConnectorId)
        else scope
        if isinstance(scope, EvseId)
        else None
    )
    station = evse.station if evse else scope
    parts = [
        station.value,
        evse.value if evse else None,
        scope.value if isinstance(scope, ConnectorId) else None,
        channel.quantity.value,
        projection,
    ]
    return f"{entry_id}:observation:" + json.dumps(parts, separators=(",", ":"))


def restored_channel(entry_id, unique_id):
    prefix = f"{entry_id}:observation:"
    if not unique_id.startswith(prefix):
        return None
    try:
        station, evse, connector, quantity, _ = json.loads(unique_id[len(prefix) :])
        scope = StationId(station)
        if evse is not None:
            scope = EvseId(scope, evse)
        if connector is not None:
            scope = ConnectorId(scope, connector)
        return Channel(scope, Quantity(quantity))
    except TypeError, ValueError:
        return None


@callback
def async_setup_station_entities(
    hass, entry, async_add_entities, factory: Callable, observation_factory=None
):
    """Subscribe before replaying snapshots; discover new stations without reload."""
    runtime = entry.runtime_data.state
    registry = dr.async_get(hass)
    seen: set[StationId] = set()
    observed_entities = set()

    @callback
    def add_observation(channel):
        if observation_factory is None:
            return
        entities = []
        for entity in observation_factory(runtime, entry.entry_id, channel):
            if entity.unique_id not in observed_entities:
                observed_entities.add(entity.unique_id)
                entities.append(entity)
        if entities:
            async_add_entities(entities)

    @callback
    def add(station, snapshot):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **device_info(entry.entry_id, station, snapshot),
        )
        if station not in seen:
            seen.add(station)
            async_add_entities(factory(runtime, entry.entry_id, station))
        if snapshot is not None:
            for channel in snapshot.supported_channels:
                add_observation(channel)

    @callback
    def changed(snapshot):
        add(snapshot.token.station, snapshot)

    entry.async_on_unload(runtime.subscribe(changed))
    # Registry identities persist; don't invent a runtime connection on reload.
    prefix = f"{entry.entry_id}:"
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith(prefix):
                station = StationId(identifier[len(prefix) :])
                add(station, runtime.get(station))
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if channel := restored_channel(entry.entry_id, entity.unique_id):
            add_observation(channel)
    for snapshot in runtime.stations:
        changed(snapshot)


class StationEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: Runtime, entry_id: str, station: StationId, key: str):
        self.runtime = runtime
        self.station = station
        self.entry_id = entry_id
        self.snapshot = runtime.get(station)
        self._attr_unique_id = f"{station_identifier(entry_id, station)}:{key}"
        self._attr_translation_key = key

    @property
    def device_info(self):
        return device_info(self.entry_id, self.station, self.snapshot)

    @property
    def available(self):
        return self.snapshot is not None

    @property
    def extra_state_attributes(self):
        attrs = {
            "station_id": self.station.value,
            "runtime_incarnation": self.runtime.runtime_id,
        }
        if self.snapshot is not None:
            attrs["connected"] = self.snapshot.connected
        return attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self._changed))
        # Updates can occur after scheduling entity addition but before attachment.
        self.snapshot = self.runtime.get(self.station)

    @callback
    def _changed(self, snapshot):
        if snapshot.token.station == self.station:
            self.snapshot = snapshot
            self.async_write_ha_state()
