"""Serve the bundled card and register it in Lovelace's resource collection."""

import asyncio
import hashlib
import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import callback

PATH = "/wallbox_manager/wallbox-manager-card.js"
KEY = "wallbox_manager_frontend"
_LOGGER = logging.getLogger(__name__)


async def async_setup_assets(hass):
    """Reconcile the shared route and resource on setup, reload or late startup."""
    if not isinstance(hass.data.get(KEY), dict):
        registered = hass.data.get(KEY) is True  # beta.1's in-memory sentinel
        state = hass.data[KEY] = {
            "lock": asyncio.Lock(),
            "static_registered": registered,
        }

        @callback
        def loaded(event):
            if event.data.get("component") in ("http", "frontend", "lovelace"):
                hass.async_create_task(async_setup_assets(hass))

        state["unsubscribe"] = hass.bus.async_listen(EVENT_COMPONENT_LOADED, loaded)
    state = hass.data[KEY]
    async with state["lock"]:
        if not getattr(hass, "http", None):
            return  # Component-loaded events retry when HTTP becomes available.
        path = Path(__file__).parent / "www" / "wallbox-manager-card.js"
        if not state["static_registered"]:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(PATH, str(path), False)]
            )
            state["static_registered"] = True
        lovelace = hass.data.get(LOVELACE_DATA)
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            return  # The Lovelace component-loaded event retries registration.
        if not isinstance(resources, ResourceStorageCollection):
            _LOGGER.warning(
                "Wallbox Manager cannot automatically register its card in "
                "non-storage Lovelace resources; configure %s as a module in "
                "your YAML resources (see docs/frontend-registration.md)",
                PATH,
            )
            return
        digest = await hass.async_add_executor_job(
            lambda: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        )
        url = f"{PATH}?v={digest}"
        await resources.async_get_info()
        matching = [
            item for item in resources.async_items() if bundled_url(item["url"])
        ]
        if not matching:
            await resources.async_create_item({"url": url, "res_type": "module"})
            return
        keeper, *duplicates = matching
        if keeper["url"] != url or keeper["type"] != "module":
            await resources.async_update_item(
                keeper["id"], {"url": url, "res_type": "module"}
            )
        for item in duplicates:
            await resources.async_delete_item(item["id"])


def bundled_url(url):
    """Only the integration-owned local route, never third-party card resources."""
    parsed = urlsplit(url)
    return not parsed.scheme and not parsed.netloc and parsed.path == PATH
