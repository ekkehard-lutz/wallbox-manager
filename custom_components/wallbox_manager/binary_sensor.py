"""Connection and observed session activity."""

import json
from datetime import UTC, datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from .entity import WallboxEntity
from .session import view


async def async_setup_entry(hass, entry, add):
    state = entry.runtime_data.state
    add([Connected(entry, "station", (state.binding[0], None, None), "connected")])
    seen = set()

    def add_active(scope):
        scope = tuple(scope)
        if scope not in seen and state.accepts(scope):
            seen.add(scope)
            add([SessionActive(entry, "session", scope, "active")])

    state.add_active = add_active
    entry.async_on_unload(lambda: setattr(state, "add_active", lambda scope: None))
    if state.session:
        add_active(state.session["scope"])
    prefix = f"{entry.entry_id}:session:"
    for registered in er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    ):
        if registered.domain == "binary_sensor" and registered.unique_id.startswith(
            prefix
        ):
            parts = json.loads(registered.unique_id[len(prefix) :])
            if parts[3] == "active":
                add_active(parts[:3])


class Connected(WallboxEntity, BinarySensorEntity):
    _attr_device_class = "connectivity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.observed.connected


class SessionActive(WallboxEntity, BinarySensorEntity):
    @property
    def available(self):
        return bool(
            self.observed.session
            and tuple(self.observed.session["scope"]) == self.scope
        )

    @property
    def is_on(self):
        return (
            view(self.observed, datetime.now(UTC))["active"] if self.available else None
        )

    @property
    def extra_state_attributes(self):
        session = self.observed.session
        return {
            **super().extra_state_attributes,
            "session_id": session["session_id"] if self.available else None,
            "session_active": self.is_on,
            "external_transaction_id": session["external_transaction_id"]
            if self.available
            else None,
            "start_known": session["started_at"] is not None
            if self.available
            else False,
        }
