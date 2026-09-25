"""Stable direction values with localized approximation labels."""

from homeassistant.components.select import SelectEntity

from .control.requests import Direction
from .control_entity import ControlEntity, setup_control_entities


async def async_setup_entry(hass, entry, async_add_entities):
    if hasattr(entry.runtime_data.control, "ownership"):
        async_add_entities(
            [ActiveWallbox(entry.runtime_data.control.ownership, entry.entry_id)]
        )
    setup_control_entities(
        hass,
        entry,
        async_add_entities,
        lambda c, e, t: (
            [PowerApproximation(c, e, t)]
            + (
                [ChargingProfile(c, e, t), PVApproximation(c, e, t)]
                if hasattr(c, "profiles")
                else []
            )
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
    _attr_options = ["NETZ", "PV_SURPLUS"]

    def __init__(self, control, entry_id, target):
        super().__init__(control, entry_id, target, "charging_profile")

    @property
    def current_option(self):
        return self.control.profiles.setting(self.target)["profile"]

    def restore_state(self, previous):
        pass  # Entry-owned profile storage is authoritative.

    async def async_select_option(self, option):
        await self.control.profiles.select(self.target, option)


class ActiveWallbox(SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "active_wallbox"
    _attr_should_poll = False

    def __init__(self, ownership, entry_id):
        self.ownership = ownership
        self.entry_id = entry_id
        self._attr_unique_id = f"{entry_id}:active_wallbox"

    @property
    def options(self):
        return list(self.ownership.inventory())

    @property
    def current_option(self):
        return self.ownership.active_wallbox

    @property
    def extra_state_attributes(self):
        return {
            "wallbox_manager_role": "active_wallbox",
            "wallbox_manager_entry": self.entry_id,
            "wallboxes": self.ownership.inventory(),
            "active_wallbox": self.ownership.active_wallbox,
            "ownership_status": self.ownership.status,
            "profile_control_ready": self.ownership.ready,
            "transition_pending": self.ownership.transition,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.ownership.subscribe(self.async_write_ha_state))

    async def async_select_option(self, option):
        from homeassistant.exceptions import HomeAssistantError

        result = await self.ownership.activate(option)
        if result.status.value != "applied":
            raise HomeAssistantError(self.ownership.status)


class PVApproximation(ControlEntity, SelectEntity):
    _attr_options = [Direction.UP.value, Direction.DOWN.value]

    def __init__(self, control, entry_id, target):
        super().__init__(control, entry_id, target, "pv_approximation")

    @property
    def current_option(self):
        return self.control.profiles.setting(self.target)["approximation"]

    def restore_state(self, previous):
        pass

    async def async_select_option(self, option):
        await self.control.profiles.set_value(self.target, "approximation", option)
