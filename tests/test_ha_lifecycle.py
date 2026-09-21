"""Minimal HA setup/unload and configuration, without starting a full HA instance."""

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.wallbox_manager import (
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.wallbox_manager.config_flow import WallboxManagerConfigFlow
from custom_components.wallbox_manager.protocols.ocpp.common.transport import (
    CentralSystem,
)


def entry(version=2):
    return ConfigEntry(
        domain="wallbox_manager",
        title="Test",
        data={"host": "127.0.0.1", "port": 0},
        version=version,
        minor_version=1,
        unique_id=None,
        source="user",
        options={},
        discovery_keys=MappingProxyType({}),
        subentries_data=(),
    )


async def test_setup_without_wallbox_and_unload(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    config = entry()
    try:
        assert await async_setup_entry(hass, config)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            config, ("binary_sensor", "sensor")
        )
        server = config.runtime_data.server
        assert config.runtime_data.state.stations == ()
        assert server.port > 0
        assert await async_unload_entry(hass, config)
        assert not server._server.is_serving()
    finally:
        await hass.async_stop()


async def test_bind_error_is_retryable(monkeypatch):
    monkeypatch.setattr(CentralSystem, "start", AsyncMock(side_effect=OSError("busy")))
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda fn, *a: fn(*a))
    )
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry())


async def test_config_flow_validation_and_duplicate_port():
    flow = WallboxManagerConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda *a: [])
    )
    flow._async_abort_entries_match = Mock()
    result = await flow.async_step_user({"host": "127.0.0.1", "port": 9876})
    assert result["type"] == "create_entry"
    assert result["data"] == {"host": "127.0.0.1", "port": 9876}
    flow._async_abort_entries_match.assert_called_once_with({"port": 9876})
    for data in ({"host": "bad", "port": 9876}, {"host": "127.0.0.1", "port": 0}):
        result = await flow.async_step_user(data)
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_listener"


async def test_migrate_scaffold_entry():
    config = SimpleNamespace(version=1, data={})

    def update(e, **kwargs):
        e.version = kwargs["version"]
        e.data = kwargs["data"]

    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
    assert await async_migrate_entry(hass, config)
    assert config.data == {"host": "0.0.0.0", "port": 9000}
    assert config.version == 2


async def test_platform_setup_failure_closes_listener(monkeypatch):
    start, stop = AsyncMock(), AsyncMock()
    monkeypatch.setattr(CentralSystem, "start", start)
    monkeypatch.setattr(CentralSystem, "stop", stop)
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(side_effect=RuntimeError("platform")),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    with pytest.raises(RuntimeError, match="platform"):
        await async_setup_entry(hass, entry())
    stop.assert_awaited_once()
    hass.config_entries.async_unload_platforms.assert_awaited_once()


async def test_failed_platform_unload_keeps_listener_running():
    stop = AsyncMock()
    config = SimpleNamespace(
        runtime_data=SimpleNamespace(server=SimpleNamespace(stop=stop))
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=False)
        )
    )
    assert not await async_unload_entry(hass, config)
    stop.assert_not_awaited()
