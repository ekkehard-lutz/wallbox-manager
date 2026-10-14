"""BALANCE response evidence with raw EV and independently filtered site power.

These deterministic plants establish controller regressions, not hardware timing
guarantees. Their site/EV acquisition clocks and command effects are explicit.
"""

import asyncio
from dataclasses import dataclass
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_power_solver import run as run
from test_pv_beta9 import evidence, point
from test_pv_optimum_hold import configure, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.power_history import PowerHistory
from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator


def balance(
    regulator,
    second,
    *,
    available=4500,
    actual=2300,
    pv=5000,
    load=2800,
    imported=0,
    exported=2200,
    discharge=0,
    limit=3500,
    ev_time=None,
    site_time=None,
    confirmed=None,
    pending=False,
    interval=5,
):
    return regulator.balance_request(
        Fraction(str(available)),
        actual,
        discharge,
        limit,
        imported,
        exported,
        now=second,
        interval=interval,
        evidence=evidence(
            second if ev_time is None else ev_time,
            second if site_time is None else site_time,
            pv=pv,
            load=load,
        ),
        confirmed=confirmed,
        pending=pending,
    )


async def test_balance_adapter_cannot_spend_negative_smoothed_household(grid):
    """The historical mixed-filter arithmetic is not an upward capacity proof."""
    p, target, _, clock = configure(grid, initial_time=100)
    samples(
        p,
        target,
        actual=Fraction("2417.69"),
        pv=Fraction("2938.67"),
        load=Fraction("2917.69"),
        soc=81,
    )
    p.references["power_smoothing_window"] = 5
    p.power_history = {
        "leistung_pv": PowerHistory(5),
        "leistung_verbraucher": PowerHistory(5),
    }
    p.power_history["leistung_pv"].add(95, Fraction("2938.67"))
    p.power_history["leistung_verbraucher"].add(95, Fraction("1297.82"))
    await asyncio.sleep(0)
    # The event at t=100 has zero weight; the existing independent filters stay
    # enabled and the selected EV remains the current, unfiltered measurement.
    clock[0] = 100
    available, _, actual = p.pv_measurements(target, details=True)
    assert available == Fraction("4058.54")
    assert actual == Fraction("2417.69")
    power, _, _ = p.optimum_request(target)
    assert p.optimum_modes[target] == "PV_BALANCE"
    assert power <= Fraction("2438.67")


def test_balance_reused_sources_do_not_accumulate_an_upward_request():
    regulator = FastDischargeRegulator()
    first = balance(regulator, 0)
    assert 2300 < first <= 4500
    revision = regulator.revision
    for second in (1, 5, 20, 60, 300):
        assert balance(regulator, second, ev_time=0, site_time=0) == first
        assert regulator.revision == revision


def test_balance_new_ev_cannot_credit_site_export_before_it_is_remeasured():
    regulator = FastDischargeRegulator()
    first = balance(
        regulator,
        0,
        available=3100,
        actual=1380,
        pv=3600,
        load=1880,
        exported=1720,
    )
    for second in (5, 10, 30):
        power = balance(
            regulator,
            second,
            available=4170,
            actual=2450,
            pv=3600,
            load=1880,
            exported=1720,
            site_time=0,
        )
        assert power <= first


def test_balance_ack_and_timeout_do_not_prove_an_increase_response():
    regulator = FastDischargeRegulator()
    balance(
        regulator,
        0,
        confirmed=point(6),
        actual=1380,
        load=1880,
        available=4500,
        exported=3120,
    )
    regulator.dispatched(point(10))
    for second in (1, 5, 10, 20, 40, 100):
        power = balance(
            regulator,
            second,
            confirmed=point(10),
            actual=1380,
            load=1880,
            available=4500,
            exported=3120,
        )
        assert power <= point(10).offered_power_w
        assert not regulator.response_observed
    responded = balance(regulator, 110, confirmed=point(10))
    assert regulator.response_observed
    assert responded > point(10).offered_power_w
    assert (
        balance(regulator, 120, confirmed=point(10), ev_time=110, site_time=110)
        == responded
    )


def test_balance_smoothed_deficit_after_a_response_cannot_reduce_a_supported_offer():
    regulator = FastDischargeRegulator()
    balance(regulator, 0, confirmed=point(10))
    # Raw PV/site/grid sources support 4.5 kW. The low soft balance can be an
    # older high-load filter combined with a newly reduced actual EV sample.
    power = balance(regulator, 5, available=800, confirmed=point(10))
    assert power == point(10).offered_power_w


def test_balance_physical_response_before_ack_preserves_pre_dispatch_baseline():
    regulator = FastDischargeRegulator()
    balance(regulator, 0, actual=1380, load=1880, exported=3120, confirmed=point(6))
    regulator.dispatched(point(10))
    # The plant changes before its delayed protocol acknowledgement. Observing
    # that fresh EV value while pending must not overwrite the dispatch baseline.
    pending = balance(
        regulator,
        5,
        actual=2070,
        load=2570,
        exported=2430,
        confirmed=point(6),
        pending=True,
    )
    assert pending <= point(10).offered_power_w
    acknowledged = balance(
        regulator,
        6,
        actual=2070,
        load=2570,
        exported=2430,
        confirmed=point(10),
        ev_time=5,
        site_time=5,
    )
    assert regulator.response_observed
    assert acknowledged > point(10).offered_power_w
    observed = balance(
        regulator, 12, actual=2070, load=2570, exported=2430, confirmed=point(10)
    )
    assert regulator.response_observed
    assert observed > point(10).offered_power_w


@pytest.mark.parametrize(
    "changes,ceiling",
    [
        ({"pv": 2000, "imported": 800, "exported": 0}, 1500),
        ({"load": 4800, "exported": 200}, 2500),
        ({"discharge": 3500, "limit": 1000}, 1900),
    ],
)
def test_balance_reductions_bypass_long_upward_interval_and_response_wait(
    changes, ceiling
):
    regulator = FastDischargeRegulator()
    first = balance(regulator, 0, interval=300)
    assert first > ceiling
    reduced = balance(
        regulator, 1, interval=300, confirmed=point(14), pending=True, **changes
    )
    assert reduced <= ceiling


@dataclass
class BalanceTrace:
    offered: list[Fraction]
    physical: list[Fraction]
    imports: list[Fraction]
    changes: list[tuple[int, Fraction]]


def simulate_balance(
    run,
    one,
    *,
    scenario,
    ev_offset,
    site_offset,
    response_delay,
    smoothing,
    under_offer=False,
):
    """One-second evaluation; five-second site and seven-second EV acquisition."""
    regulator = FastDischargeRegulator()
    confirmed = point(6)
    physical = confirmed.offered_power_w
    pending = None
    responses = []
    actual, ev_at, site_at = physical, 0, 0
    pv, load, grid_power = Fraction(3600), physical + 500, physical - 3100
    histories = {key: PowerHistory(smoothing) for key in ("pv", "load")}
    histories["pv"].add(0, pv)
    histories["load"].add(0, load)
    trace = BalanceTrace([], [], [], [])
    for second in range(360):
        production, household = 3600, 500
        if scenario == "household" and second >= 120:
            household = 1500
        elif scenario == "intermittent_pv":
            production = 2600 if 120 <= second < 240 else 3600
        if pending is not None and second >= pending[0]:
            confirmed, pending = pending[1], None
        while responses and second >= responses[0][0]:
            response = responses.pop(0)[1].offered_power_w
            physical = response * Fraction(9, 10) if under_offer else response
        measured_grid = physical + household - production
        if second % 5 == site_offset:
            pv, load, grid_power = (
                Fraction(production),
                physical + household,
                measured_grid,
            )
            site_at = second
            histories["pv"].add(second, pv)
            histories["load"].add(second, load)
        if second % 7 == ev_offset:
            actual, ev_at = physical, second
        smoothed_pv = histories["pv"].average(second, pv)[0]
        smoothed_load = histories["load"].average(second, load)[0]
        previous_consumed = regulator.consumed
        watts = balance(
            regulator,
            second,
            available=smoothed_pv - smoothed_load + actual,
            actual=actual,
            pv=pv,
            load=load,
            imported=max(0, grid_power),
            exported=max(0, -grid_power),
            ev_time=ev_at,
            site_time=site_at,
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
        # Existing exact-floor continuation is separate from the ordinary target.
        # These disturbances always leave true PV capacity above that floor.
        if not selected.charging:
            selected = point(6)
        assert selected.offered_power_w <= regulator.hard_max or selected == point(6)
        if pending is None and not selected.same_setpoint(confirmed):
            if selected.offered_power_w > confirmed.offered_power_w:
                assert regulator.response_observed
                if previous_consumed is not None:
                    assert all(
                        current > previous
                        for current, previous in zip(
                            regulator.evidence.times, previous_consumed, strict=True
                        )
                    )
            trace.changes.append((second, selected.offered_power_w))
            regulator.dispatched(selected)
            pending = (second + 1, selected)
            responses.append((second + 1 + response_delay, selected))
        trace.offered.append(confirmed.offered_power_w)
        trace.physical.append(physical)
        trace.imports.append(max(0, measured_grid))
    return trace


@pytest.mark.parametrize("scenario", ["constant", "household", "intermittent_pv"])
@pytest.mark.parametrize("ev_offset,site_offset", [(0, 0), (3, 2)])
@pytest.mark.parametrize("response_delay,smoothing", [(4, 5), (13, 15)])
def test_balance_asynchronous_closed_loop_settles(
    run, one, scenario, ev_offset, site_offset, response_delay, smoothing
):
    trace = simulate_balance(
        run,
        one,
        scenario=scenario,
        ev_offset=ev_offset,
        site_offset=site_offset,
        response_delay=response_delay,
        smoothing=smoothing,
    )
    assert trace.changes
    assert trace.offered[100] > 1380
    assert len(set(trace.offered[-60:])) == 1
    assert max(trace.imports[-60:]) <= 100
    if scenario == "constant":
        changes = [watts for _, watts in trace.changes]
        assert changes == sorted(changes)
    else:
        assert min(trace.offered[140:220]) < trace.offered[100]
        if scenario == "intermittent_pv":
            assert trace.offered[-1] > min(trace.offered[140:220])


def test_balance_under_consuming_ev_makes_controlled_closed_loop_progress(run, one):
    trace = simulate_balance(
        run,
        one,
        scenario="constant",
        ev_offset=3,
        site_offset=2,
        response_delay=8,
        smoothing=5,
        under_offer=True,
    )
    assert trace.offered[100] > 1380
    assert len(set(trace.offered[-60:])) == 1
    assert trace.physical[-1] == trace.offered[-1] * Fraction(9, 10)
    assert max(trace.imports[-60:]) <= 100
