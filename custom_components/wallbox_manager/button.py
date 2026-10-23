"""Explicit station-scoped takeover, retaining the public registry identity."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from .entity import SettingEntity, WallboxEntity, unique_id


async def async_setup_entry(hass, entry, add):
    state = entry.runtime_data.state
    # Retire only this integration's exact legacy button, not other devices/entities.
    registry = er.async_get(hass)
    legacy = registry.async_get_entity_id(
        "button",
        "wallbox_manager",
        unique_id(
            entry.entry_id, "station", (state.binding[0], None, None), "wake_up_vehicle"
        ),
    )
    if legacy and registry.async_get(legacy).config_entry_id == entry.entry_id:
        registry.async_remove(legacy)
    add(
        [
            TakeControl(
                entry, "station", (state.binding[0], None, None), "take_control"
            ),
            GridSet(entry, "grid_set"),
        ]
    )


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


class GridSet(SettingEntity, ButtonEntity):
    """Durable connector-scoped approval, independent of station eligibility."""

    async def async_press(self):
        try:
            await self.settings.approve_grid()
        except (ValueError, TypeError, OSError) as exc:
            raise HomeAssistantError(f"NETZ SET: {exc}") from exc
