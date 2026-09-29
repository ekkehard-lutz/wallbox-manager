"""Exercise registration with real HA Lovelace storage and YAML collections."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import (
    ResourceStorageCollection,
    ResourceYAMLCollection,
)
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant

from custom_components.wallbox_manager.frontend import KEY, PATH, async_setup_assets


@pytest.fixture
async def frontend(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    try:
        yield hass
    finally:
        await hass.async_stop()


def initialized(hass):
    resources = ResourceStorageCollection(
        hass, SimpleNamespace(async_load=AsyncMock(return_value={}))
    )
    hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    return resources


def resource(resources):
    items = resources.async_items()
    assert len(items) == 1
    assert items[0]["type"] == "module"
    assert items[0]["url"].startswith(PATH + "?v=")
    assert len(items[0]["url"].split("?v=")[1]) == 12
    return items[0]


async def test_first_repeated_concurrent_and_reload_setup(frontend):
    resources = initialized(frontend)
    await asyncio.gather(*(async_setup_assets(frontend) for _ in range(3)))
    first = resource(resources).copy()
    with patch.object(
        resources, "async_update_item", wraps=resources.async_update_item
    ) as update:
        await asyncio.gather(*(async_setup_assets(frontend) for _ in range(3)))
        await async_setup_assets(frontend)  # Entry unload retains shared state.
        update.assert_not_called()
    assert resource(resources) == first
    frontend.http.async_register_static_paths.assert_awaited_once()
    config = frontend.http.async_register_static_paths.call_args.args[0][0]
    assert config.url_path == PATH
    assert config.cache_headers is False


@pytest.mark.parametrize(
    "url,kind",
    [
        (PATH, "module"),
        (PATH + "?v=old", "module"),
        (PATH, "js"),
        (PATH + "?v=old", "css"),
    ],
)
async def test_existing_resource_updated_in_place(frontend, url, kind):
    resources = initialized(frontend)
    old = await resources.async_create_item({"url": url, "res_type": kind})
    await async_setup_assets(frontend)
    assert resource(resources)["id"] == old["id"]


async def test_duplicates_removed_and_unrelated_preserved(frontend):
    resources = initialized(frontend)
    unrelated = []
    for url in (
        "https://example.org" + PATH,
        "//example.org" + PATH,
        "/local/wallbox-manager-card.js",
        "/other.js",
    ):
        unrelated.append(
            await resources.async_create_item({"url": url, "res_type": "module"})
        )
    originals = []
    for url in (PATH, PATH + "?v=old", PATH + "?v=older"):
        originals.append(
            await resources.async_create_item({"url": url, "res_type": "js"})
        )
    await async_setup_assets(frontend)
    items = resources.async_items()
    assert len(items) == len(unrelated) + 1
    assert all(item in items for item in unrelated)
    keeper = next(item for item in items if item["id"] == originals[0]["id"])
    assert keeper["type"] == "module"
    assert keeper["url"].startswith(PATH + "?v=")


async def test_content_change_updates_same_resource(frontend, monkeypatch):
    resources = initialized(frontend)
    await async_setup_assets(frontend)
    before = resource(resources).copy()
    monkeypatch.setattr(
        "custom_components.wallbox_manager.frontend.Path.read_bytes",
        lambda _: b"changed module",
    )
    await async_setup_assets(frontend)
    after = resource(resources)
    assert after["id"] == before["id"]
    assert after["url"] != before["url"]
    assert (
        after["url"]
        == PATH + "?v=" + hashlib.sha256(b"changed module").hexdigest()[:12]
    )
    frontend.http.async_register_static_paths.assert_awaited_once()


async def test_late_lovelace_initialization(frontend):
    await async_setup_assets(frontend)
    frontend.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "frontend"})
    await frontend.async_block_till_done()
    resources = initialized(frontend)
    frontend.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "lovelace"})
    await frontend.async_block_till_done()
    resource(resources)
    frontend.http.async_register_static_paths.assert_awaited_once()


async def test_late_http_initialization(frontend):
    http = frontend.http
    frontend.http = None
    resources = initialized(frontend)
    await async_setup_assets(frontend)
    assert not resources.async_items()
    frontend.http = http
    frontend.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "http"})
    await frontend.async_block_till_done()
    resource(resources)


async def test_restart_loads_persisted_resource_without_duplicate(frontend):
    resources = initialized(frontend)
    await async_setup_assets(frontend)
    before = resource(resources).copy()
    await resources.store.async_save({"items": resources.async_items()})
    frontend.data[KEY]["unsubscribe"]()
    del frontend.data[KEY]
    restarted = initialized(frontend)  # Fresh, unloaded collection reads HA storage.
    await async_setup_assets(frontend)
    assert resource(restarted) == before


@pytest.mark.parametrize("yaml", [True, False])
async def test_non_mutable_resources_remain_untouched(frontend, caplog, yaml):
    items = [{"url": PATH + "?v=pinned", "type": "js"}]
    resources = (
        ResourceYAMLCollection(items.copy())
        if yaml
        else SimpleNamespace(data=items.copy())
    )
    frontend.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    await async_setup_assets(frontend)
    assert resources.data == items
    assert "cannot automatically register" in caplog.text
    frontend.http.async_register_static_paths.assert_awaited_once()


async def test_old_static_sentinel_is_reused(frontend):
    resources = initialized(frontend)
    frontend.data[KEY] = True
    await async_setup_assets(frontend)
    frontend.http.async_register_static_paths.assert_not_awaited()
    resource(resources)
