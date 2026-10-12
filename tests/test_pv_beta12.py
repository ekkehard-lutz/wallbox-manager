"""PV stability, confirmed pause causes, phase probes and passive authority state."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_ownership_recovery import schedule
from test_pv_optimum_hold import configure, evaluate, period, phase_start, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.control.requests import Direction, PowerRequest
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.profiles import GridProfiles
from custom_components.wallbox_manager.protocols.ocpp.v21.authority import normalized
from custom_components.wallbox_manager.select import ChargingProfile
from custom_components.wallbox_manager.solver.operating_point import (
    OperatingPoint,
    Reason,
)
from custom_components.wallbox_manager.solver.power import solve


def authority(c, t, value):
    c.runtime.observe_authority(
        c.runtime.get(t.station).token,
        AuthorityObservation(t.station, value, datetime.now(UTC), "test-authority"),
    )


def stale_ev(p):
    state = p.hass.states.get("sensor.selected")
    p.hass.states.async_set(
        "sensor.selected",
        state.state,
        {
            **state.attributes,
            "observed_at": (datetime.now(UTC) - timedelta(seconds=91)).isoformat(),
        },
    )


async def test_passive_ev_timeout_cannot_obscure_local_authority(grid):
    p, t, (c, _, peer, *_), _ = configure(grid)
    authority(c, t, ControlAuthority.LOCAL)
    p.optimum_refresh(datetime.now(UTC))
    assert p.status.get(t) != "measurements_unavailable"
    stale_ev(p)
    p.optimum_refresh(datetime.now(UTC))
    assert p.status[t] == "measurements_unavailable"  # Genuine independent input gap.
    assert c.inputs(t).voltage.active_voltages(
        c.inputs(t).current_mode, datetime.now(UTC)
    )
    assert p.attributes(t)["profile_status"] == "no_authority"
    assert not peer.requests and not peer.authority_requests


# Exact accepted voltage bases from pv4 L1476/1486/1496/1506/1520/1530.
VOLTAGES = (
    (226.31692504882812, 227.44287109375, 227.8634796142578),
    (226.3306121826172, 227.53050231933594, 228.06057739257812),
    (226.25521850585938, 227.457763671875, 227.8492889404297),
    (226.3798370361328, 227.69989013671875, 228.0950927734375),
    (226.229248046875, 227.51002502441406, 227.9407501220703),
    (226.32044982910156, 227.59092712402344, 228.00634765625),
)
SOFT = Fraction("4771.836")


def voltages(c, t, values):
    now = datetime.now(UTC)
    state = c.runtime.get(t.station)
    changed = []
    for obs in state.observations:
        key = obs.channel.quantity.value
        if key in ("voltage_l1", "voltage_l2", "voltage_l3"):
            obs = replace(
                obs,
                value=Fraction(str(values[int(key[-1]) - 1])),
                observed_at=now,
                received_at=now,
                valid_until=now + timedelta(seconds=60),
            )
        changed.append(obs)
    c.runtime._publish(replace(state, observations=tuple(changed)))


async def oscillation_start(grid):
    p, t, context, clock, locked = await phase_start(grid)
    c, _, _, _, source, _ = context
    samples(p, t, actual=4140, discharge=3200, imported=1800)
    await evaluate(p, t)
    assert c.phase_restricted(t)
    p.setting(t)["optimum_max_discharge_w"] = 4500
    c.intent(t).current_limits[1] = 20
    clock[0] = 30
    samples(p, t, actual=3970, discharge=2800, pv=2000, load=4470)
    # Restore the historical soft request held during the real six-voltage run.
    p.optimum_regulators[t].requested = SOFT
    voltages(c, t, VOLTAGES[0])
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert c.confirmed_point(t).current_a == 7
    return p, t, context, clock, locked


async def test_exact_six_voltage_sequence_keeps_confirmed_current(grid):
    p, t, (c, _, peer, *_), clock, _ = await oscillation_start(grid)
    before = len(peer.requests)
    for index, value in enumerate(VOLTAGES):
        clock[0] = 30 + index * 2
        samples(p, t, actual=3970, discharge=2800, pv=2000, load=4470)
        voltages(c, t, value)
        result = await evaluate(p, t)
        assert result.status == CommandStatus.APPLIED
        assert c.confirmed_point(t).current_a == 7
        assert c.intent(t).energy_desired.point.mode.count == 3
        assert c.intent(t).solver_result.point.offered_power_w <= c.intent(t).hard_max_w
        assert p.optimum_regulators[t].requested == SOFT
        assert c.phase_restricted(t)
    assert len(peer.requests) == before  # Confirmed current is reused; no 6/7 chatter.


async def test_retained_current_reduces_to_exact_floor_without_budget_only_off(grid):
    p, t, (c, _, peer, *_), clock, _ = await oscillation_start(grid)
    clock[0] = 31  # Within the current response observation window.
    samples(p, t, actual=3970, discharge=5000, pv=2000, load=4470)
    before = len(peer.requests)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert c.confirmed_point(t).offered_power_w > c.intent(t).hard_max_w
    assert c.minimum_exception_valid(t, c.confirmed_point(t))
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 6


async def test_hard_budget_can_reduce_current_before_response_settles(grid):
    p, t, (c, _, peer, *_), clock, _ = await oscillation_start(grid)
    clock[0] = 31
    samples(p, t, actual=3970, discharge=3870, pv=2000, load=4470)
    before = len(peer.requests)
    await evaluate(p, t)
    assert c.intent(t).hard_max_w == 4500
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 6


async def test_meaningful_import_can_reduce_retained_current(grid):
    p, t, (c, _, peer, *_), clock, _ = await oscillation_start(grid)
    clock[0] = 60
    samples(p, t, actual=5298, discharge=4400, imported=1500, pv=2000, load=5798)
    before = len(peer.requests)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before + 1
    assert c.phase_restricted(t)


@pytest.mark.parametrize("gap", ["voltage", "power", "authority"])
async def test_retention_does_not_authorize_unknown_inputs_or_local_writes(grid, gap):
    p, t, (c, _, peer, *_), clock, _ = await oscillation_start(grid)
    clock[0] = 31
    if gap == "voltage":
        voltage(c, t, 230, invalid="expired")
    elif gap == "power":
        stale_ev(p)
    else:
        authority(c, t, ControlAuthority.LOCAL)
    before = len(peer.requests)
    await evaluate(p, t)
    assert all(period(x)["limit"] == 0 for x in peer.requests[before:])
    if gap == "authority":
        assert len(peer.requests) == before


@pytest.mark.parametrize(
    "constraint", ["hard", "current", "phase", "permission", "zero", "grid", "unknown"]
)
def test_solver_retention_requires_fresh_feasible_confirmed_point(
    capabilities, voltage, now, one, three, constraint
):
    old = OperatingPoint(True, three, 7, 4830, (230, 230, 230))
    kwargs = dict(
        current_mode=three,
        actively_charging=True,
        retained_point=old,
        soft_retention_w=100,
        hard_max_w=6000,
        limits=(),
    )
    modes = (one, three)
    req = PowerRequest(Fraction("4829.5"), Direction.DOWN, True)
    if constraint == "hard":
        kwargs["hard_max_w"] = 4820
    elif constraint == "current":
        kwargs["limits"] = (CurrentLimit(three, 0, 6, "vehicle"),)
    elif constraint == "phase":
        modes = (one,)
    elif constraint == "permission":
        req = PowerRequest(4829, Direction.DOWN, False)
    elif constraint == "zero":
        req = PowerRequest(0, Direction.DOWN, True)
    elif constraint == "grid":
        kwargs["retained_point"] = OperatingPoint(
            True, three, Fraction("6.5"), 4485, (230, 230, 230)
        )
        req = PowerRequest(4484, Direction.DOWN, True)
    else:
        kwargs["current_mode"] = None
    result = solve(req, capabilities, voltage, now=now, eligible_modes=modes, **kwargs)
    assert result.reason != Reason.CONFIRMED_RETAINED
    assert not result.point.same_setpoint(old)
    assert result.point.offered_power_w <= kwargs["hard_max_w"]
    if req.allowed and req.target_w:
        assert result.point.offered_power_w <= req.target_w


def test_down_without_existing_confirmation_never_rounds_up(
    capabilities, voltage, now, one, three
):
    result = solve(
        PowerRequest(Fraction("4829.5"), Direction.DOWN, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        soft_retention_w=100,
    )
    assert result.point.offered_power_w <= Fraction("4829.5")


async def balance_running(grid, soc=80.4):
    p, t, context, clock = configure(grid)
    p.setting(t)["pv_stop_delay"] = 90
    samples(p, t, soc=81.5, actual=2300, pv=3300, load=2800)
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    balance_cohort(p, t, clock, 0, soc=soc)
    await evaluate(p, t)
    assert p.pv_ongoing[t]
    assert p.status[t] == "optimum_minimum_hold"
    assert t not in p.pv_stop_since
    return p, t, context, clock


def balance_cohort(
    p,
    t,
    clock,
    second,
    *,
    soc=80.4,
    imported=2000,
    actual=1380,
    pv=1000,
    load=3400,
    discharge=400,
    exported=0,
):
    from test_pv_economy_control import cohort

    p.hass.states.async_set(
        p.references["soc_speicher_aktuell"], soc, {"unit_of_measurement": "%"}
    )
    cohort(
        p,
        t,
        clock,
        second,
        source=datetime.now(UTC),
        actual=actual,
        imported=imported,
        exported=exported,
        discharge=discharge,
        pv=pv,
        load=load,
    )


def advance_balance_budget(p, t, clock, end, *, soc=80.4):
    end = Fraction(str(end))
    while Fraction(str(clock[0])) < end:
        second = min(end, Fraction(str(clock[0])) + 10)
        balance_cohort(p, t, clock, second, soc=soc)
        p.economic_observe(t)


async def economic_off_ready(grid, soc=80.4):
    result = await balance_running(grid, soc)
    p, t, _, clock = result
    await p.hass.async_block_till_done()
    advance_balance_budget(p, t, clock, 90, soc=soc)
    assert p.economic_policies[t].energy_ws == 180000
    assert p.control.confirmed_point(t).charging
    return result


def verified_balance_recovery(p, t, clock, *, soc=80.4, actual=1380):
    beginning = Fraction(str(clock[0]))
    for offset in (1, 11, 17):
        balance_cohort(
            p,
            t,
            clock,
            beginning + offset,
            soc=soc,
            actual=actual,
            imported=0,
            exported=1000,
            pv=3000,
            load=1800 if actual else 500,
            discharge=0,
        )
        p.pv_request(t)
        p.economic_observe(t)
    assert p.economic_policies[t].energy_ws == 0


@pytest.mark.parametrize("soc", [80.4, 80.7])
async def test_economic_pause_keeps_cause_and_separates_restart_gate(grid, soc):
    p, t, (c, _, peer, *_), clock = await balance_running(grid, soc)
    before = len(peer.requests)
    advance_balance_budget(p, t, clock, "89.99", soc=soc)
    await evaluate(p, t)
    assert len(peer.requests) == before and p.pv_ongoing[t]
    advance_balance_budget(p, t, clock, 90, soc=soc)
    p.pv_edit(t)
    assert p.pv_ongoing[t]  # Planning OFF is not confirmation.
    assert c.confirmed_point(t).charging
    assert p.attributes(t)["pv_pause_reason"] is None
    await c.apply_stored(t, reuse_applied=True)
    p.pv_confirm(t)
    assert not c.confirmed_point(t).charging and not p.pv_ongoing[t]
    assert p.pv_pause_reasons[t] == "grid_import_energy_limit"
    assert p.economic_policies[t].paused
    assert len(peer.requests) == before + 1
    clock[0] = 95
    await evaluate(p, t)
    assert p.status[t] == "grid_import_energy_limit"
    assert p.economic_policies[t].paused
    assert p.attributes(t)["pv_restart_reason"] == "below_pv_start"
    assert t not in p.pv_soc_protection
    assert len(peer.requests) == before + 1


async def test_genuine_soc_protection_overrides_restart_gate_label(grid):
    p, t, (c, _, peer, *_), clock = await balance_running(grid)
    samples(p, t, soc=78.9, actual=1380, pv=1000, load=1800, discharge=400)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert p.status[t] == "stopped_battery_soc"
    assert p.attributes(t)["pv_restart_reason"] == "lower_soc_protection"
    assert t in p.pv_soc_protection


async def test_rejected_economic_off_preserves_active_continuation(grid):
    p, t, (c, _, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    p.pv_edit(t)
    peer.status = "Rejected"
    result = await c.apply_stored(t)
    p.pv_confirm(t)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert c.confirmed_point(t).charging and p.pv_ongoing[t]
    assert p.attributes(t)["pv_pause_reason"] is None
    assert p.optimum_modes[t] == "PV_BALANCE"
    peer.status = "Accepted"
    await evaluate(p, t)
    assert not p.pv_ongoing[t]
    assert p.pv_pause_reasons[t] == "grid_import_energy_limit"


@pytest.mark.parametrize("supersede", [False, True])
async def test_recovered_or_superseded_off_is_fenced_before_wire(grid, supersede):
    p, t, (c, bound, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    p.pv_edit(t)
    before = len(peer.requests)
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    lock = ObservedLock()
    bound.adapter._call_lock = lock
    await lock.acquire()
    task = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(queued.wait(), 2)
        verified_balance_recovery(p, t, clock)
        if supersede:
            c._edit(t, {"target_w": Fraction(2200)})
    finally:
        lock.release()
    result = await asyncio.wait_for(task, 2)
    p.pv_confirm(t)
    assert result.reason == CommandReason.STALE
    assert len(peer.requests) == before
    assert c.confirmed_point(t).charging and p.pv_ongoing[t]
    assert p.attributes(t)["pv_pause_reason"] is None
    await evaluate(p, t)
    assert p.pv_ongoing[t]


@pytest.mark.parametrize("dispatched", [False, True])
async def test_canceled_off_preserves_only_proven_continuation(grid, dispatched):
    p, t, (c, bound, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    p.pv_edit(t)
    before = len(peer.requests)
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    lock = ObservedLock()
    bound.adapter._call_lock = lock
    peer.received.clear()
    if dispatched:
        peer.release.clear()
    else:
        await lock.acquire()
    task = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for((peer.received if dispatched else queued).wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if lock.locked():
            lock.release()
        peer.release.set()
    p.pv_confirm(t)
    assert len(peer.requests) == before + int(dispatched)
    assert p.attributes(t)["pv_pause_reason"] is None
    assert p.pv_ongoing[t] is (not dispatched)
    assert bool(c.confirmed_point(t)) is (not dispatched)
    assert t not in c._unsent_economic_off
    assert p.economic_policies[t].energy_ws >= 180000


@pytest.mark.parametrize("accepted", [False, True])
async def test_superseded_dispatched_off_uses_actual_outcome(grid, accepted):
    p, t, (c, _, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    p.pv_edit(t)
    peer.status = "Accepted" if accepted else "Rejected"
    peer.received.clear()
    peer.release.clear()
    task = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        c._edit(t, {"target_w": Fraction(2200)})
    finally:
        peer.release.set()
    assert (await asyncio.wait_for(task, 2)).reason == CommandReason.STALE
    p.pv_confirm(t)
    assert (t in c._unconfirmed_targets) is accepted
    assert p.pv_ongoing[t] is (not accepted)
    assert bool(c.confirmed_point(t)) is (not accepted)
    assert p.attributes(t)["pv_pause_reason"] is None
    assert p.economic_policies[t].energy_ws >= 180000


async def test_economic_pause_can_restart_without_retaining_old_cause(grid):
    p, t, (c, _, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    await evaluate(p, t)
    verified_balance_recovery(p, t, clock, soc=81, actual=0)
    await evaluate(p, t)
    assert c.confirmed_point(t).charging and p.pv_ongoing[t]
    assert p.attributes(t)["pv_pause_reason"] is None
    assert p.attributes(t)["pv_restart_reason"] is None


async def test_sent_off_reset_keeps_request_and_late_ack_pause(grid):
    p, t, (c, _, peer, *_), clock = await economic_off_ready(grid)
    p.pv_edit(t)
    policy = p.economic_policies[t]
    before = len(peer.requests)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        assert policy.off_in_flight and policy.off_pending
        generation = c.intent(t).generation
        for second in (91, 101, 107):
            # Removing 1380 W EV load from 2000 W import leaves 620 W.
            # The battery-aware economic conditions have not improved.
            balance_cohort(
                p,
                t,
                clock,
                second,
                actual=0,
                imported=620,
                pv=1000,
                load=2020,
                discharge=400,
            )
            p.pv_request(t)
            p.economic_observe(t)
        assert policy.energy_ws == 0
        assert policy.off_pending and policy.off_in_flight
        assert policy.restart_reference is not None
        assert not p.pv_edit(t).point.charging
        assert c.intent(t).generation == generation
        assert len(peer.requests) == before + 1
    finally:
        peer.release.set()
    assert (await asyncio.wait_for(pending, 2)).status == CommandStatus.APPLIED
    p.pv_confirm(t)
    assert not c.confirmed_point(t).charging
    assert policy.energy_ws == 0 and policy.paused and policy.restart_required
    assert not policy.off_pending and not policy.off_in_flight
    assert policy.pause_reason == "grid_import_energy_limit"
    assert policy.restart_reference is not None


async def probe_loop(grid):
    p, t, context, clock, locked = await phase_start(grid)
    p.setting(t)["optimum_max_discharge_w"] = 4500
    samples(p, t, actual=3997, discharge=3030, pv=1600, load=4447)
    p.optimum_regulators[t].requested = Fraction(2300)
    queue = asyncio.Queue()
    gate = asyncio.Event()

    async def wait(_):
        queue.put_nowait(None)
        await gate.wait()
        gate.clear()

    p.wait = wait
    p.launch = GridProfiles.launch.__get__(p)
    p.launch(t)
    await asyncio.wait_for(queue.get(), 2)
    assert p.control.phase_restricted(t)
    assert p.pv_retry_until[t] == 60
    return p, t, context, clock, locked, queue, gate


async def test_rapid_target_edits_preserve_technical_probe_deadline(grid):
    p, t, (c, _, peer, *_), clock, locked, queue, gate = await probe_loop(grid)
    before = len(peer.requests)
    for i, target in enumerate([84, 83, 82, 81, 80]):
        clock[0] = 10 + i / 2
        samples(p, t, actual=3997, discharge=3030, pv=1600, load=4447)
        await p.set_value(t, "optimum_upper_soc", target)
        await asyncio.wait_for(queue.get(), 2)
        assert p.pv_retry_until[t] == 60
        assert p.pv_phase_retry[t]
        assert c.phase_restricted(t)
        assert c.confirmed_point(t).current_a == 6
        assert len(peer.requests) == before
    clock[0] = 60
    samples(p, t, actual=3997, discharge=3030, pv=1600, load=4447)
    locked[0] = False
    gate.set()
    await asyncio.wait_for(queue.get(), 2)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["number_phases"] == 1
    assert not c.phase_restricted(t)
    assert t not in p.pv_retry_until


@pytest.mark.parametrize("context", ["authority", "connection", "phase"])
async def test_changed_context_revokes_old_phase_probe_deadline(grid, context):
    p, t, (c, bound, peer, _, source, _), clock, _, queue, gate = await probe_loop(grid)
    before = len(peer.requests)
    clock[0] = 10
    if context == "authority":
        authority(c, t, ControlAuthority.LOCAL)
    elif context == "connection":
        c.runtime.disconnect(bound.token)
        c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
    else:
        source.mode = PhaseMode.canonical(1)
        p.pv_edit(t)  # New physical evidence prunes the technical deadline.
    assert not c.phase_restricted(t)
    assert t not in p.pv_retry_until
    assert t not in p.pv_phase_retry
    assert len(peer.requests) == before


async def test_generic_busy_deadline_is_not_a_phase_restriction(grid):
    p, t, (c, _, peer, *_), clock = await balance_running(grid)
    p.pv_retry_until[t] = 60
    p.pv_phase_retry[t] = False
    assert not c.phase_restricted(t)
    p.pv_edit(t)
    assert p.pv_retry_until[t] == 60  # Ordinary BUSY semantics stay separate.


@pytest.mark.parametrize(
    "value,expected",
    [
        (ControlAuthority.LOCAL, "no_authority"),
        (ControlAuthority.UNKNOWN, "authority_unknown"),
        (ControlAuthority.REMOTE, "measurements_unavailable"),
    ],
)
@pytest.mark.parametrize("bad", ["ev", "soc", "pv", "load", "battery", "grid"])
async def test_authority_precedes_independent_required_measurement_warning(
    grid, value, expected, bad
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    authority(c, t, value)
    entity = {
        "ev": "sensor.selected",
        "soc": "sensor.soc",
        "pv": "sensor.pv",
        "load": "sensor.load",
        "battery": p.references["storage_discharge_power"],
        "grid": p.references["grid_import_power"],
    }[bad]
    state = p.hass.states.get(entity)
    p.hass.states.async_set(entity, "unavailable", dict(state.attributes))
    p.optimum_refresh(datetime.now(UTC))
    attrs = ChargingProfile(c, p.entry_id, t).extra_state_attributes
    assert attrs["profile_status"] == expected
    assert attrs["pv_measurement_status"] == "measurements_unavailable"
    assert not peer.requests and not peer.authority_requests


@pytest.mark.parametrize(
    "value,expected",
    [
        (ControlAuthority.LOCAL, "no_authority"),
        (ControlAuthority.UNKNOWN, "authority_unknown"),
        (ControlAuthority.REMOTE, "idle"),
    ],
)
async def test_fresh_passive_telemetry_does_not_imply_control_permission(
    grid, value, expected
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    authority(c, t, value)
    p.optimum_refresh(datetime.now(UTC))
    assert p.attributes(t)["profile_status"] == expected
    assert p.attributes(t)["pv_measurement_status"] is None
    assert not peer.requests and not peer.authority_requests


async def test_authority_transitions_publish_promptly_and_clear_obsolete_input_state(
    grid,
):
    p, t, (c, _, peer, *_), clock = configure(grid)
    published = []
    unsubscribe = c.subscribe(
        lambda target: published.append(p.attributes(target)["profile_status"])
    )
    try:
        for value in [
            ControlAuthority.LOCAL,
            ControlAuthority.REMOTE,
            ControlAuthority.LOCAL,
            ControlAuthority.REMOTE,
        ]:
            before = len(published)
            authority(c, t, value)
            assert len(published) > before
            assert published[-1] == (
                "no_authority" if value == ControlAuthority.LOCAL else "idle"
            )
        stale_ev(p)
        p.optimum_refresh(datetime.now(UTC))
        assert p.attributes(t)["profile_status"] == "measurements_unavailable"
        authority(c, t, ControlAuthority.LOCAL)
        assert p.attributes(t)["profile_status"] == "no_authority"
        samples(p, t)
        p.optimum_refresh(datetime.now(UTC))
        assert p.attributes(t)["pv_measurement_status"] is None
        authority(c, t, ControlAuthority.REMOTE)
        assert p.attributes(t)["profile_status"] == "idle"
        assert not peer.requests and not peer.authority_requests
    finally:
        unsubscribe()


async def test_reconnect_never_exposes_old_authority_or_sends_commands(grid):
    p, t, (c, bound, peer, *_), clock = configure(grid)
    stale_ev(p)
    p.optimum_refresh(datetime.now(UTC))
    c.runtime.disconnect(bound.token)
    assert p.attributes(t)["profile_status"] == "disconnected"
    c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
    assert p.attributes(t)["profile_status"] == "authority_unknown"
    samples(p, t)
    p.optimum_refresh(datetime.now(UTC))
    assert p.attributes(t)["pv_measurement_status"] is None
    assert not peer.requests and not peer.authority_requests


async def test_reload_classifies_local_authority_without_old_warning_cache(grid):
    p, t, (c, _, peer, *_), clock = configure(grid)
    authority(c, t, ControlAuthority.LOCAL)
    stale_ev(p)
    p.optimum_refresh(datetime.now(UTC))
    assert p.pv_measurement_status[t] == "measurements_unavailable"
    samples(p, t)
    await p.close()
    clone = GridProfiles(
        p.hass, SimpleNamespace(entry_id=p.entry_id, options=p.references), c, p.battery
    )
    c.profiles = clone
    try:
        await clone.load()
        assert clone.attributes(t)["profile_status"] == "no_authority"
        assert clone.attributes(t)["pv_measurement_status"] is None
        assert not peer.requests and not peer.authority_requests
    finally:
        await clone.close()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Local", "local"),
        ("OCPP", "remote"),
        ("EMS", "unknown"),
        ("External", "unknown"),
    ],
)
def test_authority_labels_use_existing_protocol_normalization(value, expected):
    assert normalized(value).value == expected


@pytest.mark.parametrize("matching", [True, False])
async def test_confirmed_economic_pause_recovery_is_transaction_scoped(grid, matching):
    from test_ocpp21_control import transaction as new_transaction

    p, t, (c, _, peer, *_), clock = await economic_off_ready(grid)
    clock[0] = 90
    await evaluate(p, t)
    transaction = c.runtime.sessions.get(t).external_transaction_id
    record = {
        "enabled_intent": True,
        "continuation": False,
        "transaction": transaction,
        "pause_reason": "grid_import_energy_limit",
    }
    schedule(peer, c.confirmed_point(t))
    if not matching:
        # The actual live OCPP transaction must differ, rather than merely
        # changing a supplied ownership record while the Wh identity still fits.
        await new_transaction(peer, identity="another-session", connector=1)
    before = len(peer.requests)
    await p.close()
    clone = GridProfiles(
        p.hass, SimpleNamespace(entry_id=p.entry_id, options=p.references), c, p.battery
    )
    clone.launch = lambda *args, **kwargs: None
    c.profiles = clone
    try:
        await clone.load()
        await asyncio.wait_for(clone.recover(t, record), 2)
        point = c.confirmed_point(t)
        assert point is None or not point.charging
        if matching:
            assert clone.economic_policies[t].energy_ws == 180000
            assert (
                clone.attributes(t)["pv_economic_stop_reason"]
                == "grid_import_energy_limit"
            )
            assert clone.status[t] == "grid_import_energy_limit"
        else:
            assert t not in clone.economic_policies
            assert clone.economic_reasons[t] in (
                "economic_history_missing",
                "economic_history_identity_mismatch",
            )
        assert all(period(request)["limit"] == 0 for request in peer.requests[before:])
    finally:
        await clone.close()
