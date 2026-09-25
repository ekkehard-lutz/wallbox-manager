"""Register the bundled module with the frontend and Lovelace resource loader."""

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import callback

PATH = "/wallbox_manager/wallbox-manager-card.js"
KEY = "wallbox_manager_frontend"


async def async_setup_assets(hass):
    # Keep a listener even when HA loads frontend/Lovelace after this integration.
    if not isinstance(hass.data.get(KEY), dict):
        registered = hass.data.get(KEY) is True  # beta.1's in-memory sentinel
        state = hass.data[KEY] = {
            "lock": asyncio.Lock(),
            "url": None,
            "static_registered": registered,
        }

        @callback
        def loaded(event):
            if event.data.get("component") in ("frontend", "lovelace"):
                hass.async_create_task(async_setup_assets(hass))

        state["unsubscribe"] = hass.bus.async_listen(EVENT_COMPONENT_LOADED, loaded)
    state = hass.data[KEY]
    async with state["lock"]:
        if DATA_EXTRA_MODULE_URL not in hass.data:
            return  # Headless or not yet initialized; component event retries setup.
        if state["url"] is None:
            path = Path(__file__).parent / "www" / "wallbox-manager-card.js"
            digest = await hass.async_add_executor_job(
                lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            )
            if not state["static_registered"]:
                await hass.http.async_register_static_paths(
                    [StaticPathConfig(PATH, str(path), False)]
                )
                state["static_registered"] = True
            state["url"] = f"{PATH}?v={digest}"
        # Public HA API registers an ES module (es5=False), including YAML dashboards.
        add_extra_js_url(hass, state["url"])
        lovelace = hass.data.get(LOVELACE_DATA)
        resources = getattr(lovelace, "resources", None)
        if resources is None or not hasattr(resources, "async_create_item"):
            return  # YAML resources are user-owned; the frontend module covers them.
        # Use HA's collection API, never edit .storage or dashboard configuration.
        await resources.async_get_info()
        matches = [
            item
            for item in resources.async_items()
            if not urlsplit(item["url"]).netloc and urlsplit(item["url"]).path == PATH
        ]
        if not matches:
            await resources.async_create_item(
                {"res_type": "module", "url": state["url"]}
            )
        else:
            for item in matches:
                if item["url"] != state["url"] or item["type"] != "module":
                    await resources.async_update_item(
                        item["id"], {"res_type": "module", "url": state["url"]}
                    )
