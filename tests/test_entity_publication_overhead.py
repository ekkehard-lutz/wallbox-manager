"""Semantic publication, configuration and timer regressions on real HA entities."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from test_control_runtime import manual as manual
from test_ha_control import controls as controls
from test_ha_diagnostics import diagnostics as diagnostics
from test_ha_observations import observation, operational
from test_ha_sessions import sessions
from test_ocpp21_control import connected as connected
from test_pv_beta12 import authority
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager import profiles, pv_soc
from custom_components.wallbox_manager.control_entity import ControlEntity
from custom_components.wallbox_manager.core.authority import ControlAuthority
from custom_components.wallbox_manager.core.enabled import EnabledObservation
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import (
    EvseId,
    PhaseMode,
    PhysicalPhaseObservation,
    StationId,
)
from custom_components.wallbox_manager.core.sessions import (
    SessionEvent,
    SessionEventKind,
)
from custom_components.wallbox_manager.core.telemetry import Quantity, State
from custom_components.wallbox_manager.profiles import target_key


@pytest.mark.parametrize(
    "remote,enabled", [(False, False), (False, True), (True, False)]
)
@pytest.mark.parametrize(
    "quantity", [Quantity.CURRENT_L1, Quantity.POWER, Quantity.ENERGY]
)
async def test_two_thousand_meter_reports_do_not_rebuild_controls(
    controls, monkeypatch, remote, enabled, quantity
):
    hass, entities, (control, bound, peer, *_), _, _ = controls
    runtime = control.runtime
    authority(
        control,
        bound.target,
        ControlAuthority.REMOTE if remote else ControlAuthority.LOCAL,
    )
    now = datetime.now(UTC)
    runtime.observe_enabled(
        bound.token,
        EnabledObservation(
            bound.target, enabled, now, now + timedelta(seconds=90), "wallbox"
        ),
    )
    writes = [Mock(wraps=e.async_write_ha_state) for e in entities.values()]
    for entity, write in zip(entities.values(), writes, strict=True):
        monkeypatch.setattr(entity, "async_write_ha_state", write)
    before = list(peer.operations)
    for _ in range(2000):
        runtime.observe(bound.token, (observation(bound.target, quantity, 0),))
    await hass.async_block_till_done()
    assert sum(w.call_count for w in writes) == 0
    assert peer.operations == before


async def test_local_permission_phase_voltage_and_generation_still_publish(controls):
    hass, entities, (control, bound, *_), _, _ = controls
    runtime, target = control.runtime, bound.target
    authority(control, target, ControlAuthority.LOCAL)
    enabled = entities["charging_enabled"]
    for value in (True, False, True):
        now = datetime.now(UTC)
        assert runtime.observe_enabled(
            bound.token,
            EnabledObservation(
                target, value, now, now + timedelta(seconds=90), "wallbox"
            ),
        )
        assert hass.states.get(enabled.entity_id).state == ("on" if value else "off")
    entity = entities["desired_charging_power"]
    before = hass.states.get(entity.entity_id).last_reported
    runtime.observe(bound.token, (observation(target, Quantity.VOLTAGE_L1, 240),))
    assert hass.states.get(entity.entity_id).last_reported > before
    now = datetime.now(UTC)
    assert runtime.observe_physical_phase(
        bound.token,
        PhysicalPhaseObservation(
            target, PhaseMode.canonical(3), now, now + timedelta(seconds=90), "wallbox"
        ),
    )
    assert (
        hass.states.get(entity.entity_id).attributes["physical_phase_valid_until"]
        is not None
    )
    runtime.disconnect(bound.token)
    assert not enabled.available
    token = runtime.connect(target.station)
    token = runtime.boot(token, StationIdentity(model="new boot"))
    assert token != bound.token
    assert runtime.enabled(target) is None
    assert not runtime.observe(bound.token, (observation(target, Quantity.POWER, 100),))
    assert runtime.observe(token, (observation(target, Quantity.VOLTAGE_L1, 235),))
    assert hass.states.get(entity.entity_id).attributes["actual_enabled"] is None


async def test_reading_configuration_never_normalizes_or_mutates(grid, monkeypatch):
    p, target, _ = grid
    setup_optimum(p, target)
    await p.hass.async_block_till_done()
    before = deepcopy(p.settings)
    clamp = Mock(wraps=profiles.clamp_settings)
    bounds = Mock(wraps=pv_soc.target_bounds)
    reserve = Mock(wraps=p.pv_reserve)
    monkeypatch.setattr(profiles, "clamp_settings", clamp)
    monkeypatch.setattr(pv_soc, "target_bounds", bounds)
    monkeypatch.setattr(p, "pv_reserve", reserve)
    for _ in range(5000):
        p.setting(target)
        p.attributes(target)
    assert p.settings == before

    clamp.assert_not_called()
    bounds.assert_not_called()
    reserve.assert_not_called()
    # An unknown/offline registry target can be displayed without creating config.
    p.setting(replace(target, value="99"))
    assert p.settings == before


async def test_current_only_flow_updates_actual_charging_display(grid):
    p, target, (control, bound, *_) = grid
    now = datetime.now(UTC)
    control.runtime.observe_enabled(
        bound.token,
        EnabledObservation(target, True, now, now + timedelta(seconds=90), "wallbox"),
    )
    entity = ControlEntity(control, p.entry_id, target, "current_only")
    entity.hass = p.hass
    attributes = []
    entity.async_write_ha_state = lambda: attributes.append(
        entity.extra_state_attributes
    )
    remove = control.runtime.subscribe(entity._telemetry, select=entity.runtime_view)
    try:
        control.runtime.observe(
            bound.token,
            (
                observation(target, Quantity.CHARGING_STATE, State.CHARGING),
                observation(target, Quantity.CURRENT_L1, 10),
            ),
        )
        assert attributes[-1]["actual_charging"] is True
        control.runtime.observe(
            bound.token, (observation(target, Quantity.CURRENT_L1, 0),)
        )
        assert attributes[-1]["actual_charging"] is False
    finally:
        remove()
        entity._cancel_expiry()


async def test_reserve_changes_project_once_and_expand_without_restoring(grid):
    p, target, _ = grid
    setup_optimum(p, target)
    p.subscribe_inputs()
    await p.hass.async_block_till_done()
    p.hass.states.async_set("number.reserve", 60)
    await p.hass.async_block_till_done()
    assert p.setting(target)["optimum_lower_soc"] == 62
    p.hass.states.async_set("number.reserve", 5)
    await p.hass.async_block_till_done()
    assert p.setting(target)["optimum_lower_soc"] == 62
    await p.set_value(target, "soc_hysterese", 5)
    assert p.setting(target)["soc_hysterese"] == 5
    p.hass.states.async_set("number.reserve", "unavailable")
    await p.hass.async_block_till_done()
    with pytest.raises(ValueError):
        p.pv_target(target, datetime.now(UTC))
    assert p.settings[target_key(target)]["optimum_lower_soc"] == 62


async def test_control_expiry_keeps_same_deadline_and_publishes_transition(
    controls, monkeypatch
):
    hass, entities, (control, bound, *_), _, _ = controls
    from custom_components.wallbox_manager import control_entity

    entity = entities["charging_enabled"]
    tracker = Mock(wraps=control_entity.async_track_point_in_utc_time)
    monkeypatch.setattr(control_entity, "async_track_point_in_utc_time", tracker)
    entity._schedule_expiry()
    deadline = entity._expiry_deadline
    assert deadline is not None
    for _ in range(1000):
        entity._schedule_expiry()
    tracker.assert_not_called()
    assert entity._expiry_deadline == deadline
    # Permission expiry is independent of new measurement input.
    now = datetime.now(UTC)
    control.runtime.observe_enabled(
        bound.token,
        EnabledObservation(
            bound.target, True, now, now + timedelta(milliseconds=40), "wallbox"
        ),
    )
    import asyncio

    await asyncio.sleep(0.06)
    await hass.async_block_till_done()
    assert control.runtime.enabled(bound.target) is None
    assert hass.states.get(entity.entity_id).state == "unknown"


async def test_measurement_reports_reach_only_their_channel(diagnostics, monkeypatch):
    hass, _, runtime, platforms, _, _ = diagnostics
    scope = EvseId(StationId("reports"), "1")
    token = runtime.connect(scope.station)
    runtime.observe(
        token,
        (
            observation(scope, Quantity.POWER, 500),
            observation(scope, Quantity.VOLTAGE_L1, 230),
        ),
    )
    await hass.async_block_till_done()
    entities = {e.channel.quantity: e for e in operational(platforms)}
    power, voltage = entities[Quantity.POWER], entities[Quantity.VOLTAGE_L1]
    unchanged = Mock(wraps=voltage.async_write_ha_state)
    monkeypatch.setattr(voltage, "async_write_ha_state", unchanged)
    power._attr_force_update = True
    before = hass.states.get(power.entity_id)
    runtime.observe(token, (observation(scope, Quantity.POWER, 500),))
    after = hass.states.get(power.entity_id)
    assert after.last_reported > before.last_reported
    assert after.last_updated > before.last_updated
    unchanged.assert_not_called()
    runtime.observe_zero_point(token, scope)
    # Explicit validity invalidation must reach filtered listeners too.
    assert unchanged.call_count == 1


async def test_session_meter_fanout_and_inactive_timers(diagnostics, monkeypatch):
    hass, _, runtime, platforms, _, _ = diagnostics
    scope = EvseId(StationId("session"), "1")
    token = runtime.connect(scope.station)
    at = datetime.now(UTC) - timedelta(seconds=1)
    runtime.session_event(
        token, SessionEvent(scope, "tx", SessionEventKind.STARTED, at)
    )
    await hass.async_block_till_done()
    entities = sessions(platforms)
    assert entities["duration"]._refresh is not None
    assert entities["power"]._refresh is None
    writes = {key: Mock(wraps=e.async_write_ha_state) for key, e in entities.items()}
    for key, entity in entities.items():
        monkeypatch.setattr(entity, "async_write_ha_state", writes[key])
    runtime.observe(token, (observation(scope, Quantity.POWER, 100),))
    assert writes["power"].call_count == 1
    assert all(w.call_count == 0 for key, w in writes.items() if key != "power")
    assert entities["power"]._refresh is not None
    runtime.session_event(
        token,
        SessionEvent(
            scope, "tx", SessionEventKind.ENDED, datetime.now(UTC), State.IDLE
        ),
    )
    assert entities["duration"]._refresh is None
    assert entities["power"]._refresh is None
    stopped = {key: w.call_count for key, w in writes.items()}
    runtime.observe(token, (observation(scope, Quantity.VOLTAGE_L1, 232),))
    assert {key: w.call_count for key, w in writes.items()} == stopped


async def test_session_power_expires_without_periodic_refresh(diagnostics):
    import asyncio

    hass, _, runtime, platforms, _, _ = diagnostics
    scope = EvseId(StationId("expiry"), "1")
    token = runtime.connect(scope.station)
    runtime.session_event(
        token,
        SessionEvent(
            scope,
            "tx",
            SessionEventKind.STARTED,
            datetime.now(UTC) - timedelta(seconds=1),
        ),
    )
    await hass.async_block_till_done()
    sample = observation(scope, Quantity.POWER, 500, lifetime=0.04)
    runtime.observe(token, (sample,))
    entity = sessions(platforms)["power"]
    assert hass.states.get(entity.entity_id).state == "500.0"
    assert entity._refresh_deadline == sample.valid_until
    await asyncio.sleep(0.06)
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == "unknown"
    assert entity._refresh is None
