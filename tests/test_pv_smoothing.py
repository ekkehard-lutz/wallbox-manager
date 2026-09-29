"""Only PV/load are averaged; validity and dispatched snapshots remain authoritative."""

import asyncio
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.power_history import PowerHistory


async def test_independent_event_histories_raw_soc_and_unavailable(grid):
    p, t, _ = grid
    now = [0]
    p.monotonic = lambda: now[0]
    p.references["power_smoothing_window"] = 5
    p.power_history = {
        key: PowerHistory(5) for key in ("leistung_pv", "leistung_verbraucher")
    }
    measurements(p, t, pv=2000, load=500, actual=0, soc=96)
    await asyncio.sleep(0)
    now[0] = 4
    measurements(p, t, pv=1000, load=1000, actual=0, soc=89)
    await asyncio.sleep(0)
    now[0] = 5
    power, soc = p.pv_measurements(t)
    assert power == 1800 - 600
    assert soc == 89
    assert len(p.power_history) == 2
    p.hass.states.async_set("sensor.pv", "unavailable")
    await asyncio.sleep(0)
    with pytest.raises(ValueError):
        p.pv_measurements(t)
    assert p.power_history["leistung_pv"].samples[-1][1] is None
    now[0] = 6
    measurements(p, t, pv=1000, load=1000, actual=0, soc=90)
    await asyncio.sleep(0)
    now[0] = 7
    assert p.pv_measurements(t)[1] == 90
    assert p.power_history["leistung_pv"].average(7, 1000) == (1500, 4)


async def test_disabled_raw_without_event_polling(grid):
    p, t, _ = grid
    measurements(p, t, pv=1234, load=234, actual=0, soc=91)
    assert p.pv_measurements(t) == (1000, 91)
    measurements(p, t, pv=2234, load=234, actual=0, soc=92)
    assert p.pv_measurements(t) == (2000, 92)


async def test_history_alone_never_authorizes_invalid_current_measurement(grid):
    p, t, _ = grid
    measurements(p, t)
    p.power_history["leistung_pv"] = PowerHistory(5)
    p.power_history["leistung_pv"].add(p.monotonic() - 5, Fraction(10000))
    p.hass.states.async_set("sensor.pv", "unavailable")
    with pytest.raises(ValueError):
        p.pv_measurements(t)


async def test_smoothed_samples_during_dispatch_belong_to_next_snapshot(grid):
    from test_pv_transient_gaps import running

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, _, peer, *_), clock, tick = await running(grid)
    p.references["power_smoothing_window"] = 5
    p.power_history = {
        key: PowerHistory(5) for key in ("leistung_pv", "leistung_verbraucher")
    }
    p.power_history["leistung_pv"].add(0, Fraction(2300))
    p.power_history["leistung_verbraucher"].add(0, Fraction(0))
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    await asyncio.sleep(0)
    clock[0] = 5
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(tick())
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        point = c.pending_points[t]
        assert point.current_a == 10
        generation, epoch = c.intent(t).generation, p.epochs[t]
        measurements(p, t, pv=1840, load=0, actual=0, soc=96)
        await asyncio.sleep(0)
        clock[0] = 10
        assert p.pv_measurements(t)[0] == 1840
        assert c.intent(t).generation == generation and p.epochs[t] == epoch
    finally:
        peer.release.set()
        await pending
    assert c.confirmed_point(t) == point
    assert c.intent(t).command_result.status == CommandStatus.APPLIED
    await tick()
    assert c.confirmed_point(t).current_a == 8
    confirmed, count = c.confirmed_point(t), len(peer.requests)
    p.hass.states.async_set("sensor.pv", "unavailable")
    await asyncio.sleep(0)
    await tick()
    assert c.confirmed_point(t) == confirmed
    assert len(peer.requests) == count


async def test_voltage_remains_raw_with_power_smoothing_enabled(grid):
    from test_pv_transient_gaps import running
    from test_voltage_command_fence import voltage

    p, t, (c, *_), clock, tick = await running(grid)
    p.references["power_smoothing_window"] = 15
    p.power_history = {
        key: PowerHistory(15) for key in ("leistung_pv", "leistung_verbraucher")
    }
    p.power_history["leistung_pv"].add(0, Fraction(2070))
    p.power_history["leistung_verbraucher"].add(0, Fraction(0))
    clock[0] = 5
    voltage(c, t, 200)
    await tick()
    # SoC above target keeps the existing UP approximation: ceil(2070 / 200).
    assert c.confirmed_point(t).current_a == 11
    assert c.confirmed_point(t).phase_voltages_v == (200,)


async def test_sensor_event_during_storage_load_has_history(grid):
    from custom_components.wallbox_manager.profiles import GridProfiles

    p, t, (c, *_) = grid
    clone = GridProfiles(p.hass, p.entry, c, p.battery)
    clone.references["leistung_pv"] = "sensor.startup_pv"
    try:
        p.hass.states.async_set("sensor.startup_pv", 2000, {"unit_of_measurement": "W"})
        await asyncio.sleep(0)
        assert clone.power_history["leistung_pv"].samples[-1][1] == 2000
    finally:
        clone.unsubscribe_battery()
        clone.unsubscribe()
        clone.session_unsubscribe()
