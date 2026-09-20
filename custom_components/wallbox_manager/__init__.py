"""Wallbox Manager integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

type WallboxManagerConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallboxManagerConfigEntry,
) -> bool:
    """Set up Wallbox Manager from a config entry."""
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: WallboxManagerConfigEntry,
) -> bool:
    """Unload a Wallbox Manager config entry."""
    return True
