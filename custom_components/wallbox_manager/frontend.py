"""Serve the familiar card at its existing local resource identity."""

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import callback

PATH = "/wallbox_manager/wallbox-manager-card.js"
KEY = "wallbox_manager_frontend"


async def async_setup_assets(hass):
    if not isinstance(hass.data.get(KEY), dict):
        state = hass.data[KEY] = {"lock": asyncio.Lock(), "registered": False}

        @callback
        def loaded(event):
            if event.data.get("component") in ("http", "frontend", "lovelace"):
                hass.async_create_task(async_setup_assets(hass))

        state["unsubscribe"] = hass.bus.async_listen(EVENT_COMPONENT_LOADED, loaded)
    state = hass.data[KEY]
    async with state["lock"]:
        if not getattr(hass, "http", None):
            return
        path = Path(__file__).parent / "www" / "wallbox-manager-card.js"
        if not state["registered"]:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(PATH, str(path), False)]
            )
            state["registered"] = True
        resources = getattr(hass.data.get(LOVELACE_DATA), "resources", None)
        if not isinstance(resources, ResourceStorageCollection):
            return
        digest = await hass.async_add_executor_job(
            lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        )
        await resources.async_get_info()
        matches = [
            item
            for item in resources.async_items()
            if urlsplit(item["url"]).path == PATH and not urlsplit(item["url"]).netloc
        ]
        if matches:
            await resources.async_update_item(
                matches[0]["id"], {"url": f"{PATH}?v={digest}", "res_type": "module"}
            )
            for duplicate in matches[1:]:
                await resources.async_delete_item(duplicate["id"])
        else:
            await resources.async_create_item(
                {"url": f"{PATH}?v={digest}", "res_type": "module"}
            )
