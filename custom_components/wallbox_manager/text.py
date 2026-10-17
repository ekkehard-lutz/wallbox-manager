"""Optional local duration preferences; no timers are armed."""

from homeassistant.components.text import TextEntity

from .entity import SettingEntity
from .settings import TIMES, duration_text


async def async_setup_entry(hass, entry, add):
    add([Duration(entry, role) for role in TIMES])


class Duration(SettingEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255
    _attr_pattern = r"^(?:[0-9]{2,}:[0-5][0-9])?$"

    @property
    def native_value(self):
        return duration_text(self.settings.get(self.role))

    async def async_set_value(self, value):
        await self.settings.set(self.role, value)
