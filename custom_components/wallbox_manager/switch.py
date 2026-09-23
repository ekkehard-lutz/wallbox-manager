"""Desired charging permission, not a claim that the charger has stopped."""

from homeassistant.components.switch import SwitchEntity

from .control_entity import ControlEntity, setup_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    setup_control_entities(
        hass, entry, async_add_entities, lambda c, e, t: [ChargingEnabled(c, e, t)]
    )


class ChargingEnabled(ControlEntity, SwitchEntity):
    def __init__(self, control, entry_id, target):
        super().__init__(control, entry_id, target, "charging_enabled")

    @property
    def is_on(self):
        return self.intent.request.allowed

    def restore_value(self, value):
        if value not in ("on", "off"):
            raise ValueError("invalid permission")
        self.control.restore(self.target, allowed=value == "on")

    async def async_turn_on(self, **kwargs):
        await self.control.change(self.target, allowed=True)

    async def async_turn_off(self, **kwargs):
        await self.control.change(self.target, allowed=False)
