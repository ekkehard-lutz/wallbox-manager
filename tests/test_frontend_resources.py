"""Bundled frontend uses HA's public static-path and extra-module APIs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

from custom_components.wallbox_manager.frontend import PATH, async_setup_assets


async def test_assets_registered_once_without_lovelace_storage_edits():
    hass = SimpleNamespace(
        config=SimpleNamespace(components={"frontend"}),
        data={DATA_EXTRA_MODULE_URL: set()},
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        async_add_executor_job=AsyncMock(side_effect=lambda fn: fn()),
    )
    await async_setup_assets(hass)
    await async_setup_assets(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    config = hass.http.async_register_static_paths.call_args.args[0][0]
    assert config.url_path == PATH
    assert config.path.endswith(
        "custom_components/wallbox_manager/www/wallbox-manager-card.js"
    )
    assert config.cache_headers is False
    assert len(hass.data[DATA_EXTRA_MODULE_URL]) == 1
    url = next(iter(hass.data[DATA_EXTRA_MODULE_URL]))
    assert url.startswith(PATH + "?v=")
    assert len(url.split("?v=")[1]) == 12


async def test_headless_ha_needs_no_http_or_dashboard():
    await async_setup_assets(SimpleNamespace(config=SimpleNamespace(components=set())))
