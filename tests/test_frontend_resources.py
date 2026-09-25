"""Real HA frontend managers and Lovelace collections, including late setup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import (
    ResourceStorageCollection,
    ResourceYAMLCollection,
)
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant

from custom_components.wallbox_manager.frontend import PATH, async_setup_assets


@pytest.fixture
async def frontend(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    try:
        yield hass
    finally:
        await hass.async_stop()


def initialized(hass, *, yaml=False):
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
    resources = (
        ResourceYAMLCollection([])
        if yaml
        else ResourceStorageCollection(
            hass, SimpleNamespace(async_load=AsyncMock(return_value={}))
        )
    )
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    return resources


async def test_assets_and_resource_registered_once_under_concurrent_setup(frontend):
    resources = initialized(frontend)
    await asyncio.gather(*(async_setup_assets(frontend) for _ in range(3)))
    frontend.http.async_register_static_paths.assert_awaited_once()
    config = frontend.http.async_register_static_paths.call_args.args[0][0]
    assert config.url_path == PATH
    assert config.cache_headers is False
    urls = frontend.data[DATA_EXTRA_MODULE_URL].urls
    assert len(urls) == 1
    url = next(iter(urls))
    assert url.startswith(PATH + "?v=")
    assert len(url.split("?v=")[1]) == 12
    assert [(item["url"], item["type"]) for item in resources.async_items()] == [
        (url, "module")
    ]


async def test_headless_then_late_frontend_and_lovelace(frontend):
    await async_setup_assets(frontend)
    frontend.http.async_register_static_paths.assert_not_awaited()
    frontend.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *args: None, [])
    frontend.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "frontend"})
    await frontend.async_block_till_done()
    assert len(frontend.data[DATA_EXTRA_MODULE_URL].urls) == 1
    resources = ResourceStorageCollection(
        frontend, SimpleNamespace(async_load=AsyncMock(return_value={}))
    )
    frontend.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    frontend.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "lovelace"})
    await frontend.async_block_till_done()
    assert len(resources.async_items()) == 1
    frontend.http.async_register_static_paths.assert_awaited_once()


async def test_existing_manual_resource_is_reused_and_updated(frontend):
    resources = initialized(frontend)
    manual = await resources.async_create_item({"url": PATH, "res_type": "js"})
    await async_setup_assets(frontend)
    items = resources.async_items()
    assert len(items) == 1
    assert items[0]["id"] == manual["id"]
    assert items[0]["type"] == "module"
    assert items[0]["url"].startswith(PATH + "?v=")


async def test_yaml_resources_remain_untouched(frontend):
    resources = initialized(frontend, yaml=True)
    await async_setup_assets(frontend)
    assert resources.async_items() == []
    assert len(frontend.data[DATA_EXTRA_MODULE_URL].urls) == 1


async def test_beta1_in_memory_registration_is_upgraded_without_duplicate_route(
    frontend,
):
    resources = initialized(frontend)
    frontend.data["wallbox_manager_frontend"] = True
    await async_setup_assets(frontend)
    frontend.http.async_register_static_paths.assert_not_awaited()
    assert len(resources.async_items()) == 1
    assert len(frontend.data[DATA_EXTRA_MODULE_URL].urls) == 1
