"""Explicit station-scoped takeover, retaining the public registry identity."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity

from .entity import WallboxEntity


async def async_setup_entry(hass, entry, add):
    state = entry.runtime_data.state
    add([TakeControl(entry, "station", (state.binding[0], None, None), "take_control")])


class TakeControl(WallboxEntity, ButtonEntity):
    @property
    def available(self):
        return (
            self.entry.runtime_data.server.control.blocked("ControlAuthority") is None
        )

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            **self.entry.runtime_data.server.control.attributes("ControlAuthority"),
        }

    async def async_added_to_hass(self):
        await Entity.async_added_to_hass(self)
        self.async_on_remove(
            self.observed.watch(
                ("command", self.scope, "control_authority"), self.changed
            )
        )

    async def async_press(self):
        result = await self.entry.runtime_data.server.control.request(
            "ControlAuthority", "OCPP"
        )
        if result != "confirmed":
            raise HomeAssistantError(f"Takeover: {result}")
