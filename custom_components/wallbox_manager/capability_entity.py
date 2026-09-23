"""Stable, read-only projections of individually resolved capabilities."""

import json
from fractions import Fraction

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from .core.models import ConnectorId, EvseId, StationId
from .entity import StationEntity


@callback
def setup_capability_entities(hass, entry, add_entities):
    runtime = entry.runtime_data.state
    source = entry.runtime_data.control.capability_source
    seen = set()
    prefix = f"{entry.entry_id}:capability:"

    def add(scope, key):
        identity = (scope, key)
        if identity not in seen:
            seen.add(identity)
            add_entities(
                [CapabilitySensor(runtime, source, entry.entry_id, scope, key)]
            )

    @callback
    def changed(snapshot):
        for target in snapshot.connectors:
            capabilities = source.resolved(target)
            counts = next(
                (c.value for c in capabilities if c.key == "supported_phases"), None
            )
            for capability in capabilities:
                if capability.key.startswith("maximum_current_") and (
                    counts is None or int(capability.key[-1]) not in counts
                ):
                    continue
                if capability.value is not None:
                    add(capability.scope, capability.key)

    entry.async_on_unload(runtime.subscribe(changed))
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if entity.unique_id.startswith(prefix):
            try:
                station, evse, connector, key = json.loads(
                    entity.unique_id[len(prefix) :]
                )
                scope = EvseId(StationId(station), evse)
                if connector is not None:
                    scope = ConnectorId(scope, connector)
                add(scope, key)
            except ValueError, TypeError:
                continue
    for snapshot in runtime.stations:
        changed(snapshot)


class CapabilitySensor(StationEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, source, entry_id, scope, key):
        super().__init__(runtime, entry_id, scope.station, key)
        self.source, self.scope, self.key = source, scope, key
        evse = scope.evse if isinstance(scope, ConnectorId) else scope
        connector = scope.value if isinstance(scope, ConnectorId) else None
        self._attr_unique_id = f"{entry_id}:capability:" + json.dumps(
            [scope.station.value, evse.value, connector, key], separators=(",", ":")
        )
        self._attr_translation_key = key
        self._attr_translation_placeholders = {
            "scope": f"EVSE {evse.value}" + (f" / {connector}" if connector else "")
        }
        if "current" in key:
            self._attr_native_unit_of_measurement = "A"

    @property
    def capability(self):
        if self.snapshot:
            for target in self.snapshot.connectors:
                capabilities = self.source.resolved(target)
                counts = next(
                    (c.value for c in capabilities if c.key == "supported_phases"), None
                )
                if self.key.startswith("maximum_current_") and (
                    counts is None or int(self.key[-1]) not in counts
                ):
                    continue
                for capability in capabilities:
                    if capability.scope == self.scope and capability.key == self.key:
                        return capability
        return None

    @property
    def available(self):
        return bool(
            self.snapshot
            and self.snapshot.connected
            and self.capability
            and self.capability.value is not None
        )

    @property
    def native_value(self):
        capability = self.capability
        if capability is None:
            return None
        value = capability.value
        if isinstance(value, tuple):
            return ",".join(str(n) for n in value)
        if isinstance(value, Fraction):
            return float(value)
        return value

    @property
    def extra_state_attributes(self):
        c = self.capability
        return {
            **super().extra_state_attributes,
            "source": c.evidence.source if c else None,
            "evidence": c.evidence.state.value if c else "unknown",
        }
