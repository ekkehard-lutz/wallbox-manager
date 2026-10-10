"""Temporary phase rejection preserves the confirmed charging session safely."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_ocpp21_control import transaction
from test_pv_optimum_hold import configure, evaluate, period, phase_start, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.models import PhaseMode

PROFILES = ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")


def desired_one_phase(p, t, *, imported=1840, soc=90, actual=4140):
    # EV consumption belongs to total site load. Both FAST's import correction
    # and SURPLUS's available-power calculation request exactly 2300 W here.
    samples(
        p,
        t,
        actual=actual,
        discharge=3300,
        imported=imported,
        soc=soc,
        pv=2300,
        load=actual,
    )


def exported(p, watts):
    p.hass.states.async_set(
        p.references["grid_export_power"],
        watts,
        {
            "unit_of_measurement": "W",
            "observed_at": p.hass.states.get("sensor.selected").attributes[
                "observed_at"
            ],
        },
    )


def incident_ev_zero(p, t, clock, *, second):
    # Hardware's EV source temporarily reported zero after the accepted phase
    # transition. The first complete site generation clamps the soft request;
    # the second restores sufficient genuine battery budget but no EV response.
    clock[0] = second
    if second == 1:
        samples(p, t, actual=0, discharge=1351.6, pv=3349.3, load=4246.2)
        exported(p, 282.8)
        p.pv_request(t)
        assert float(p.control.intent(t).hard_max_w) == 2331.2
    else:
        samples(p, t, actual=0, discharge=1021.3, pv=3355.2, load=351)
        exported(p, 3849)


async def restricted(grid, profile):
    p, t, context, clock, locked = await phase_start(grid)
    desired_one_phase(p, t)
    await evaluate(p, t)
    assert context[0].phase_restricted(t)
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    clock[0] = 30
    desired_one_phase(p, t)
    await evaluate(p, t)
    return p, t, context, clock, locked


async def incident_start(grid, profile):
    p, t, context, clock = configure(grid)
    c, _, peer, _, source, _ = context
    p.setting(t).update(profile="NETZ", soll_soc_speicher=80)
    c.restore(t, allowed_current_1p=19)
    samples(p, t, actual=4370, discharge=3400, pv=4370, load=4370)
    assert (
        await asyncio.wait_for(c.change(t, target_w=4370, allowed=True), 2)
    ).status == CommandStatus.APPLIED
    assert period(peer.requests[-1]) == {
        "start_period": 0,
        "limit": 19,
        "number_phases": 1,
    }
    # The incident's accepted operating points use the same real solver and
    # OCPP acknowledgements as the subsequent PV policy/fallback command.
    p.setting(t)["profile"] = profile
    p.optimum_initialize(t)
    p.pv_request(t)
    p.setting(t)["profile"] = "NETZ"
    assert (
        await asyncio.wait_for(c.change(t, target_w=4830), 2)
    ).status == CommandStatus.APPLIED
    source.mode = PhaseMode.canonical(3)
    p.setting(t)["profile"] = profile
    p.status[t] = "actively_charging"
    p.pv_confirm(t)
    assert period(peer.requests[-1]) == {
        "start_period": 0,
        "limit": 7,
        "number_phases": 3,
    }
    return p, t, context, clock


@pytest.mark.parametrize("profile", PROFILES)
async def test_incident_sequence_rejected_reverse_continues_without_off(grid, profile):
    p, t, (c, _, peer, _, source, _), clock = await incident_start(grid, profile)
    session = c.runtime.sessions.get(t).session_id

    def reject_reverse(profile):
        phases = profile["charging_schedule"][0]["charging_schedule_period"][0][
            "number_phases"
        ]
        return call_result.SetChargingProfile(
            status="Rejected" if phases == 1 else "Accepted",
            status_info={"reason_code": "PhaseSwitchLockout"} if phases == 1 else None,
        )

    peer.profile_response = reject_reverse
    # Expire physical-response settling so this test reaches an actual rejected
    # transition; the immediate reverse-request guard is tested separately.
    incident_ev_zero(p, t, clock, second=1)
    incident_ev_zero(p, t, clock, second=21)
    before = len(peer.requests)
    result = await evaluate(p, t)
    assert result.status == CommandStatus.APPLIED, (
        result,
        c.intent(t).status,
        c.intent(t).fence_reason,
        [period(r) for r in peer.requests],
        p.status[t],
    )
    assert [
        (period(r)["number_phases"], period(r)["limit"]) for r in peer.requests[before:]
    ] == [(1, 10), (3, 6)]
    assert c.confirmed_point(t).mode == source.mode
    assert c.confirmed_point(t).current_a == 6
    assert float(c.intent(t).hard_max_w) == 6227.7
    assert c.phase_restricted(t)
    assert c.runtime.enabled(t) is True
    assert c.runtime.sessions.get(t).session_id == session
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    count = len(peer.requests)
    for second in (22, 30, 59, 600):
        clock[0] = second
        desired_one_phase(p, t)
        await evaluate(p, t)
        assert len(peer.requests) == count
        assert c.confirmed_point(t).current_a == 6


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("imported", [0, 2069, 2070])
async def test_lockout_minimum_accepts_up_to_half_its_power(grid, profile, imported):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    session = c.runtime.sessions.get(t).session_id
    count = len(peer.requests)
    for second in (31, 45, 90):
        clock[0] = second
        desired_one_phase(p, t, imported=imported)
        await evaluate(p, t)
        assert c.confirmed_point(t).offered_power_w == 4140
        assert c.runtime.enabled(t) is True
        assert c.runtime.sessions.get(t).session_id == session
    assert len(peer.requests) == count


@pytest.mark.parametrize("profile", PROFILES)
async def test_lockout_import_excess_is_transient_then_stops_once(grid, profile):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    before = len(peer.requests)
    for second in (31, 40.99):
        clock[0] = second
        desired_one_phase(p, t, imported=2071)
        await evaluate(p, t)
        assert c.confirmed_point(t).charging
    clock[0] = 41
    desired_one_phase(p, t, imported=2071)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert c.runtime.enabled(t) is True
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0
    clock[0] = 42
    await evaluate(p, t)
    assert len(peer.requests) == before + 1


@pytest.mark.parametrize("profile", PROFILES)
async def test_lockout_soc_exact_target_continues_but_below_target_stops(grid, profile):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    target = float(p.pv_target(t, datetime.now(UTC)))
    before = len(peer.requests)
    clock[0] = 31
    desired_one_phase(p, t, soc=target)
    await evaluate(p, t)
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before
    clock[0] = 32
    desired_one_phase(p, t, soc=target - 0.001)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert p.status[t] == "stopped_battery_soc"
    assert c.runtime.enabled(t) is True
    assert len(peer.requests) == before + 1


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    "channel", ["grid_import_power", "grid_export_power", "soc", "ev"]
)
@pytest.mark.parametrize("gap", ["unavailable", "stale"])
async def test_lockout_does_not_extend_through_invalid_critical_measurements(
    grid, profile, channel, gap
):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    entity = (
        "sensor.soc"
        if channel == "soc"
        else "sensor.selected"
        if channel == "ev"
        else p.references[channel]
    )
    state = p.hass.states.get(entity)
    attributes = dict(state.attributes)
    if gap == "stale":
        attributes["valid_until"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    p.hass.states.async_set(
        entity, "unavailable" if gap == "unavailable" else state.state, attributes
    )
    clock[0] = 31
    before = len(peer.requests)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0


@pytest.mark.parametrize("profile", PROFILES)
async def test_lockout_keeps_independent_battery_budget_safety_fence(grid, profile):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    clock[0] = 31
    samples(p, t, actual=4140, discharge=4000, imported=1800)
    before = len(peer.requests)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert len(peer.requests) == before + 1
    assert p.status[t] == "hard_budget_pause"


@pytest.mark.parametrize("fence", ["authority", "ownership", "transaction", "disable"])
async def test_lockout_never_overrides_live_control_fences(grid, fence):
    p, t, (c, bound, peer, *_), _, _ = await restricted(grid, "PV_OPTIMUM")
    before = len(peer.requests)
    if fence == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    elif fence == "ownership":
        c.profile_permitted = lambda _: False
    elif fence == "transaction":
        await transaction(peer, connector=1, kind="Ended")
    else:
        await p.permission(t, False)
        assert c.runtime.enabled(t) is False
    await evaluate(p, t)
    assert len(peer.requests) == before


async def test_changed_connection_generation_revokes_learned_lockout(grid):
    p, t, (c, bound, peer, *_), _, _ = await restricted(grid, "PV_OPTIMUM")
    token = bound.token
    before = len(peer.requests)
    c.runtime.disconnect(token)
    c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
    assert c.runtime.get(t.station).token != token
    assert not c.phase_restricted(t)
    assert await evaluate(p, t) is None
    assert len(peer.requests) == before


async def test_lockout_minimum_uses_limits_grid_steps_and_measured_voltage(grid):
    p, t, (c, _, peer, _, source, _), clock, _ = await restricted(grid, "PV_OPTIMUM")
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
    clock[0] = 31
    samples(p, t, actual=6120, discharge=3300, imported=3060)
    await evaluate(p, t)
    point = c.confirmed_point(t)
    assert point.mode == source.mode
    assert point.current_a == 9
    assert point.offered_power_w == 9 * (220 + 230 + 230)
    assert period(peer.requests[-1])["limit"] == 9
    before = len(peer.requests)
    clock[0] = 32
    samples(p, t, actual=6120, discharge=3300, imported=point.offered_power_w / 2)
    await evaluate(p, t)
    assert c.confirmed_point(t).charging
    assert len(peer.requests) == before
    source.permitted = (CurrentLimit(source.mode, 0, 5, "vehicle"),)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging


@pytest.mark.parametrize("profile", PROFILES)
async def test_periodic_lockout_probe_uses_fresh_desire_and_preserves_session(
    grid, profile
):
    p, t, (c, _, peer, _, source, _), clock, locked = await restricted(grid, profile)
    session = c.runtime.sessions.get(t).session_id
    gate, parked = asyncio.Event(), asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    async def tick(second):
        clock[0] = second
        desired_one_phase(p, t)
        gate.set()
        await asyncio.wait_for(parked.get(), 2)

    p.wait = wait
    task = asyncio.create_task(p.pv_sequence(t, p.epochs[t]))
    p.tasks[t] = task
    try:
        await asyncio.wait_for(parked.get(), 2)
        before = len(peer.requests)
        assert p.pv_retry_until[t] == 90
        for second in (31, 59, 89):
            await tick(second)
            assert len(peer.requests) == before
        locked[0] = False
        await tick(90)
        assert len(peer.requests) == before + 1
        assert period(peer.requests[-1])["number_phases"] == 1
        assert not c.phase_restricted(t)
        source.mode = PhaseMode.canonical(1)
        assert c.runtime.sessions.get(t).session_id == session
        assert c.runtime.enabled(t) is True
        assert all(period(r)["limit"] > 0 for r in peer.requests)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("profile", PROFILES)
async def test_lockout_ev_zero_response_does_not_apply_a_startup_reserve(grid, profile):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, profile)
    clock[0] = 51
    samples(p, t, actual=0, discharge=1021, pv=3355, load=351)
    p.hass.states.async_set(
        p.references["grid_export_power"],
        3849,
        {
            "unit_of_measurement": "W",
            "observed_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    before = len(peer.requests)
    await evaluate(p, t)
    # 6228 W is independently affordable, but reserving half the response at
    # EV=0 would incorrectly reject an established 4140 W three-phase floor.
    assert 4140 <= c.intent(t).hard_max_w < 8280
    assert c.confirmed_point(t).mode.count == 3
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == before


@pytest.mark.parametrize("conflict", ["grid", "ev"])
async def test_lockout_conflicting_measurements_stop_safely(grid, conflict):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, "PV_OPTIMUM")
    clock[0] = 31
    if conflict == "grid":
        p.hass.states.async_set(
            p.references["grid_export_power"], 1200, {"unit_of_measurement": "W"}
        )
    else:
        state = p.hass.states.get("sensor.selected")
        p.hass.states.async_set("sensor.duplicate_ev", 1000, dict(state.attributes))
    before = len(peer.requests)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert len(peer.requests) == before + 1


async def test_lockout_below_target_soc_can_stop_when_physical_phase_is_unknown(grid):
    p, t, (c, _, peer, _, source, _), clock, _ = await restricted(grid, "PV_OPTIMUM")
    source.mode = None
    clock[0] = 31
    desired_one_phase(p, t, soc=79.999)
    before = len(peer.requests)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert p.status[t] == "stopped_battery_soc"
    assert period(peer.requests[-1]) == {"start_period": 0, "limit": 0}
    assert len(peer.requests) == before + 1


@pytest.mark.parametrize("grid_mapping", ["fresh", "missing"])
async def test_surplus_lockout_without_discharge_mapping_still_requires_grid(
    grid, grid_mapping
):
    p, t, (c, _, peer, *_), clock, _ = await restricted(grid, "PV_SURPLUS")
    clock[0] = 31
    desired_one_phase(p, t, soc=80, imported=2070)
    p.references.pop("storage_discharge_power")
    if grid_mapping == "missing":
        p.references.pop("grid_import_power")
    before = len(peer.requests)
    await evaluate(p, t)
    assert c.confirmed_point(t).charging is (grid_mapping == "fresh")
    assert c.intent(t).hard_max_w is None
    assert len(peer.requests) == before + (grid_mapping == "missing")


@pytest.mark.parametrize("profile", PROFILES)
async def test_phase_ack_without_physical_response_prevents_immediate_reverse(
    grid, profile
):
    p, t, (c, _, peer, *_), clock = await incident_start(grid, profile)
    incident_ev_zero(p, t, clock, second=1)
    incident_ev_zero(p, t, clock, second=2)
    before = len(peer.requests)
    result = await evaluate(p, t)
    assert c.intent(t).fence_reason is None
    assert c.confirmed_point(t).mode.count == 3
    assert c.confirmed_point(t).current_a == 6, (
        result,
        p.status[t],
        c.intent(t).fence_reason,
        p.optimum_regulators[t].response_settling(clock[0]),
        p.optimum_regulators[t].requested,
        [period(r) for r in peer.requests],
    )
    assert len(peer.requests) == before + 1
    assert all(period(r).get("number_phases") == 3 for r in peer.requests[before:])
    assert all(period(r)["limit"] > 0 for r in peer.requests)
    # Physical settling never prevents an independently required safe reduction.
    samples(p, t, actual=0, discharge=4000, pv=3355, load=351)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging


@pytest.mark.parametrize("profile", PROFILES)
async def test_fresh_physical_response_releases_phase_settling_before_timeout(
    grid, profile
):
    p, t, (c, _, peer, *_), clock = await incident_start(grid, profile)
    incident_ev_zero(p, t, clock, second=1)
    clock[0] = 2
    desired_one_phase(p, t, actual=4830, imported=2530)
    before = len(peer.requests)
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["number_phases"] == 1
    assert period(peer.requests[-1])["limit"] == 10
    assert all(period(r)["limit"] > 0 for r in peer.requests)
