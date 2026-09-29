"""Optional relative duration entities for NETZ profiles."""

from homeassistant.components.text import TextEntity

from .control_entity import ControlEntity, setup_control_entities
from .grid_timing import duration_text


async def async_setup_entry(hass, entry, async_add_entities):
    setup_control_entities(
        hass,
        entry,
        async_add_entities,
        lambda c, e, t: [
            ProfileDuration(c, e, t, key)
            for key in ("grid_start_delay", "grid_duration")
        ],
    )


class ProfileDuration(ControlEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255
    _attr_pattern = r"^(?:[0-9]{2,}:[0-5][0-9])?$"

    @property
    def native_value(self):
        return duration_text(self.control.profiles.setting(self.target).get(self.key))

    def restore_state(self, previous):
        pass  # Profile storage is authoritative.

    async def async_set_value(self, value):
        await self.control.profiles.set_value(self.target, self.key, value)
