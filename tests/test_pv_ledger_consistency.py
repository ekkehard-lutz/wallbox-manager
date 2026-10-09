"""Real session power publication must not turn excluded samples into PV OFF."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from test_control_runtime import manual as manual
from test_ha_lifecycle import entry
from test_ha_observations import observation
from test_ocpp21_control import connected as connected
from test_pv_optimum_hold import configure, evaluate, period, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager import sensor as sensor_module
from custom_components.wallbox_manager.core.sessions import SessionEvent
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import Quantity
from custom_components.wallbox_manager.session_entity import SessionSensor


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"])
async def test_ledger_sensor_pv_continues_but_invalid_power_still_pauses(grid, profile):
    p, t, (c, bound, peer, *_), clock = configure(grid)
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    if profile == "PV_MAXIMUM":
        p.hass.states.async_set("number.reserve", 78)
    samples(p, t, actual=1380, pv=0, load=1680, discharge=1680)
    runtime = c.runtime
    session = runtime.sessions.get(t)
    # The existing wire fixture intentionally starts one second ahead of its
    # receive clock. Wait for that start; every test acquisition is in the past.
    await asyncio.sleep(
        max(0, (session.started_at - datetime.now(UTC)).total_seconds()) + 0.01
    )
    at = session.started_at
    accepted_at = at + timedelta(milliseconds=1)
    runtime.observe(
        bound.token, (observation(t.evse, Quantity.POWER, 1380, accepted_at),)
    )
    sensor = SessionSensor(runtime, p.entry_id, t, "power")
    sensor.entity_id = "sensor.selected"
    p.hass.config_entries = ConfigEntries(p.hass, {})
    config = entry()
    p.hass.config_entries._entries[config.entry_id] = config
    dr.async_setup(p.hass)
    await dr.async_load(p.hass, load_empty=True)
    await er.async_load(p.hass, load_empty=True)
    platform = EntityPlatform(
        hass=p.hass,
        logger=logging.getLogger(__name__),
        domain="sensor",
        platform_name="wallbox_manager",
        platform=sensor_module,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    platform.config_entry = config
    p.hass.states.async_remove(sensor.entity_id)
    await platform.async_add_entities([sensor])
    assert p.hass.states.get(sensor.entity_id).state == "1380.0"
    assert p.pv_measurements(t, details=True)[2] == 1380
    await p.permission(t, True)
    assert c.confirmed_point(t).charging
    before = len(peer.requests)
    published = []

    def publish():
        sensor.async_write_ha_state()
        published.append(p.hass.states.get(sensor.entity_id).state)

    unsubscribe = runtime.sessions.subscribe(publish)
    try:
        runtime.session_event(
            bound.token,
            SessionEvent(
                t,
                session.external_transaction_id,
                Kind.UPDATED,
                at + timedelta(milliseconds=2),
                sequence=1,
            ),
            (observation(t, Quantity.POWER, 1800, at + timedelta(milliseconds=2.4)),),
            live=True,
        )
        assert published == ["1380.0"]
        state = p.hass.states.get(sensor.entity_id)
        assert state.attributes["power_availability_reason"] == "valid"
        assert state.attributes["observed_at"] == accepted_at.isoformat()
        assert p.pv_measurements(t, details=True)[2] == 1380
        clock[0] = 1
        await evaluate(p, t)
        assert c.confirmed_point(t).charging
        assert all(period(request)["limit"] > 0 for request in peer.requests[before:])

        runtime.observe(
            bound.token,
            (
                observation(
                    t.evse, Quantity.POWER, 1800, at + timedelta(milliseconds=4)
                ),
            ),
        )
        assert p.hass.states.get(sensor.entity_id).state == "1800.0"
        clock[0] = 2
        await evaluate(p, t)
        assert c.confirmed_point(t).charging
        assert all(period(request)["limit"] > 0 for request in peer.requests[before:])

        runtime.observe(
            bound.token,
            (
                observation(
                    t.evse, Quantity.POWER, None, at + timedelta(milliseconds=5)
                ),
            ),
        )
        assert sensor.native_value is None
        assert p.hass.states.get(sensor.entity_id).state == "unknown"
        clock[0] = 3
        await evaluate(p, t)
        assert p.status[t] == "hard_budget_unavailable"
        assert not c.confirmed_point(t).charging
        assert period(peer.requests[-1])["limit"] == 0
    finally:
        unsubscribe()
        await platform.async_reset()
