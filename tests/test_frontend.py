"""Lovelace resource registration with simulated Home Assistant components."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import EVENT_COMPONENT_LOADED

from custom_components.wallbox_manager.frontend import PATH, async_setup_assets


@pytest.mark.parametrize("late", [False, True])
async def test_frontend_registration_and_resource_deduplication(ha, monkeypatch, late):
    resources = Mock(spec=ResourceStorageCollection)
    resources.async_get_info = AsyncMock()
    resources.async_update_item = AsyncMock()
    resources.async_create_item = AsyncMock()
    resources.async_delete_item = AsyncMock()
    resources.async_items.return_value = [
        {"id": "external", "url": f"https://example.invalid{PATH}"},
        *(
            []
            if late
            else [
                {"id": "primary", "url": f"{PATH}?v=old"},
                {"id": "duplicate", "url": PATH},
            ]
        ),
    ]
    register = AsyncMock()
    if late:
        await async_setup_assets(ha)
        register.assert_not_awaited()
    monkeypatch.setattr(
        ha, "http", SimpleNamespace(async_register_static_paths=register), raising=False
    )
    ha.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)
    if late:
        ha.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "lovelace"})
        await ha.async_block_till_done()
    else:
        await async_setup_assets(ha)

    register.assert_awaited_once()
    [static] = register.await_args.args[0]
    assert static.url_path == PATH
    digest = await ha.async_add_executor_job(
        lambda: hashlib.sha256(Path(static.path).read_bytes()).hexdigest()[:12]
    )
    resource = {"url": f"{PATH}?v={digest}", "res_type": "module"}
    if late:
        resources.async_create_item.assert_awaited_once_with(resource)
        resources.async_update_item.assert_not_awaited()
        resources.async_delete_item.assert_not_awaited()
    else:
        resources.async_update_item.assert_awaited_once_with("primary", resource)
        resources.async_delete_item.assert_awaited_once_with("duplicate")
        resources.async_create_item.assert_not_awaited()
