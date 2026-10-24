"""Canonical timing drafts and revision-fenced interaction for both editors."""

import voluptuous as vol
from homeassistant.components.text import TextEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform

from .entity import SettingEntity
from .settings import TIMES, duration_text


async def async_setup_entry(hass, entry, add):
    entity_platform.async_get_current_platform().async_register_entity_service(
        "edit_timing",
        {
            vol.Required("action"): vol.In(["begin", "update", "end", "renew"]),
            vol.Required("owner"): vol.All(str, vol.Length(min=1, max=128)),
            vol.Required("revision"): vol.All(int, vol.Range(min=0)),
            vol.Required("incarnation"): str,
            vol.Optional("value", default=""): str,
        },
        "async_edit_timing",
    )
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

    async def async_edit_timing(self, action, owner, revision, incarnation, value):
        if incarnation != self.observed.runtime_id:
            raise HomeAssistantError("Superseded timing runtime")
        try:
            await self.settings.edit_timing(self.role, action, owner, revision, value)
        except (ValueError, TypeError, OSError) as exc:
            raise HomeAssistantError(str(exc)) from exc
