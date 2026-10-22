"""Explicit station-scoped takeover, retaining the public registry identity."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity

from .entity import WallboxEntity


async def async_setup_entry(hass, entry, add):
    state = entry.runtime_data.state
    scope = (state.binding[0], None, None)
    add(
        [
            TakeControl(entry, "station", scope, "take_control"),
            WakeUpVehicle(entry, "station", scope, "wake_up_vehicle"),
        ]
    )


class TakeControl(WallboxEntity, ButtonEntity):
    command = "ControlAuthority"
    requested = "OCPP"
    error_label = "Takeover"

    @property
    def available(self):
        return self.entry.runtime_data.server.control.blocked(self.command) is None

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            **self.entry.runtime_data.server.control.attributes(self.command),
        }

    async def async_added_to_hass(self):
        await Entity.async_added_to_hass(self)
        self.async_on_remove(
            self.observed.watch(
                (
                    "command",
                    self.scope,
                    self.entry.runtime_data.server.control.key(self.command)[2],
                ),
                self.changed,
            )
        )

    async def async_press(self):
        result = await self.entry.runtime_data.server.control.request(
            self.command, self.requested
        )
        if result != "confirmed":
            raise HomeAssistantError(f"{self.error_label}: {result}")


class WakeUpVehicle(TakeControl):
    """An explicit CP cycle; success does not assert that charging has started."""

    command = "WakeUpVehicle"
    requested = "1"
    error_label = "Wake-up"
    _attr_icon = "mdi:car-connected"
