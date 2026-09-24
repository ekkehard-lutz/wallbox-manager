"""Serve the bundled card and register a supported HA extra frontend module."""

import hashlib
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

PATH = "/wallbox_manager/wallbox-manager-card.js"


async def async_setup_assets(hass):
    if "frontend" not in hass.config.components:
        return  # Headless HA has no frontend to register.
    if hass.data.get("wallbox_manager_frontend"):
        return
    path = Path(__file__).parent / "www" / "wallbox-manager-card.js"
    digest = await hass.async_add_executor_job(
        lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    )
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PATH, str(path), False)]
    )
    add_extra_js_url(hass, f"{PATH}?v={digest}")
    hass.data["wallbox_manager_frontend"] = True
