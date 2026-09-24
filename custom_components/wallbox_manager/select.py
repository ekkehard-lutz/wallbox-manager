"""Stable direction values with localized approximation labels."""

from homeassistant.components.select import SelectEntity

from .control.requests import Direction
from .control_entity import ControlEntity, setup_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_control_entities(
        hass,
        entry,
        async_add_entities,
        lambda c, e, t: (
            [PowerApproximation(c, e, t)]
            + ([ChargingProfile(c, e, t)] if hasattr(c, "profiles") else [])
        ),
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


class ChargingProfile(ControlEntity, SelectEntity):
    _attr_options = ["NETZ"]

    def __init__(self, control, entry_id, target):
        super().__init__(control, entry_id, target, "charging_profile")

    @property
    def current_option(self):
        return self.control.profiles.setting(self.target)["profile"]

    def restore_state(self, previous):
        pass  # Entry-owned profile storage is authoritative.

    async def async_select_option(self, option):
        await self.control.profiles.select(self.target, option)
