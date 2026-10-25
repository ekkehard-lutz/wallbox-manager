"""Small real-HA fixture for independent passive entries and their entities."""

import asyncio
import importlib
import logging
from datetime import timedelta
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform, current_platform

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


def config(station="Wallbox01", port=0):
    return ConfigEntry(
        domain="wallbox_manager",
        title=station,
        data={
            "host": "127.0.0.1",
            "port": port,
            "station_id": station,
            "evse_id": "1",
            "connector_id": "1",
        },
        version=3,
        minor_version=1,
        unique_id=None,
        source="user",
        options={},
        discovery_keys=MappingProxyType({}),
        subentries_data=(),
    )


async def eventually(predicate):  # noqa: ASYNC110
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


@pytest.fixture
async def ha(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    platforms = {}

    async def forward(entry, domains):
        owned = platforms[entry.entry_id] = []
        for domain in domains:
            module = importlib.import_module(
                "custom_components.wallbox_manager." + domain
            )
            platform = EntityPlatform(
                hass=hass,
                logger=logging.getLogger(__name__),
                domain=domain,
                platform_name="wallbox_manager",
                platform=module,
                scan_interval=timedelta(seconds=30),
                entity_namespace=None,
            )
            platform.config_entry = entry
            owned.append(platform)

            def add(entities, platform=platform):
                hass.async_create_task(platform.async_add_entities(list(entities)))

            token = current_platform.set(platform)
            try:
                await module.async_setup_entry(hass, entry, add)
            finally:
                current_platform.reset(token)
        await hass.async_block_till_done()

    async def unload(entry, domains):
        for platform in platforms.pop(entry.entry_id, []):
            await platform.async_reset()
        await entry._async_process_on_unload(hass)
        return True

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forward)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)

    async def install(entry=None):
        entry = entry or config()
        hass.config_entries._entries[entry.entry_id] = entry
        await async_setup_entry(hass, entry)
        return entry

    hass.install = install
    hass.test_platforms = platforms
    yield hass
    for entry in tuple(hass.config_entries._entries.values()):
        if hasattr(entry, "runtime_data"):
            await async_unload_entry(hass, entry)
    await hass.async_stop()


def entities(hass, entry):
    return [
        e
        for platform in hass.test_platforms[entry.entry_id]
        for e in platform.entities.values()
    ]
