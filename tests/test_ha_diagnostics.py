"""Exercise real HA entity/device registries with generic runtime push events."""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import EntityPlatform
from test_ha_lifecycle import entry

from custom_components.wallbox_manager import PLATFORMS, binary_sensor, sensor
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    EvidenceState,
)
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.runtime import Runtime


@pytest.fixture
async def diagnostics(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    platforms = []

    async def setup():
        from custom_components.wallbox_manager.control.capabilities import (
            CapabilityResolver,
        )

        runtime = Runtime()
        config.runtime_data = SimpleNamespace(
            state=runtime,
            control=SimpleNamespace(capability_source=CapabilityResolver(runtime)),
        )
        for domain, module in [("binary_sensor", binary_sensor), ("sensor", sensor)]:
            platform = EntityPlatform(
                hass=hass,
                logger=logging.getLogger(__name__),
                domain=domain,
                platform_name="wallbox_manager",
                platform=module,
                scan_interval=timedelta(seconds=30),
                entity_namespace=None,
            )
            platform.config_entry = config
            platforms.append(platform)

            def add(entities, platform=platform):
                hass.async_create_task(platform.async_add_entities(entities))

            await module.async_setup_entry(hass, config, add)
        await hass.async_block_till_done()
        return config.runtime_data.state

    async def unload():
        for platform in platforms:
            await platform.async_reset()
        platforms.clear()
        await config._async_process_on_unload(hass)
        await hass.async_block_till_done()

    runtime = await setup()
    try:
        yield hass, config, runtime, platforms, setup, unload
    finally:
        await unload()
        await hass.async_stop()


def entities(platforms):
    return {e.translation_key: e for p in platforms for e in p.entities.values()}


async def test_dynamic_diagnostics_and_reload(diagnostics):
    hass, config, runtime, platforms, setup, unload = diagnostics
    registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    assert not registry.devices and not entity_registry.entities
    assert len(runtime._listeners) == 3
    station = StationId("garage")
    token = runtime.connect(station, protocol="ocpp", protocol_version="2.1")
    await hass.async_block_till_done()
    es = entities(platforms)
    assert len(es) == 6
    assert es["connected"].is_on
    assert es["protocol_version"].native_value == "2.1"
    assert es["connection_generation"].native_value == 1
    assert es["boot_generation"].native_value == 0
    assert es["discovery"].native_value == "unknown"
    device = registry.async_get_device(
        {("wallbox_manager", f"{config.entry_id}:garage")}
    )
    assert device is not None
    assert device.manufacturer is None
    ids = {key: e.unique_id for key, e in es.items()}
    registry_ids = set(entity_registry.entities)
    assert all(uid == f"{config.entry_id}:garage:{key}" for key, uid in ids.items())
    token = runtime.boot(
        token,
        StationIdentity(vendor="Vendor", model="Model", firmware="1.2", serial="abc"),
    )
    assert es["boot_generation"].native_value == 1
    device = registry.async_get(device.id)
    assert (
        device.manufacturer,
        device.model,
        device.sw_version,
        device.serial_number,
    ) == ("Vendor", "Model", "1.2", "abc")
    evidence = CapabilityEvidence(
        EvidenceState.DEGRADED, "test", datetime.now(UTC), "timeout"
    )
    runtime.discover(token, discovery=evidence, charging_schedule=evidence)
    assert es["discovery"].native_value == "degraded"
    assert es["discovery"].extra_state_attributes["reason"] == "timeout"
    assert es["revision"].native_value == 3
    assert hass.states.get(es["connected"].entity_id).state == "on"
    runtime.disconnect(token)
    assert hass.states.get(es["connected"].entity_id).state == "off"
    assert all(e.available for e in es.values())
    assert es["protocol_version"].native_value == "2.1"
    assert es["discovery"].extra_state_attributes["reason"] == "disconnected"
    token = runtime.connect(station, protocol="ocpp", protocol_version="1.6")
    await hass.async_block_till_done()
    assert es["connection_generation"].native_value == 2
    assert es["boot_generation"].native_value == 1
    runtime.boot(token, StationIdentity())
    assert es["boot_generation"].native_value == 2
    assert registry.async_get(device.id).manufacturer == "Vendor"
    assert ids == {key: e.unique_id for key, e in entities(platforms).items()}
    assert len(registry.devices) == 1
    assert set(entity_registry.entities) == registry_ids
    old_incarnation = runtime.runtime_id
    await unload()
    assert not runtime._listeners
    runtime = await setup()
    es = entities(platforms)
    assert not es["connected"].is_on and es["connected"].available
    assert all(not e.available for key, e in es.items() if key != "connected")
    assert runtime.runtime_id != old_incarnation
    runtime.connect(station, protocol="ocpp", protocol_version="2.0.1")
    await hass.async_block_till_done()
    assert set(entity_registry.entities) == registry_ids
    assert len(registry.devices) == 1
    assert registry.async_get(device.id).model == "Model"
    assert es["protocol_version"].native_value == "2.0.1"
    await unload()
    assert not runtime._listeners


async def test_multiple_stations_read_only_generic_entities(diagnostics):
    hass, config, runtime, platforms, _, _ = diagnostics
    for name in ["garage", "driveway"]:
        runtime.connect(StationId(name), protocol="ocpp", protocol_version="1.6")
    await hass.async_block_till_done()
    assert len(dr.async_get(hass).devices) == 2
    all_entities = [e for p in platforms for e in p.entities.values()]
    assert len(all_entities) == 12
    assert len({e.unique_id for e in all_entities}) == 12
    assert PLATFORMS == ("binary_sensor", "sensor", "switch", "number", "select")
    for e in all_entities:
        assert not e.should_poll
        assert e.entity_category == EntityCategory.DIAGNOSTIC
        assert e.runtime is runtime
        assert not hasattr(e, "charge_point")
        if isinstance(e, sensor.DiagnosticSensor):
            assert e.native_unit_of_measurement is None
            assert e.state_class is None
            assert e.device_class in (None, sensor.SensorDeviceClass.ENUM)


def test_station_identity_is_scoped_to_listener():
    from custom_components.wallbox_manager.entity import station_identifier

    station = StationId("garage")
    assert station_identifier("listener-a", station) != station_identifier(
        "listener-b", station
    )
