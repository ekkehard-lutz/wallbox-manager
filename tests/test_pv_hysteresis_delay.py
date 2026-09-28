"""Battery policy memory composes with a continuous electrical stop deadline."""

import asyncio
import logging
from fractions import Fraction

from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import apply, prepare
from test_pv_diagnostics import records
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements
from test_voltage_command_fence import voltage


async def started(grid):
    p, t, context, clock = await prepare(grid)
    p.setting(t).update(soll_soc_speicher=41, soc_hysterese=5, pv_stop_delay=90)
    measurements(p, t, pv=1000, load=0, actual=0, soc=41.2)
    await p.permission(t, True)
    await asyncio.sleep(0)
    return p, t, context, clock


async def test_target_jitter_and_voltage_drift_do_not_bypass_stop_delay(grid, caplog):
    p, t, (c, _, peer, *_), clock = await started(grid)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    confirmed = c.confirmed_point(t)
    for seconds, soc in ((0, 41.1), (5, 41), (30, 41), (60, 40.9), (89, 41)):
        clock[0] = seconds
        voltage(c, t, Fraction(230) + Fraction(seconds, 1000))
        measurements(p, t, pv=1000, load=0, actual=0, soc=soc)
        result = p.pv_edit(t)
        assert result.point.same_setpoint(confirmed)
        if soc <= 41:
            assert p.status[t] == "pv_stop_delay"
            assert p.pv_stop_since[t] == 5
            line = records(caplog)[-1]
            assert line["delays"]["pv_stop_delay"]["elapsed_s"] == seconds - 5
            assert line["delays"]["pv_stop_delay"]["remaining_s"] == 95 - seconds
        else:
            await apply(p, t)
    # Recovery cancels, later low surplus starts a new independent deadline.
    clock[0] = 90
    measurements(p, t, pv=1000, load=0, actual=0, soc=41.1)
    p.pv_edit(t)
    assert t not in p.pv_stop_since and p.pv_battery[t]
    for seconds in (100, 105, 130, 160, 189):
        clock[0] = seconds
        measurements(p, t, pv=1000, load=0, actual=0, soc=41)
        assert p.pv_edit(t).point.same_setpoint(confirmed)
        assert p.pv_stop_since[t] == 100
    clock[0] = 190
    assert not (await apply(p, t)).charging
    assert c.runtime.enabled(t) is True
    assert all(
        r[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] > 0
        for r in peer.requests[:-1]
    )


async def test_lower_soc_latches_stop_until_upper_threshold_and_uses_delay(grid):
    p, t, (c, _, peer, *_), clock = await started(grid)
    confirmed = c.confirmed_point(t)
    count = len(peer.requests)
    for seconds, soc in ((0, 35.9), (5, 36.1), (30, 40), (60, 41), (89, 40.9)):
        clock[0] = seconds
        measurements(p, t, pv=5000, load=0, actual=0, soc=soc)
        await asyncio.sleep(0)  # Exercise the real SoC-event listener too.
        assert p.pv_edit(t).point.same_setpoint(confirmed)
        assert not p.pv_battery[t]
        assert p.pv_stop_since[t] == 0
        assert p.pv_ongoing[t]
        assert len(peer.requests) == count
    clock[0] = 90
    assert not (await apply(p, t)).charging
    assert not p.pv_ongoing[t]


async def test_lower_soc_recovery_cancels_pending_stop_only_above_target(grid):
    p, t, (c, _, _, *_), clock = await started(grid)
    measurements(p, t, pv=5000, load=0, actual=0, soc=35)
    p.pv_edit(t)
    clock[0] = 30
    measurements(p, t, pv=5000, load=0, actual=0, soc=41.1)
    assert (await apply(p, t)).charging
    assert t not in p.pv_stop_since and p.pv_battery[t]
