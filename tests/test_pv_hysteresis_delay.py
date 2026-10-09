"""Common SoC protection is independent of the electrical PV stop delay."""

import asyncio
from fractions import Fraction

from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import apply, prepare
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements
from test_voltage_command_fence import voltage


async def started(grid):
    p, t, context, clock = await prepare(grid)
    p.setting(t).update(soll_soc_speicher=41, soc_hysterese=2, pv_stop_delay=90)
    measurements(p, t, pv=2000, load=0, actual=0, soc=42)
    await p.permission(t, True)
    await asyncio.sleep(0)
    assert context[0].confirmed_point(t).charging
    return p, t, context, clock


async def test_target_jitter_and_voltage_drift_do_not_bypass_stop_delay(grid):
    p, t, (c, _, _, *_), clock = await started(grid)
    for seconds, soc in ((0, 41.1), (5, 41), (30, 41), (60, 40.9), (89, 41)):
        clock[0] = seconds
        voltage(c, t, Fraction(230) + Fraction(seconds, 1000))
        measurements(p, t, pv=2000, load=0, actual=0, soc=soc)
        assert (await apply(p, t)).charging
        assert p.optimum_modes[t] == "PV_BALANCE"
        assert t not in p.pv_stop_since
    for seconds in (100, 105, 130, 160, 189):
        clock[0] = seconds
        measurements(p, t, pv=0, load=0, actual=0, soc=41)
        assert p.pv_edit(t).point.charging
        assert p.pv_stop_since[t] == 100
    clock[0] = 190
    assert not (await apply(p, t)).charging
    assert c.runtime.enabled(t) is True


async def test_lower_soc_stops_immediately_despite_stop_delay(grid):
    p, t, (c, _, _, *_), clock = await started(grid)
    for seconds, soc in ((0, 40), (5, 40.1), (30, 41), (60, 41.999)):
        clock[0] = seconds
        measurements(p, t, pv=5000, load=0, actual=0, soc=soc)
        assert not (await apply(p, t)).charging
        assert p.optimum_modes[t] == "STOP"
        assert t not in p.pv_stop_since
    assert c.runtime.enabled(t) is True


async def test_lower_soc_recovery_restarts_only_at_middle_with_surplus(grid):
    p, t, _, clock = await started(grid)
    measurements(p, t, pv=5000, load=0, actual=0, soc=40)
    assert not (await apply(p, t)).charging
    clock[0] = 30
    measurements(p, t, pv=5000, load=0, actual=0, soc=41.1)
    assert not (await apply(p, t)).charging
    measurements(p, t, pv=5000, load=0, actual=0, soc=42)
    assert (await apply(p, t)).charging
    assert p.optimum_modes[t] == "PV_BALANCE"
