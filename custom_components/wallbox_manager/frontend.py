"""Register one frontend module; migrate obsolete Lovelace resource entries."""

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.frontend import (
    DATA_EXTRA_MODULE_URL,
    add_extra_js_url,
    remove_extra_js_url,
)
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
        path = Path(__file__).parent / "www" / "wallbox-manager-card.js"
        digest = await hass.async_add_executor_job(
            lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        )
        if not state["static_registered"]:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(PATH, str(path), False)]
            )
            state["static_registered"] = True
        url = f"{PATH}?v={digest}"
        manager = hass.data[DATA_EXTRA_MODULE_URL]
        for old_url in tuple(manager.urls):
            if bundled_url(old_url) and old_url != url:
                remove_extra_js_url(hass, old_url)
        if url not in manager.urls:
            add_extra_js_url(hass, url)
        state["url"] = url
        lovelace = hass.data.get(LOVELACE_DATA)
        resources = getattr(lovelace, "resources", None)
        if resources is None or not hasattr(resources, "async_create_item"):
            return  # YAML resources are user-owned; the frontend module covers them.
        # Migration only: the extra module API works with both dashboard modes.
        # Do not leave an independent Lovelace loader racing the global loader.
        await resources.async_get_info()
        for item in tuple(resources.async_items()):
            if bundled_url(item["url"]):
                await resources.async_delete_item(item["id"])


def bundled_url(url):
    """Only the integration-owned local route, never third-party card resources."""
    parsed = urlsplit(url)
    return not parsed.scheme and not parsed.netloc and parsed.path == PATH
