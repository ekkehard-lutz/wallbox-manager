"""Stable direction values with localized approximation labels."""

from homeassistant.components.select import SelectEntity

from .control.requests import Direction
from .control_entity import ControlEntity, setup_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_control_entities(
        hass, entry, async_add_entities, lambda c, e, t: [PowerApproximation(c, e, t)]
    )


class PowerApproximation(ControlEntity, SelectEntity):
    _attr_options = [direction.value for direction in Direction]

    def __init__(self, control, entry_id, target):
        super().__init__(control, entry_id, target, "power_approximation")

    @property
    def current_option(self):
        return self.intent.request.direction.value

    def restore_value(self, value):
        self.control.restore(self.target, direction=Direction(value))

    async def async_select_option(self, option):
        await self.control.change(self.target, direction=Direction(option))
