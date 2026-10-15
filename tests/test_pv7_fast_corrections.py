"""pv7 confirmed offers and measured allocation through planning/solver/OCPP."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta12 import voltages
from test_pv_optimum_hold import configure, evaluate, period, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.models import PhaseMode


def cohort(p, t, *, actual, discharge, pv, load, imported=0, exported=0, times=None):
    samples(
        p, t, actual=actual, discharge=discharge, pv=pv, load=load, imported=imported
    )
    entities = ["sensor.selected"] + [
        p.references[key]
        for key in (
            "storage_discharge_power",
            "grid_import_power",
            "grid_export_power",
            "leistung_pv",
            "leistung_verbraucher",
        )
    ]
    for index, entity in enumerate(entities):
        state = p.hass.states.get(entity)
        p.hass.states.async_set(
            entity,
            exported if index == 3 else state.state,
            {
                **state.attributes,
                **({"observed_at": times[index].isoformat()} if times else {}),
            },
        )


async def established(
    grid,
    amps,
    phases,
    *,
    actual,
    volts=(230, 230, 230),
    profile="PV_OPTIMUM",
    only_mode=True,
):
    p, t, context, clock = configure(grid, initial_time=-60)
    p.setting(t)["optimum_max_discharge_w"] = 4000
    c, _, _, _, source, _ = context
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    clock[0] = -30
    # Establish the historical offer using the real shared solver and ACK.
    p.setting(t)["profile"] = "NETZ"
    source.mode = PhaseMode.canonical(phases)
    c.restore(t, allowed_current_1p=20)
    if phases == 3 and only_mode:
        source.snapshot = replace(
            source.snapshot,
            envelopes=tuple(e for e in source.snapshot.envelopes if e.mode.count == 3),
        )
    voltages(c, t, volts)
    cohort(p, t, actual=actual, discharge=1000, pv=3000, load=actual + 1000)
    assert (
        await c.change(t, target_w=amps * sum(volts[:phases]))
    ).status == CommandStatus.APPLIED
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    p.pv_confirm(t)
    assert c.confirmed_point(t).current_a == amps
    assert c.confirmed_point(t).mode.count == phases
    p.pv_request(t)
    # Start each reconstruction at the established offer, rather than the
    # unrelated lower soft preference retained from fixture startup.
    p.optimum_regulators[t].requested = c.confirmed_point(t).offered_power_w
    return p, t, context, clock


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS", "PV_MAXIMUM"])
async def test_r6_exact_logged_unanswered_rebound(grid, profile):
    p, t, (c, _, peer, *_), clock = await established(
        grid,
        19,
        1,
        actual=Fraction("4139.97607421875"),
        volts=(Fraction("225.83787536621094"), 230, 230),
        profile=profile,
    )
    clock[0] = 0
    voltages(c, t, (Fraction("226.04486083984375"), 230, 230))
    # This reduction is confirmed while its original measured EV baseline is
    # still 4139.976 W. The preceding preference is deliberately retained.
    p.setting(t)["profile"] = "NETZ"
    assert (
        await c.change(t, target_w=6 * Fraction("226.04486083984375"))
    ).status == CommandStatus.APPLIED
    p.setting(t)["profile"] = profile
    p.pv_confirm(t)
    regulator = p.optimum_regulators[t]
    regulator.requested = Fraction("4200.376")
    regulator.updated_at = 0
    assert regulator.command_ev_baseline == Fraction("4139.97607421875")
    assert regulator.command_delta < 0
    # Shift the exact logged source spacing into the live fixture's valid window.
    origin = datetime.now(UTC) - timedelta(seconds=5)
    offsets = (2.381068, 1.718309, 1.618579, 1.714534, 1.616438, 1.613364)
    times = tuple(origin + timedelta(seconds=s) for s in offsets)
    regulator.command_evidence = (origin,) * 6
    clock[0] = 2
    voltages(c, t, (Fraction("227.84629821777344"), 230, 230))
    cohort(
        p,
        t,
        actual=Fraction("2391.46166992188"),
        discharge=Fraction("3867.4"),
        pv=Fraction("1124.9"),
        load=Fraction("4467.3"),
        exported=Fraction("282.7"),
        times=times,
    )
    before = len(peer.requests)
    plan = p.pv_plan(t)
    assert float(regulator.hard_max) == pytest.approx(2706.76166992188)
    assert not regulator.response_observed
    assert plan[3].point.charging
    assert plan[3].point.mode.count == 1
    assert plan[3].point.current_a == 6
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before


@pytest.mark.parametrize("amps", [7, 6])
async def test_r12_r13_measured_excess_selects_executable_reduction_or_minimum(
    grid, amps
):
    p, t, (c, _, peer, _, source, _), clock = await established(
        grid,
        amps,
        3,
        actual=Fraction("5388.5634765625"),
        volts=(Fraction("4717.655426025391") / 21,) * 3,
    )
    # Only the actual 3p mode is permitted; no imaginary cheaper phase minimum.
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(e for e in source.snapshot.envelopes if e.mode.count == 3),
    )
    voltages(c, t, (229.2894287109375, 228.22145080566406, 228.27633666992188))
    clock[0] = 30
    cohort(
        p,
        t,
        actual=Fraction("5388.5634765625"),
        discharge=Fraction("4199.2"),
        imported=Fraction("29.4"),
        pv=1763,
        load=Fraction("5721.4"),
    )
    regulator = p.optimum_regulators[t]
    regulator.requested = Fraction("4994.615")
    before = len(peer.requests)
    plan = p.pv_plan(t)
    assert float(regulator.hard_max) == pytest.approx(5089.3634765625)
    assert plan[3].point.mode.count == 3
    assert plan[3].point.current_a == 6
    await evaluate(p, t)
    assert c.confirmed_point(t).charging
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before + (amps == 7)
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    assert p.economic_policies[t].energy_ws == 0


async def test_voltage_drop_cannot_turn_an_old_watt_bound_into_more_current(grid):
    p, t, (c, _, peer, *_), clock = await established(
        grid, 10, 1, actual=2700, volts=(250, 230, 230)
    )
    regulator = p.optimum_regulators[t]
    regulator.requested = Fraction(4200)
    clock[0] = 0
    voltages(c, t, (210, 230, 230))
    cohort(p, t, actual=2700, discharge=0, pv=3000, load=3700)
    before = len(peer.requests)
    await evaluate(p, t)
    assert not regulator.response_observed
    assert c.confirmed_point(t).current_a == 10
    assert len(peer.requests) == before


async def test_measured_deficit_reduction_with_all_phase_modes_available(grid):
    p, t, (c, _, peer, *_), clock = await established(
        grid, 7, 3, actual=5388, only_mode=False
    )
    clock[0] = 30
    cohort(p, t, actual=5388, discharge=4200, pv=1763, load=5721)
    before = len(peer.requests)
    result = p.pv_plan(t)[3]
    assert result.point.charging
    assert result.point.offered_power_w < 7 * 3 * 230
    await evaluate(p, t)
    assert c.confirmed_point(t).same_setpoint(result.point)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] > 0


async def test_deficit_respects_nonstandard_current_grid_and_exact_floor(grid):
    p, t, (c, _, peer, _, source, _), clock = await established(
        grid, 11, 1, actual=3000
    )
    one = PhaseMode.canonical(1)
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, min_current_a=7, current_step_a=2)
            for e in source.snapshot.envelopes
            if e.mode == one
        ),
    )
    source.permitted = (CurrentLimit(one, 8, 12, "vehicle"),)
    for second in (30, 35, 60, 4800):
        clock[0] = second
        cohort(p, t, actual=3000, discharge=4200, pv=2000, load=4000)
        await evaluate(p, t)
        assert c.confirmed_point(t).current_a == 9
        assert c.confirmed_point(t).mode == one
        assert not p.optimum_regulators[t].response_observed
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    assert c.technical_minimum(t).point.current_a == 9
    assert p.economic_policies[t].energy_ws == 0


async def test_unchanged_measured_deficit_reduces_stepwise_only_after_new_sources(grid):
    p, t, (c, _, peer, *_), clock = await established(grid, 14, 1, actual=4000)
    regulator = p.optimum_regulators[t]
    previous = 14
    for second in (30, 35, 40, 45, 50):
        clock[0] = second
        cohort(p, t, actual=4000, discharge=4200, pv=3000, load=5000)
        await evaluate(p, t)
        reduced = c.confirmed_point(t)
        assert reduced.charging
        assert 6 <= reduced.current_a < previous or previous == reduced.current_a == 6
        assert not regulator.response_observed
        count = len(peer.requests)
        clock[0] += 1
        await evaluate(p, t)  # Reuse the same sources; no additional step.
        assert c.confirmed_point(t).same_setpoint(reduced)
        assert len(peer.requests) == count
        previous = reduced.current_a
    assert previous == 6
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    assert p.economic_policies[t].energy_ws == 0


@pytest.mark.parametrize("actual", [2700, 2300, 2000])
async def test_measured_above_equal_below_nominal_still_reduces(grid, actual):
    p, t, (c, _, peer, *_), clock = await established(grid, 10, 1, actual=actual)
    clock[0] = 30
    cohort(p, t, actual=actual, discharge=4200, pv=2000, load=actual + 1000)
    before = len(peer.requests)
    await evaluate(p, t)
    reduced = c.confirmed_point(t)
    assert reduced.charging and 6 <= reduced.current_a < 10
    assert len(peer.requests) == before + 1
    regulator = p.optimum_regulators[t]
    baseline = regulator.command_ev_baseline
    assert baseline == actual
    assert not regulator.response_observed
    # Identical evidence after ACK cannot decrement another step, invent a
    # physical response, or rebound to the retained higher soft preference.
    before = len(peer.requests)
    for second in (31, 35, 60, 4800):
        clock[0] = second
        await evaluate(p, t)
        assert c.confirmed_point(t).same_setpoint(reduced)
        assert not regulator.response_observed
        assert regulator.command_ev_baseline == baseline
    assert len(peer.requests) == before


async def test_economic_reduction_skew_no_rebound_then_genuine_response(grid):
    p, t, (c, _, peer, *_), clock = await established(grid, 7, 3, actual=5388)
    clock[0] = 30
    cohort(p, t, actual=5388, discharge=4200, pv=1763, load=5721)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    regulator = p.optimum_regulators[t]
    assert regulator.command_ev_baseline == 5388
    assert regulator.command_delta < 0
    count = len(peer.requests)
    # New EV / older site reports, all later than dispatch. Power still exceeds
    # the 6 A offer: even a real decline is not sufficient response proof yet.
    origin = datetime.now(UTC)
    times = (origin - timedelta(milliseconds=100),) + (
        origin - timedelta(milliseconds=200),
    ) * 5
    clock[0] = 35
    cohort(p, t, actual=5000, discharge=1000, pv=3000, load=5500, times=times)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert not regulator.response_observed
    assert len(peer.requests) == count
    # Complete fresh sources and an actual downward response below the offer
    # release ordinary regulation with sufficient economic headroom.
    clock[0] = 40
    cohort(p, t, actual=3800, discharge=1000, pv=3000, load=4300)
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert c.confirmed_point(t).current_a > 6
    assert len(peer.requests) == count + 1
    raised = c.confirmed_point(t)
    for second in (41, 60, 4800):
        clock[0] = second
        await evaluate(p, t)
        assert c.confirmed_point(t).same_setpoint(raised)
        assert not regulator.response_observed
    assert len(peer.requests) == count + 1


@pytest.mark.parametrize("stale_channel", range(6))
async def test_deficit_needs_post_command_sources_but_hard_reduction_is_prompt(
    grid, stale_channel
):
    p, t, (c, _, peer, *_), clock = await established(grid, 10, 1, actual=2700)
    regulator = p.optimum_regulators[t]
    origin = datetime.now(UTC) - timedelta(milliseconds=100)
    times = [origin] * 6
    times[stale_channel] = regulator.command_evidence[stale_channel]
    clock[0] = 30
    cohort(p, t, actual=2700, discharge=4200, pv=2000, load=3700, times=times)
    await evaluate(p, t)
    # H=2400 still supports the nominal 2300 W offer. An incomplete post-command
    # cohort cannot project another measured reduction from the offered point.
    assert c.confirmed_point(t).current_a == 10
    assert not regulator.response_observed
    # An independently binding raw hard budget still reduces without waiting
    # for response, interval, or that missing post-command source.
    clock[0] = 31
    cohort(p, t, actual=2700, discharge=6000, pv=0, load=10000, times=times)
    before = len(peer.requests)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert c.confirmed_point(t).charging
    assert len(peer.requests) == before + 1
    assert c.minimum_exception_valid(t, c.confirmed_point(t))


@pytest.mark.parametrize("stale_site", [False, True])
async def test_rejected_phase_delayed_fallback_ack_retains_desire_and_baseline(
    grid, stale_site
):
    p, t, (c, _, peer, *_), clock = await established(grid, 19, 1, actual=4370)
    c.restore(t, allowed_current_1p=20)
    clock[0] = 0
    cohort(p, t, actual=4370, discharge=1000, pv=3000, load=4870)

    def response(profile):
        phases = profile["charging_schedule"][0]["charging_schedule_period"][0][
            "number_phases"
        ]
        if phases == 3:
            # Reject 3p, then block the same-phase fallback's ACK.
            peer.release.clear()
            peer.received.clear()
            return call_result.SetChargingProfile(
                status="Rejected", status_info={"reason_code": "PhaseSwitchLockout"}
            )
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    before = len(peer.requests)
    pending = asyncio.create_task(evaluate(p, t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        # The first signal may belong to the rejected phase request. Wait until
        # the peer has received the positive fallback, without a wall-clock timer.
        while len(peer.requests) < before + 2:
            peer.received.clear()
            await asyncio.wait_for(peer.received.wait(), 2)
        assert period(peer.requests[-1])["number_phases"] == 1
        assert period(peer.requests[-1])["limit"] == 6
        regulator = p.optimum_regulators[t]
        dispatch_times = regulator.dispatch_evidence
        dispatch_ev = regulator.dispatch_ev
        assert dispatch_ev == 4370
        clock[0] = 2
        cohort(p, t, actual=2391, discharge=1000, pv=3000, load=2891)
        p.pv_request(t)  # Independent observation while ACK is pending.
    finally:
        peer.release.set()
        result = await asyncio.wait_for(pending, 2)
    assert result.status == CommandStatus.APPLIED
    assert c.confirmed_point(t).current_a == 6
    assert c.phase_restricted(t)
    assert regulator.command_evidence == dispatch_times
    assert regulator.command_ev_baseline == dispatch_ev
    assert not regulator.response_observed
    count = len(peer.requests)
    for second in (3, 30, 600):
        clock[0] = second
        origin = datetime.now(UTC) - timedelta(milliseconds=100)
        times = (origin,) + (dispatch_times[1] if stale_site else origin,) * 5
        cohort(p, t, actual=2391, discharge=1000, pv=3000, load=2891, times=times)
        await evaluate(p, t)
        assert c.confirmed_point(t).current_a == 6
        assert c.intent(t).energy_desired.point.mode.count == 3
        assert regulator.command_evidence == dispatch_times
        assert regulator.command_ev_baseline == dispatch_ev
        assert not regulator.response_observed
    assert len(peer.requests) == count
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    # A falling economic ceiling now makes the desired point same-phase 11 A.
    # Phase lockout alone no longer forces the minimum: the unanswered fallback
    # must itself fence this physical increase from a lower old soft request.
    clock[0] = 601
    cohort(
        p,
        t,
        actual=2391,
        discharge=Fraction("3867.4"),
        pv=Fraction("1124.9"),
        load=Fraction("4467.3"),
        exported=Fraction("282.7"),
        times=(datetime.now(UTC) - timedelta(milliseconds=50),) * 6,
    )
    await evaluate(p, t)
    assert c.intent(t).energy_desired.point.mode.count == 1
    assert c.intent(t).energy_desired.point.current_a == 11
    assert c.confirmed_point(t).current_a == 6
    assert not regulator.response_observed
    assert len(peer.requests) == count
    # A genuinely answered fallback can subsequently authorize a fresh phase
    # retry through the existing task-local probe mechanism.
    peer.profile_response = None
    clock[0] = 610
    cohort(
        p,
        t,
        actual=1000,
        discharge=0,
        pv=8000,
        load=1500,
        exported=4000,
        times=(datetime.now(UTC) - timedelta(milliseconds=25),) * 6,
    )
    with c.phase_probe(t, enabled=True):
        assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert c.confirmed_point(t).mode.count == 3
    assert not c.phase_restricted(t)
