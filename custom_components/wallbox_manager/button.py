"""Explicitly unavailable takeover action, retaining its public registry identity."""

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
        return False

    async def async_added_to_hass(self):
        await Entity.async_added_to_hass(self)

    async def async_press(self):
        raise HomeAssistantError("Takeover is unavailable in passive v0.4")
