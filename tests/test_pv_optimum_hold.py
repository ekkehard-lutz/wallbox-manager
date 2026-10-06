"""Optimum pause policy through the real regulator, solver and OCPP lifecycle."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_diagnostics import records
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.models import PhaseMode


def period(request):
    return request[1]["charging_schedule"][0]["charging_schedule_period"][0]


def samples(p, t, *, actual=0, imported=0, discharge=0, soc=90, pv=4000, load=500):
    measurements(p, t, actual=actual, pv=pv, load=load, soc=soc)
    for key, value in (
        ("storage_discharge_power", discharge),
        ("grid_import_power", imported),
        ("grid_export_power", 0),
    ):
        p.hass.states.async_set(p.references[key], value, {"unit_of_measurement": "W"})


def configure(grid):
    p, t, context = grid
    setup_optimum(p, t, actual=0)
    samples(p, t)
    from homeassistant.util import dt as dt_util

    from custom_components.wallbox_manager.pv_optimum import PVDay

    p.optimum_days[t] = PVDay(
        dt_util.as_local(datetime.now(UTC)).date().isoformat(), "FINISHED"
    )
    clock = [0]
    p.monotonic = lambda: clock[0]
    p.setting(t)["pv_stop_delay"] = 10
    # Most cases explicitly drive policy/dispatch, avoiding uncontrolled background
    # ticks. The restart test below drives the real serialized regulation loop.
    p.launch = lambda *args, **kwargs: None
    p.wait = lambda _: asyncio.Event().wait()
    return p, t, context, clock


async def evaluate(p, t):
    plan = p.pv_edit(t)
    if plan is None:
        return None
    result = await p.control.apply_stored(t, reuse_applied=True)
    p.pv_confirm(t)
    return result


async def test_initial_floor_and_accepted_startup_settling_never_off(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert p.optimum_regulators[t].requested == 875  # Raw ramp is unchanged.
    assert period(peer.requests[-1]) == {
        "start_period": 0,
        "limit": 6,
        "number_phases": 1,
    }
    clock[0] = 5
    await evaluate(p, t)
    assert p.optimum_regulators[t].requested == Fraction("1531.25")
    clock[0] = 6
    samples(p, t, imported=1000)  # Actual power still lags at zero.
    await evaluate(p, t)
    assert p.optimum_regulators[t].requested == 0
    assert p.status[t] == "optimum_minimum_hold"
    assert c.confirmed_point(t).current_a == 6
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    assert c.runtime.enabled(t) is True


async def test_one_phase_ten_amps_reduces_to_floor_and_stays_there(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    samples(p, t, actual=2300, discharge=3500)
    await p.permission(t, True)
    assert c.confirmed_point(t).current_a == 10
    for seconds in range(1, 701, 10):
        clock[0] = seconds
        samples(p, t, actual=2300, imported=5000, discharge=3500)
        await evaluate(p, t)
        assert c.confirmed_point(t).current_a == 6
        assert p.optimum_modes[t] == "FAST_DISCHARGE"
        assert t not in p.pv_stop_since
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    assert len(peer.requests) == 2


async def phase_start(grid):
    p, t, context, clock = configure(grid)
    c, _, peer, _, source, _ = context
    source.mode = PhaseMode.canonical(3)
    c.restore(t, allowed_current_1p=16)
    samples(p, t, actual=6900, discharge=3500)
    await p.permission(t, True)
    assert (c.confirmed_point(t).mode.count, c.confirmed_point(t).current_a) == (3, 10)
    locked = [True]

    def respond(profile):
        phase = profile["charging_schedule"][0]["charging_schedule_period"][0].get(
            "number_phases"
        )
        return call_result.SetChargingProfile(
            status="Rejected" if locked[0] and phase == 1 else "Accepted",
            status_info={"reason_code": "PhaseSwitchLockout"}
            if locked[0] and phase == 1
            else None,
        )

    peer.profile_response = respond
    return p, t, context, clock, locked


async def test_specific_phase_rejection_floors_same_phase_and_probe_recovers(grid):
    p, t, (c, _, peer, _, source, _), clock, locked = await phase_start(grid)
    clock[0] = 1
    samples(p, t, imported=6000)
    before = len(peer.requests)
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert (
        len(peer.requests) == before + 2
    )  # Initial rejected transition, then safe floor.
    assert c.phase_restricted(t)
    assert period(peer.requests[-1])["number_phases"] == 3
    assert period(peer.requests[-1])["limit"] == 6
    before = len(peer.requests)
    for seconds in (2, 60, 300, 600):
        clock[0] = seconds
        await evaluate(p, t)
        assert c.minimum_positive(t).point.mode.count == 3
        assert c.phase_restricted(t)  # Elapsed retry time is not expiry evidence.
    assert len(peer.requests) == before
    locked[0] = False
    with c.phase_probe(t, enabled=True):
        assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert not c.phase_restricted(t)
    assert period(peer.requests[-1])["number_phases"] == 1
    source.mode = PhaseMode.canonical(1)
    assert all(period(r)["limit"] > 0 for r in peer.requests)


async def test_phase_probe_is_task_local_and_rejection_retains_evidence(grid):
    p, t, (c, _, peer, *_), clock, _ = await phase_start(grid)
    samples(p, t, imported=6000)
    await evaluate(p, t)
    clock[0] = 60
    before = len(peer.requests)
    with c.phase_probe(t, enabled=True):

        async def observe():
            return c.minimum_positive(t).point.mode.count

        assert await asyncio.create_task(observe()) == 3
        await evaluate(p, t)
    assert len(peer.requests) == before + 1  # Rejected probe; identical floor reused.
    assert c.phase_restricted(t)
    assert c.confirmed_point(t).mode.count == 3


async def test_available_transition_uses_existing_combined_profile(grid):
    p, t, (c, _, peer, *_), clock, locked = await phase_start(grid)
    locked[0] = False
    clock[0] = 1
    samples(p, t, actual=6900, discharge=3500, imported=4800)
    before = len(peer.requests)
    await evaluate(p, t)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1]) == {
        "start_period": 0,
        "limit": 9,
        "number_phases": 1,
    }
    assert c.confirmed_point(t).mode.count == 1


@pytest.mark.parametrize("proof", ["same_mode_only", "unknown_mode", "no_proof"])
async def test_unknown_transition_never_claims_supported_lower_phase_reachable(
    grid, proof
):
    p, t, (c, _, peer, _, source, _), _, _ = await phase_start(grid)
    if proof == "same_mode_only":
        source.phase_operation_evidence = lambda snapshot, mode: (
            snapshot.envelopes[0].evidence if mode == source.mode else None
        )
    elif proof == "unknown_mode":
        source.mode = None
    else:
        source.proof = False
    samples(p, t, imported=6000)
    before = len(peer.requests)
    result = await evaluate(p, t)
    if proof == "same_mode_only":
        assert result.status == CommandStatus.APPLIED
        assert period(peer.requests[-1])["number_phases"] == 3
        assert c.confirmed_point(t).current_a == 6
    else:
        assert result is None and len(peer.requests) == before
    assert all(period(r).get("number_phases") != 1 for r in peer.requests)


async def test_household_step_grace_then_floor_exposes_import(grid, caplog):
    import logging

    p, t, (c, _, peer, *_), clock = configure(grid)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    p.setting(t)["optimum_max_discharge_w"] = 4800
    samples(p, t, actual=3000, discharge=4800)
    await p.permission(t, True)
    for second in (0, 1, 2):
        clock[0] = second
        samples(p, t, actual=3000, discharge=1000, imported=3000)
        await evaluate(p, t)
        assert p.optimum_regulators[t].requested == 3000
    clock[0] = 3
    await evaluate(p, t)
    assert p.optimum_regulators[t].requested == 0
    assert c.confirmed_point(t).current_a == 6
    report = [r for r in records(caplog) if r.get("minimum_positive_hold")][-1]
    assert report["raw_regulator_target_w"] == 0
    assert report["observed_net_grid_import_w"] == 3000
    assert report["minimum_reachable_power_w"] == 1380
    assert all(period(r)["limit"] > 0 for r in peer.requests)


@pytest.mark.parametrize("stop_delay,expiry", [(10, 10), (0, 5)])
async def test_balance_transient_recovers_then_sustained_deficit_deliberately_pauses(
    grid, stop_delay, expiry
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    p.setting(t)["pv_stop_delay"] = stop_delay
    await p.permission(t, True)
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    assert p.optimum_modes[t] == "PV_BALANCE"
    assert p.status[t] == "optimum_pause_pending"
    clock[0] = expiry - 0.01
    samples(p, t, soc=80, pv=2000, load=0)
    await evaluate(p, t)
    assert t not in p.pv_stop_since
    clock[0] = 20
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    clock[0] = 20 + expiry - 0.01
    await evaluate(p, t)
    assert c.confirmed_point(t).charging
    clock[0] = 20 + expiry
    await evaluate(p, t)
    assert p.status[t] == "optimum_deliberate_pause"
    assert not c.confirmed_point(t).charging
    assert c.runtime.enabled(t) is True
    assert sum(period(r)["limit"] == 0 for r in peer.requests) == 1


async def test_existing_soc_hysteresis_controls_mode_and_cancels_pause(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    await p.permission(t, True)
    for second, soc, mode in [
        (1, 84, "FAST_DISCHARGE"),
        (2, 80, "PV_BALANCE"),
        (3, 85, "PV_BALANCE"),
        (4, 86, "FAST_DISCHARGE"),
    ]:
        clock[0] = second
        samples(p, t, soc=soc, pv=0, load=0, imported=6000)
        await evaluate(p, t)
        assert p.optimum_modes[t] == mode
        assert c.confirmed_point(t).charging
    assert t not in p.pv_stop_since
    assert all(period(r)["limit"] > 0 for r in peer.requests)


@pytest.mark.parametrize("gap", ["missing", "stale"])
async def test_input_gap_holds_and_breaks_pause_evidence(grid, gap):
    p, t, (c, _, peer, *_), clock = configure(grid)
    await p.permission(t, True)
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    clock[0] = 9
    if gap == "missing":
        p.hass.states.async_remove("sensor.pv")
    else:
        p.hass.states.async_set(
            "sensor.pv",
            0,
            {
                "unit_of_measurement": "W",
                "valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
    before, confirmed = len(peer.requests), c.confirmed_point(t)
    assert await evaluate(p, t) is None
    assert t not in p.pv_stop_since
    assert len(peer.requests) == before and c.confirmed_point(t) == confirmed
    clock[0] = 20
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    assert p.pv_stop_since[t] == 20 and c.confirmed_point(t).charging


async def test_enable_with_retained_positive_never_prepares_zero(grid):
    p, t, (c, _, peer, *_), _ = configure(grid)
    samples(p, t, actual=2300, discharge=3500)
    await p.permission(t, True)
    await p.permission(t, False)
    before = len(peer.requests)
    assert c.runtime.enabled(t) is False
    assert (
        period(peer.requests[-1])["limit"] == 10
    )  # Disabled permission retains current.
    samples(p, t)
    await p.permission(t, True)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 6
    assert c.runtime.enabled(t) is True


async def test_no_initial_decision_does_not_zero_retained_station_profile(grid):
    p, t, (c, _, peer, *_), _ = configure(grid)
    await p.permission(t, True)
    await p.permission(t, False)
    p.hass.states.async_remove("sensor.pv")
    before = len(peer.requests)
    assert (await p.permission(t, True)).status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == before and c.runtime.enabled(t) is False


@pytest.mark.parametrize(
    "event", ["disable", "authority", "limits", "ownership", "transaction"]
)
async def test_minimum_hold_never_overrides_hard_fences(grid, event):
    p, t, (c, bound, peer, _, source, _), _ = configure(grid)
    await p.permission(t, True)
    before = len(peer.requests)
    if event == "disable":
        await p.permission(t, False)
        assert c.runtime.enabled(t) is False
    elif event == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
        assert await evaluate(p, t) is None
    elif event == "limits":
        source.permitted = tuple(
            CurrentLimit(e.mode, 0, 5, "vehicle") for e in source.snapshot.envelopes
        )
        await evaluate(p, t)
        assert not c.confirmed_point(t).charging
        assert p.status[t] == "optimum_no_positive_point"
    elif event == "ownership":
        c.profile_permitted = lambda _: False
        await evaluate(p, t)
    else:
        from test_ocpp21_control import transaction

        await transaction(peer, connector=1, kind="Ended")
        await evaluate(p, t)
    assert len(peer.requests) == before + (event == "limits")


async def test_minimum_respects_device_grid_vehicle_limits_and_measured_voltage(grid):
    from test_voltage_command_fence import voltage

    p, t, (c, _, peer, _, source, _), _ = configure(grid)
    source.mode = PhaseMode.canonical(3)
    source.phase_operation_evidence = lambda snapshot, mode: (
        snapshot.envelopes[0].evidence if mode == source.mode else None
    )
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, min_current_a=7, current_step_a=2)
            for e in source.snapshot.envelopes
        ),
    )
    source.permitted = (CurrentLimit(source.mode, 8, 12, "vehicle"),)
    c.restore(t, allowed_current_3p=10)
    voltage(c, t, 220)
    await p.permission(t, True)
    point = c.confirmed_point(t)
    assert point.current_a == 9 and point.offered_power_w == 9 * (220 + 230 + 230)
    assert period(peer.requests[-1])["number_phases"] == 3


async def test_restart_busy_retries_fresh_targets_without_rearming(
    grid,
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    await p.permission(t, True)
    state = {"amps": 6, "until": None, "pauses": 0}
    attempts = []

    def station(profile):
        amps = profile["charging_schedule"][0]["charging_schedule_period"][0]["limit"]
        attempts.append((clock[0], amps))
        if amps > 0 and state["until"] is not None and clock[0] < state["until"]:
            return call_result.SetChargingProfile(status="Rejected")
        if amps == 0 and state["amps"] > 0:
            state["until"] = clock[0] + 600
            state["pauses"] += 1
        state["amps"] = amps
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = station
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    clock[0] = 10
    await evaluate(p, t)
    assert state["until"] == 610
    samples(p, t)
    gate, parked = asyncio.Event(), asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    async def tick(seconds):
        clock[0] = seconds
        gate.set()
        await asyncio.wait_for(parked.get(), 2)

    p.wait = wait
    task = asyncio.create_task(p.pv_sequence(t, p.epochs[t]))
    p.tasks[t] = task
    try:
        await asyncio.wait_for(parked.get(), 2)
        assert not c.phase_restricted(t)
        count = len(attempts)
        for second in (11, 30, 69):
            await tick(second)
            assert len(attempts) == count
        for second in range(70, 611, 60):
            await tick(second)
        assert c.confirmed_point(t).charging
        assert len({amps for _, amps in attempts if amps}) > 1  # Fresh ramped targets.
        samples(p, t, imported=1000)  # Delayed actual power after successful restart.
        await tick(611)
        assert c.confirmed_point(t).charging
        assert state["pauses"] == 1 and state["until"] == 610
        assert sum(amps == 0 for _, amps in attempts) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_economic_off_is_revalidated_but_explicit_off_still_wins(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    await p.permission(t, True)
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    clock[0] = 10
    assert not p.pv_edit(t).point.charging
    assert c.intent(t).policy_pause
    before = len(peer.requests)
    lock = c._point_locks[t]
    await lock.acquire()
    task = asyncio.create_task(c.apply_stored(t))
    await asyncio.sleep(0)
    samples(p, t, soc=90)  # A recovery before dispatch revokes an economic pause.
    lock.release()
    assert (await task).status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == before and c.confirmed_point(t).charging
    assert (await c.change(t, target_w=0)).status == CommandStatus.APPLIED
    assert period(peer.requests[-1])["limit"] == 0


async def test_lost_confirmation_is_not_proof_that_balance_is_already_off(grid):
    p, t, (c, _, peer, *_), _ = configure(grid)
    await p.permission(t, True)
    c._confirmed_points.pop(t)  # Same uncertainty as a failed control readback.
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    assert p.status[t] == "optimum_pause_pending"
    assert c.confirmed_point(t).charging
    assert all(period(r)["limit"] > 0 for r in peer.requests)


async def test_observer_gap_between_balance_ticks_resets_pause_evidence(grid):
    p, t, (c, _, _, *_), clock = configure(grid)
    await p.permission(t, True)
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    assert p.pv_stop_since[t] == 0
    clock[0] = 2
    p.hass.states.async_remove("sensor.remaining_pv_energy")
    p.optimum_refresh(datetime.now(UTC))
    assert t not in p.pv_stop_since
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", 4423.6, {"unit_of_measurement": "Wh"}
    )
    clock[0] = 5
    await evaluate(p, t)
    assert p.pv_stop_since[t] == 5 and c.confirmed_point(t).charging


async def test_missing_voltage_during_zero_budget_is_no_decision(grid):
    from test_voltage_command_fence import voltage

    p, t, (c, _, peer, *_), _ = configure(grid)
    await p.permission(t, True)
    before = len(peer.requests)
    samples(p, t, imported=6000)
    voltage(c, t, 230, invalid="expired")
    assert await evaluate(p, t) is None
    assert c.confirmed_point(t).charging and len(peer.requests) == before


async def test_no_decision_startup_retry_prepares_positive_after_inputs_recover(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    p.hass.states.async_remove("sensor.pv")
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()

    p.wait = wait
    await p.permission(t, True)
    task = p.tasks[t]
    await asyncio.wait_for(parked.wait(), 2)
    assert not peer.requests
    samples(p, t)
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(task, 2)
    assert c.runtime.enabled(t) is True and c.confirmed_point(t).charging
    assert all(period(r)["limit"] > 0 for r in peer.requests)


async def test_loop_phase_probes_are_bounded_and_same_phase_busy_still_backs_off(grid):
    p, t, (c, _, peer, *_), clock, locked = await phase_start(grid)
    response = peer.profile_response
    busy = [False]
    attempts = []

    def respond(profile):
        value = profile["charging_schedule"][0]["charging_schedule_period"][0]
        attempts.append((clock[0], value["number_phases"]))
        if busy[0] and value["number_phases"] == 3:
            return call_result.SetChargingProfile(status="Rejected")
        return response(profile)

    peer.profile_response = respond
    samples(p, t, imported=6000)
    gate, parked = asyncio.Event(), asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    async def tick(seconds):
        clock[0] = seconds
        gate.set()
        await asyncio.wait_for(parked.get(), 2)

    p.wait = wait
    task = asyncio.create_task(p.pv_sequence(t, p.epochs[t]))
    p.tasks[t] = task
    try:
        await asyncio.wait_for(parked.get(), 2)
        assert attempts == [(0, 1), (0, 3)]
        assert c.phase_restricted(t)
        await tick(1)
        assert len(attempts) == 2
        # Known phase restriction still permits prompt same-phase regulation.
        busy[0] = True
        samples(p, t, actual=6900)
        await tick(20)
        assert attempts[-1] == (20, 3)
        count = len(attempts)
        samples(
            p, t, actual=6000
        )  # Changing desired watts must not erase BUSY backoff.
        await tick(21)
        assert len(attempts) == count
        busy[0] = False
        samples(p, t, imported=6000)
        await tick(60)
        assert attempts[-1] == (60, 1)
        assert c.phase_restricted(t)
        locked[0] = False
        await tick(119)
        assert attempts[-1] == (60, 1)
        await tick(120)
        assert attempts[-1] == (120, 1)
        assert not c.phase_restricted(t) and c.confirmed_point(t).mode.count == 1
        assert all(period(r)["limit"] > 0 for r in peer.requests)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_pending_optimum_command_coalesces_settling_without_parallel_off(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    await p.permission(t, True)
    samples(p, t, actual=3000, discharge=3500)
    p.pv_edit(t)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        generation = c.intent(t).generation
        point = c.pending_points[t]
        count = len(peer.requests)
        clock[0] = 1
        samples(p, t, imported=1000)
        p.optimum_refresh(datetime.now(UTC))
        p.pv_edit(t)
        assert c.pending_points[t] == point
        assert c.intent(t).generation == generation
        assert len(peer.requests) == count
    finally:
        peer.release.set()
        await pending
    p.pv_confirm(t)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert all(period(r)["limit"] > 0 for r in peer.requests)
