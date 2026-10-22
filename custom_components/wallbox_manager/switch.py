"""Independent charging permission; only station observations set switch state."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import WallboxEntity


async def async_setup_entry(hass, entry, add):
    add(
        [
            Permission(
                entry, "control", entry.runtime_data.state.binding, "charging_enabled"
            ),
            AutomaticWakeUp(
                entry,
                "station",
                (entry.runtime_data.state.binding[0], None, None),
                "auto_wakeup_enabled",
            ),
        ]
    )


class Permission(WallboxEntity, SwitchEntity):
    command = "ChargingEnabled"
    error_label = "Charging permission"
    state_represents = "confirmed_hardware"

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
            "state_represents": self.state_represents,
            "enabled_valid_until": None,
            **self.entry.runtime_data.server.control.attributes(self.command),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.observed.watch(("command", self.scope, self.role), self.changed)
        )

    async def set_permission(self, enabled):
        result = await self.entry.runtime_data.server.control.request(
            self.command, "true" if enabled else "false"
        )
        if result != "confirmed":
            raise HomeAssistantError(f"{self.error_label}: {result}")

    async def async_turn_on(self, **kwargs):
        await self.set_permission(True)

    async def async_turn_off(self, **kwargs):
        await self.set_permission(False)


class AutomaticWakeUp(Permission):
    """Only the station's confirmed boolean is displayed; WBM never times wake-up."""

    command = "AutoWakeUpEnabled"
    error_label = "Automatic vehicle wake-up"
    state_represents = "confirmed_station_setting"
    _attr_icon = "mdi:car-connected"

    @property
    def available(self):
        return (
            self.observed.value(self.key) is not None
            and self.entry.runtime_data.server.control.blocked(self.command) is None
        )
