"""Profile and approximation preferences, persisted without execution."""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SettingEntity
from .settings import SELECTS


async def async_setup_entry(hass, entry, add):
    add([SettingSelect(entry, role) for role in SELECTS])


class SettingSelect(SettingEntity, SelectEntity, RestoreEntity):
    def __init__(self, entry, role):
        super().__init__(entry, role)
        self._attr_options = SELECTS[role][1]

    @property
    def current_option(self):
        value = self.settings.get(self.role)
        return value if value in self.options else None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if (
            self.role == "power_approximation"
            and "direction" not in self.settings.profiles.get(self.settings.key, {})
        ):
            previous = await self.async_get_last_state()
            if previous and previous.state in self.options:
                self.settings.values["direction"] = previous.state

    async def async_select_option(self, option):
        await self.settings.set(self.role, option)
