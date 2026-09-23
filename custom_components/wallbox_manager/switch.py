"""Confirmed hardware permission; requests never become persistent desired state."""

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
        return self.control.runtime.enabled(self.target)

    @property
    def available(self):
        state = self.control.runtime.get(self.target.station)
        return bool(state and state.connected)

    @property
    def extra_state_attributes(self):
        observation = self.control.runtime.enabled_observation(self.target)
        return {
            **super().extra_state_attributes,
            "state_represents": "confirmed_hardware",
            "enabled_observed_at": observation.observed_at.isoformat()
            if observation
            else None,
            "enabled_valid_until": observation.valid_until.isoformat()
            if observation
            else None,
        }

    def restore_state(self, previous):
        """Ignore old desired permission, including legacy ON restore records."""

    async def async_turn_on(self, **kwargs):
        await self.control.request_enabled(self.target, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.control.request_enabled(self.target, False)
        self.async_write_ha_state()
