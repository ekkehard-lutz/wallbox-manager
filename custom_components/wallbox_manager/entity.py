"""Stable public identities and thin, direct observation/settings entities."""

import json

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


def unique_id(entry_id, family, scope, role, projection=None):
    if family == "station":
        return f"{entry_id}:{scope[0]}:{role}"
    parts = [*scope, role]
    if family == "observation":
        parts.append(projection)
    return f"{entry_id}:{family}:" + json.dumps(parts, separators=(",", ":"))


def device_info(entry, state):
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}:{state.binding[0]}")},
        name=state.binding[0],
        **state.identity,
    )


class WallboxEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, family, scope, role):
        self.entry, self.observed = entry, entry.runtime_data.state
        self.family, self.scope, self.role = family, tuple(scope), role
        self.key = (family, self.scope, role)
        self._attr_unique_id = unique_id(entry.entry_id, family, scope, role)
        self._attr_translation_key = "session_" + role if family == "session" else role
        label = (
            "Station"
            if scope[1] is None
            else f"EVSE {scope[1]}" + (f" / {scope[2]}" if scope[2] else "")
        )
        self._attr_translation_placeholders = {"scope": label}

    @property
    def device_info(self):
        return device_info(self.entry, self.observed)

    @property
    def available(self):
        return self.observed.connected and self.observed.value(self.key) is not None

    @property
    def extra_state_attributes(self):
        station, evse, connector = self.scope
        target = json.dumps(
            [self.entry.entry_id, *self.observed.binding], separators=(",", ":")
        )
        attrs = {
            "wallbox_manager_role": "session_" + self.role
            if self.family == "session"
            else self.role,
            "wallbox_manager_entry": self.entry.entry_id,
            "station_id": station,
            "evse_id": evse,
            "connector_id": connector,
            "wallbox_manager_scope": "connector"
            if connector
            else "evse"
            if evse
            else "station",
            "runtime_incarnation": self.observed.runtime_id,
            "connected": self.observed.connected,
        }
        attrs["wallbox_manager_target" if connector else "wallbox_manager_targets"] = (
            target if connector else [target]
        )
        if observation := self.observed.observations.get(self.key):
            attrs.update(
                source=observation.source,
                connection_generation=observation.generation[0],
                boot_generation=observation.generation[1],
            )
            # Acquisition times stay internal; identical reads do not change
            # Recorder-significant attributes. Compatibility deadlines are inert.
            attrs["valid_until"] = None
        return attrs

    def changed(self):
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.observed.watch(self.key, self.changed))


class SettingEntity(WallboxEntity):
    def __init__(self, entry, role):
        super().__init__(entry, "control", entry.runtime_data.state.binding, role)
        self.settings = entry.runtime_data.settings

    @property
    def available(self):
        return True

    @property
    def extra_state_attributes(self):
        grid = self.entry.runtime_data.grid
        return {
            **super().extra_state_attributes,
            "state_represents": "draft"
            if self.role in ("soll_power", "grid_start_delay", "grid_duration")
            else "desired_intent",
            **grid.attributes(),
        }

    async def async_added_to_hass(self):
        # Settings never subscribe to telemetry or generation publications.
        await Entity.async_added_to_hass(self)
        self.async_on_remove(self.settings.watch(self.role, self.async_write_ha_state))
        self.async_on_remove(
            self.observed.watch(("grid", self.scope, self.role), self.changed)
        )
