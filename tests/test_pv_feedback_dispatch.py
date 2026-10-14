"""Causal under-offer feedback through the shared real OCPP dispatch boundary."""

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum_hold import configure, evaluate, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.pv_soc import PV_PROFILES


@pytest.mark.parametrize("profile", PV_PROFILES)
async def test_dispatched_start_records_physical_response_below_offer(grid, profile):
    p, target, (control, _, peer, *_), clock = configure(grid)
    p.setting(target).update(profile=profile, soll_soc_speicher=80)
    if profile == "PV_MAXIMUM":
        p.hass.states.async_set("number.reserve", 78)
    await p.permission(target, True)
    first = control.confirmed_point(target)
    assert first.current_a == 7 and first.offered_power_w == 1610
    count = len(peer.requests)
    # 130 W below the offer fails beta.13's half-step response test. A real
    # post-dispatch EV rise from zero and complete source evidence prove response.
    clock[0] = 20
    samples(p, target, actual=1480, discharge=1000, pv=4000, load=1980)
    await evaluate(p, target)
    assert control.confirmed_point(target).current_a > first.current_a
    assert len(peer.requests) == count + 1
    assert (
        control.confirmed_point(target).offered_power_w
        <= p.optimum_regulators[target].hard_max
    )
