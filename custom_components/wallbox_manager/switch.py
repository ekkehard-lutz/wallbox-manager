"""Read-only physical permission retains the original switch identity."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import WallboxEntity


async def async_setup_entry(hass, entry, add):
    add(
        [
            Permission(
                entry, "control", entry.runtime_data.state.binding, "charging_enabled"
            )
        ]
    )


class Permission(WallboxEntity, SwitchEntity):
    @property
    def available(self):
        return self.observed.connected

    @property
    def is_on(self):
        return self.observed.value(self.key)

    @property
    def extra_state_attributes(self):
        observation = self.observed.observations.get(self.key)
        return {
            **super().extra_state_attributes,
            "state_represents": "confirmed_hardware",
            "enabled_valid_until": observation.valid_until.isoformat()
            if observation and observation.valid_until
            else None,
            "enabled_observed_at": observation.observed_at.isoformat()
            if observation
            else None,
            "execution_ready": False,
            "execution_blocked_reason": "passive_only",
        }

    async def async_turn_on(self, **kwargs):
        raise HomeAssistantError(
            "Active charging control is unavailable in passive v0.4"
        )

    async def async_turn_off(self, **kwargs):
        raise HomeAssistantError(
            "Active charging control is unavailable in passive v0.4"
        )
