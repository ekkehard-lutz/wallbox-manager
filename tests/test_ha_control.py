"""Real HA entity registration and manual services through the OCPP runtime."""

import logging
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from test_control_runtime import manual as manual
from test_ha_lifecycle import entry
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager import number, select, switch
from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.control_entity import ControlEntity


@pytest.fixture
async def controls(tmp_path, manual):
    control, bound, peer, wire, source, _ = manual
    runtime = bound.adapter.runtime
    runtime._publish(replace(runtime.get(bound.target.station), protocol_version="2.1"))
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
        for domain, module in (
            ("switch", switch),
            ("number", number),
            ("select", select),
        ):
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
        return {e.key: e for p in platforms for e in p.entities.values()}

    async def unload():
        for platform in platforms:
            await platform.async_reset()
        platforms.clear()
        await config._async_process_on_unload(hass)

    entities = await setup()
    try:
        yield hass, entities, manual, setup, unload
    finally:
        await unload()
        await hass.async_stop()


async def test_registered_and_retained_offline(controls):
    hass, entities, manual, setup, unload = controls
    control, bound, peer, _, source, _ = manual
    assert set(entities) == {
        "charging_enabled",
        "allowed_current_1p",
        "allowed_current_2p",
        "allowed_current_3p",
        "desired_charging_power",
        "power_approximation",
        "phase_switch_deviation_pct",
    }
    assert len(er.async_get(hass).entities) == 7
    assert len(dr.async_get(hass).devices) == 1
    assert not peer.requests
    ids = {key: e.unique_id for key, e in entities.items()}
    source.snapshot = None
    bound.adapter.runtime.disconnect(bound.token)
    assert all(e.available for e in entities.values())
    assert all(
        not e.extra_state_attributes["execution_ready"] for e in entities.values()
    )
    await unload()
    entities = await setup()
    assert {key: e.unique_id for key, e in entities.items()} == ids
    assert len(er.async_get(hass).entities) == 7
    assert not peer.requests


async def test_power_permission_direction_and_retention_entities(controls):
    hass, entities, manual, _, _ = controls
    control, bound, peer, _, _, _ = manual
    power = entities["desired_charging_power"]
    enabled = entities["charging_enabled"]
    direction = entities["power_approximation"]
    retention = entities["phase_switch_deviation_pct"]
    assert power.native_unit_of_measurement == "W"
    assert power.native_max_value == 100000 and power.native_step == 100
    assert retention.native_value == 5
    assert retention.native_max_value == 25 and retention.native_step == 1
    await power.async_set_native_value(4000)
    assert not peer.requests  # Permission defaults to false.
    for value in Direction:
        await direction.async_select_option(value.value)
        assert control.intent(bound.target).request.direction == value
    await direction.async_select_option("nearest")
    await enabled.async_turn_on()
    assert enabled.is_on and power.native_value == 4000
    assert len(peer.requests) == 1
    assert enabled.extra_state_attributes["command_status"] == "applied"
    await enabled.async_turn_off()
    assert not enabled.is_on and power.native_value == 4000
    assert enabled.extra_state_attributes["solver_reason"] is None
    assert enabled.extra_state_attributes["command_status"] == "applied"
    assert len(peer.requests) == 1
    await retention.async_set_native_value(10)
    assert control.intent(bound.target).phase_switch_deviation_pct == 10
    assert hass.states.get(power.entity_id).state == "4000.0"


async def test_ha_restoration_never_sends(controls, monkeypatch):
    _, _, manual, setup, unload = controls
    control, bound, peer, _, _, _ = manual
    await unload()

    async def restored(self):
        return State(
            self.entity_id,
            {
                "charging_enabled": "on",
                "desired_charging_power": "5000",
                "power_approximation": "down",
                "phase_switch_deviation_pct": "7",
                "allowed_current_1p": "30.25",
                "allowed_current_2p": "17.125",
                "allowed_current_3p": "43.5",
            }[self.key],
        )

    monkeypatch.setattr(ControlEntity, "async_get_last_state", restored)
    entities = await setup()
    assert entities["charging_enabled"].is_on
    assert entities["desired_charging_power"].native_value == 5000
    assert control.intent(bound.target).request.direction == Direction.DOWN
    assert control.intent(bound.target).phase_switch_deviation_pct == 7
    assert not peer.requests


async def test_other_known_evse_exists_without_inventing_capabilities(controls):
    hass, _, manual, _, _ = controls
    control, bound, peer, _, _, _ = manual
    from test_control_runtime import measured

    from custom_components.wallbox_manager.core.models import (
        ConnectorId,
        EvseId,
        StationId,
    )

    runtime = bound.adapter.runtime
    evse = EvseId(bound.target.station, "2")
    measured(runtime, bound.token, evse)
    await hass.async_block_till_done()
    assert len(er.async_get(hass).entities) == 7
    target = ConnectorId(evse, "5")
    measured(runtime, bound.token, target)
    await hass.async_block_till_done()
    assert len(er.async_get(hass).entities) == 14
    await control.change(target, target_w=4000, allowed=True)
    assert control.intent(target).status == "capabilities_unavailable"
    other = runtime.connect(
        StationId("legacy"), protocol="ocpp", protocol_version="1.6"
    )
    measured(runtime, other, EvseId(other.station, "1"))
    await hass.async_block_till_done()
    assert len(er.async_get(hass).entities) == 14
    assert not peer.requests


async def test_fractional_limits_are_independent_desired_state(controls):
    _, entities, manual, _, _ = controls
    control, bound, peer, _, _, _ = manual
    for count, value in ((1, 40.125), (2, 17.375), (3, 0)):
        entity = entities[f"allowed_current_{count}p"]
        assert entity.native_min_value == 0
        assert entity.native_unit_of_measurement == "A"
        await entity.async_set_native_value(value)
        assert entity.native_value == value
    assert not peer.requests and not peer.permissions
    await control.change(bound.target, target_w=10000, allowed=True)
    assert control.intent(bound.target).solver_result.point.mode.count == 1
    assert control.intent(bound.target).solver_result.point.current_a <= 32
    assert control.intent(bound.target).current_limits[2] > 17


async def test_defaults_persist_exactly_through_ha_reload(controls):
    from datetime import UTC, datetime
    from fractions import Fraction

    from custom_components.wallbox_manager.core.capabilities import (
        CapabilityEvidence,
        EvidenceState,
    )
    from custom_components.wallbox_manager.core.electrical import ElectricalCapability

    hass, entities, manual, setup, unload = controls
    control, bound, peer, _, _, _ = manual
    maximum = Fraction(80, 3)
    proof = CapabilityEvidence(
        EvidenceState.VERIFIED, "test_inventory", datetime.now(UTC)
    )
    control.electrical_capabilities = lambda target: (
        ElectricalCapability(target, "maximum_current", maximum, proof),
    )
    control.initialize_current_limits(bound.target)
    ids = {key: e.unique_id for key, e in entities.items()}
    for count in (1, 2, 3):
        entity = entities[f"allowed_current_{count}p"]
        state = hass.states.get(entity.entity_id)
        assert state.attributes["exact_current_limit_a"] == "80/3"
    await entities["allowed_current_1p"].async_set_native_value(0)
    await unload()
    # Simulate fresh in-memory intent and changed capabilities before HA restores.
    control.intents.clear()
    control._edited.clear()
    maximum = Fraction(40)
    control.initialize_current_limits(bound.target)
    entities = await setup()
    assert control.intent(bound.target).current_limits == {
        1: 0,
        2: Fraction(80, 3),
        3: Fraction(80, 3),
    }
    assert {key: e.unique_id for key, e in entities.items()} == ids
    assert not peer.requests and not peer.permissions


async def test_discovery_during_restore_cannot_replace_saved_or_live_values(
    controls, monkeypatch
):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.capabilities import (
        CapabilityEvidence,
        EvidenceState,
    )
    from custom_components.wallbox_manager.core.electrical import ElectricalCapability

    _, _, manual, setup, unload = controls
    control, bound, peer, _, _, _ = manual
    await unload()
    proof = CapabilityEvidence(
        EvidenceState.VERIFIED, "test_inventory", datetime.now(UTC)
    )
    control.electrical_capabilities = lambda target: (
        ElectricalCapability(target, "maximum_current_1", 20, proof),
        ElectricalCapability(target, "maximum_current_3", 27, proof),
    )

    async def restored(entity):
        if entity.key == "allowed_current_1p":
            control.initialize_current_limits(bound.target)
            await control.change(bound.target, allowed_current_2p=0)
            return State(entity.entity_id, "16")
        if entity.key == "allowed_current_2p":
            return State(entity.entity_id, "18")
        return None

    monkeypatch.setattr(ControlEntity, "async_get_last_state", restored)
    await setup()
    assert control.intent(bound.target).current_limits == {1: 16, 2: 0, 3: 27}
    assert not peer.requests and not peer.permissions
