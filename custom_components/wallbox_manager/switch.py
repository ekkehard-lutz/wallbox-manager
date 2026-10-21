"""Independent charging permission; only station observations set switch state."""

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
        return {
            **super().extra_state_attributes,
            "state_represents": "confirmed_hardware",
            "enabled_valid_until": None,
            **self.entry.runtime_data.server.control.attributes("ChargingEnabled"),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.observed.watch(
                ("command", self.scope, "charging_enabled"), self.changed
            )
        )

    async def set_permission(self, enabled):
        result = await self.entry.runtime_data.server.control.request(
            "ChargingEnabled", "true" if enabled else "false"
        )
        if result != "confirmed":
            raise HomeAssistantError(f"Charging permission: {result}")

    async def async_turn_on(self, **kwargs):
        await self.set_permission(True)

    async def async_turn_off(self, **kwargs):
        await self.set_permission(False)
