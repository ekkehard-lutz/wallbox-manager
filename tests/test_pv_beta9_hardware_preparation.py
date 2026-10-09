"""Offline acceptance evidence; no claim of synchronized physical measurements."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_power_solver import run as run
from test_pv_beta9 import evidence, point
from test_pv_optimum_hold import configure, evaluate, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator
from custom_components.wallbox_manager.pv_soc import PV_PROFILES


@pytest.mark.parametrize("profile", PV_PROFILES)
@pytest.mark.parametrize("initial_amps", [11, 12])
async def test_2760_cannot_be_selected_or_maintained_against_2670(
    grid, profile, initial_amps
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    if profile == "PV_MAXIMUM":
        p.hass.states.async_set("number.reserve", 78)
    samples(p, t, actual=initial_amps * 230, discharge=3400, pv=0, load=3260)
    await p.permission(t, True)
    assert c.confirmed_point(t) == point(initial_amps)
    before = len(peer.requests)
    # Site-first observation: the increase may be EV response, household, or both.
    samples(p, t, actual=2530, discharge=3260, pv=0, load=3260)
    clock[0] = 1
    await evaluate(p, t)
    assert p.optimum_regulators[t].hard_max == 2670
    assert c.confirmed_point(t).offered_power_w <= 2670
    assert not p.permits_point(t, point(12))
    assert len(peer.requests) == before + (initial_amps == 12)
    after = len(peer.requests)
    for tick in (2, 5, 20, 30):
        clock[0] = tick
        await evaluate(p, t)
        assert c.confirmed_point(t).offered_power_w <= 2670
    assert len(peer.requests) == after


@pytest.mark.parametrize("profile", PV_PROFILES)
@pytest.mark.parametrize("missing", ["sensor.selected", "sensor.load"])
async def test_stale_ev_or_site_pauses_then_recovers_conservatively(
    grid, profile, missing
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    if profile == "PV_MAXIMUM":
        p.hass.states.async_set("number.reserve", 78)
    samples(p, t, actual=2300, discharge=3400, pv=0, load=2800)
    await p.permission(t, True)
    assert c.confirmed_point(t).charging
    state = p.hass.states.get(missing)
    p.hass.states.async_set(
        missing,
        state.state,
        {
            **state.attributes,
            "observed_at": (datetime.now(UTC) - timedelta(seconds=91)).isoformat(),
        },
    )
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    count = len(peer.requests)
    await evaluate(p, t)
    assert len(peer.requests) == count
    # Sufficient independent current budget, measured pause, and new source times.
    clock[0] = 30
    samples(p, t, actual=0, discharge=0, pv=4000, load=500)
    await evaluate(p, t)
    assert c.confirmed_point(t).charging
    assert c.confirmed_point(t).offered_power_w <= p.optimum_regulators[t].hard_max


@pytest.mark.parametrize("ev_time,site_time", [(9, 11), (11, 9), (10, 10)])
def test_old_or_identical_generations_never_release_another_increase(
    ev_time, site_time
):
    r = FastDischargeRegulator()
    first = r.request(
        2530, 3030, 3500, 0, 0, now=10, interval=5, evidence=evidence(10, load=3030)
    )
    revision = r.revision
    for tick in (20, 40, 80):
        result = r.request(
            2530,
            3030,
            3500,
            0,
            0,
            now=tick,
            interval=5,
            evidence=evidence(ev_time, site_time, load=3030),
        )
        assert result <= first
        assert r.revision == revision


def test_ev_nonresponse_timeout_never_authorizes_an_upward_step():
    r = FastDischargeRegulator()
    for second in range(61):
        result = r.request(
            1380,
            1880,
            3500,
            0,
            0,
            now=second,
            interval=5,
            evidence=evidence(second, load=1880),
            confirmed=point(10),
        )
        assert result <= 1380
        assert not r.response_observed
    assert r.reason == "unobserved_command_no_increase"


@dataclass
class CadenceMetrics:
    commands: int
    reversals: int
    settling_s: int
    final_ev_w: float
    final_import_w: float
    peak_import_w: float
    final_battery_w: float
    final_headroom_w: float
    last_minute_changes: int


def simulate_cadence(run, one, *, scenario, ev_offset, site_offset, ack, response):
    """100 ms plant clock, 1 s evaluation, 2.3 s EV and 5 s site reports.

    Idealized acquisition stamps and a bounded inverter are assumptions of this
    plant only. Production solver selects points; the existing runtime tests
    separately cover wire deduplication, stale-input rejection and fencing.
    """
    regulator = FastDischargeRegulator()
    physical, discharge = 1380, 1880.0
    confirmed, pending, responses = point(6), None, []
    ev, d, g, load, pv = physical, discharge, 0, physical + 500, 0
    ev_time = site_time = 0
    points, imports, batteries = [], [], []
    commands = reversals = last_direction = 0
    for tick in range(3600):
        second = tick / 10
        household, production = 500, 0
        if scenario == "load_up" and tick >= 1200:
            household = 1700
        elif scenario == "load_down":
            household = 1700 if tick < 1200 else 500
        elif scenario == "pv":
            production = 1800 if 1200 <= tick < 2400 else 0
        elif scenario == "near_limit":
            household = 2000
        if pending and tick >= pending[0]:
            confirmed, pending = pending[1], None
        while responses and tick >= responses[0][0]:
            physical = float(responses.pop(0)[1].offered_power_w)
        net = household + physical - production
        if tick % 10 == 0:
            discharge += (min(3500, max(0, net)) - discharge) / 2
        grid_w = net - discharge
        if tick % 50 == site_offset:
            d, g, load, pv = discharge, grid_w, household + physical, production
            site_time = second
        if tick % 23 == ev_offset:
            ev, ev_time = physical, second
        if tick % 10:
            continue
        revision = regulator.revision
        watts = regulator.request(
            ev,
            d,
            3500,
            max(0, g),
            max(0, -g),
            now=second,
            interval=5,
            evidence=evidence(ev_time, site_time, pv=pv, load=load),
            confirmed=confirmed,
            pending=pending is not None,
        )
        selected = run(
            watts,
            Direction.DOWN,
            eligible_modes=(one,),
            hard_max_w=regulator.hard_max,
        ).point
        assert selected is not None
        if not selected.charging:
            selected = run(
                watts,
                Direction.DOWN,
                eligible_modes=(one,),
                minimum_positive=True,
                hard_max_w=regulator.hard_max,
            ).point
        if selected.charging and selected.offered_power_w == 1380:
            if not regulator.minimum_allowed(point(6), now=second, actual=ev):
                selected = point(0)
        assert selected.offered_power_w <= regulator.hard_max
        if pending is None and not selected.same_setpoint(confirmed):
            delta = selected.offered_power_w - confirmed.offered_power_w
            if delta > 0:
                assert regulator.revision > revision
                assert regulator.response_observed
            direction = 1 if delta > 0 else -1
            reversals += bool(last_direction) and last_direction != direction
            last_direction = direction
            commands += 1
            pending = (tick + ack * 10, selected)
            responses.append((tick + (ack + response) * 10, selected))
        points.append(float(confirmed.offered_power_w))
        imports.append(max(0, grid_w))
        batteries.append(discharge)
    disturbance = 240 if scenario == "pv" else 120 if scenario != "constant" else 0
    changes = [s for s in range(disturbance + 1, 360) if points[s] != points[s - 1]]
    return CadenceMetrics(
        commands,
        reversals,
        max(changes, default=disturbance) - disturbance,
        points[-1],
        round(sum(imports[-60:]) / 60, 2),
        round(max(imports), 2),
        round(batteries[-1], 2),
        round(3500 - batteries[-1], 2),
        sum(points[s] != points[s - 1] for s in range(300, 360)),
    )


@pytest.mark.parametrize(
    "scenario", ["constant", "load_up", "load_down", "pv", "near_limit"]
)
@pytest.mark.parametrize("ev_offset,site_offset", [(0, 0), (3, 0), (0, 17)])
@pytest.mark.parametrize("ack,response", [(1, 4), (3, 15)])
def test_23_second_ev_reports_with_five_second_site_reports(
    run, one, scenario, ev_offset, site_offset, ack, response
):
    metrics = simulate_cadence(
        run,
        one,
        scenario=scenario,
        ev_offset=ev_offset,
        site_offset=site_offset,
        ack=ack,
        response=response,
    )
    assert metrics.last_minute_changes == 0
    assert metrics.settling_s <= 120
    assert metrics.final_import_w <= (690 if metrics.final_ev_w == 1380 else 100)
    if scenario == "constant":
        assert metrics.reversals == 0
        assert metrics.commands > 0
        assert metrics.final_ev_w > 1380


if __name__ == "__main__":
    import json
    from dataclasses import asdict

    import conftest

    # The same deterministic electrical fixtures as pytest; no device discovery.
    at = conftest.now.__wrapped__()
    one = conftest.one.__wrapped__()
    three = conftest.three.__wrapped__()
    cap = conftest.capabilities.__wrapped__(
        at, one, three, conftest.evidence.__wrapped__(at)
    )
    solve = run.__wrapped__(cap, conftest.voltage.__wrapped__(at, cap), at, one, three)
    print(
        json.dumps(
            {
                (
                    f"{scenario}/offsets{ev_offset}-{site_offset}"
                    f"/ack{ack}/response{response}"
                ): asdict(
                    simulate_cadence(
                        solve,
                        one,
                        scenario=scenario,
                        ev_offset=ev_offset,
                        site_offset=site_offset,
                        ack=ack,
                        response=response,
                    )
                )
                for scenario in ("constant", "load_up", "load_down", "pv", "near_limit")
                for ev_offset, site_offset in ((0, 0), (3, 0), (0, 17))
                for ack, response in ((1, 4), (3, 15))
            },
            indent=2,
        )
    )
