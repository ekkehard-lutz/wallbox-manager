"""Station-scoped HA authority status/action registration and lifecycle."""

import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from test_control_authority import authority as authority
from test_control_runtime import manual as manual
from test_ha_lifecycle import entry
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager import button, sensor
from custom_components.wallbox_manager.core.events import StationIdentity


@pytest.fixture
async def authority_entities(tmp_path, authority):
    control, bound, _, _ = authority
    runtime = bound.adapter.runtime
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    config.runtime_data = SimpleNamespace(state=runtime, control=control)
    hass.config_entries._entries[config.entry_id] = config
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    platforms = []

    async def setup():
        for domain, module in (("sensor", sensor), ("button", button)):
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
        return {
            e.translation_key: e
            for p in platforms
            for e in p.entities.values()
            if isinstance(e, (sensor.AuthoritySensor, button.TakeControlButton))
        }

    async def unload():
        for platform in platforms:
            await platform.async_reset()
        platforms.clear()
        await config._async_process_on_unload(hass)

    entities = await setup()
    try:
        yield hass, entities, authority, setup, unload
    finally:
        await unload()
        await hass.async_stop()


async def test_one_way_button_confirms_and_applies_saved_intent(authority_entities):
    hass, entities, (control, bound, peer, _), _, _ = authority_entities
    assert set(entities) == {"control_authority", "take_control"}
    action = entities["take_control"]
    status = entities["control_authority"]
    assert status.native_value == "local"
    assert status.options == ["unknown", "local", "remote"]
    assert action.available
    assert action.unique_id.endswith(f":{bound.target.station.value}:take_control")
    control.restore(bound.target, target_w=2300, allowed=True)
    await action.async_press()
    assert status.native_value == "remote"
    assert action.extra_state_attributes["takeover_status"] == "applied"
    assert peer.operations == [
        "authority_set",
        "authority_get",
        "profile",
        "permission",
    ]
    assert hass.states.get(status.entity_id).state == "remote"
    buttons = [e for e in er.async_get(hass).entities.values() if e.domain == "button"]
    assert len(buttons) == 1 and buttons[0].unique_id == action.unique_id


async def test_authority_entities_keep_ids_across_boot_offline_reload(
    authority_entities,
):
    _, entities, (_, bound, peer, _), setup, unload = authority_entities
    ids = {key: e.unique_id for key, e in entities.items()}
    live = bound.adapter
    live.token = live.runtime.boot(live.token, StationIdentity("Generic", "Generic"))
    assert entities["control_authority"].native_value == "unknown"
    assert not entities["take_control"].available
    live.runtime.disconnect(live.token)
    assert all(not e.available for e in entities.values())
    await unload()
    entities = await setup()
    assert {key: e.unique_id for key, e in entities.items()} == ids
    assert all(not e.available for e in entities.values())
    assert not peer.operations
