"""Confirmed PV offers survive lifecycle handovers and unknown regulation inputs."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_ocpp21_control import transaction
from test_ownership_recovery import prepared, reconnect
from test_profile_ownership import site as site
from test_pv_beta2 import prepare
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import CommandStatus


async def running(grid):
    p, t, context, clock = await prepare(grid, soc=96)
    p.setting(t)["pv_stop_delay"] = 90
    measurements(p, t, pv=2070, load=0, actual=0, soc=96)
    gate, parked = asyncio.Event(), asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    async def tick():
        gate.set()
        await asyncio.wait_for(parked.get(), 1)

    p.wait = wait
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    await asyncio.wait_for(parked.get(), 1)
    confirmed = context[0].confirmed_point(t)
    assert (confirmed.mode.count, confirmed.current_a) == (1, 9)
    return p, t, context, clock, tick


async def test_applied_start_transaction_handover_with_missing_phases(
    grid, monkeypatch
):
    p, t, (c, _, peer, *_), _, tick = await running(grid)
    confirmed = c.confirmed_point(t)
    request, generation = c.intent(t).request, c.intent(t).generation
    count = len(peer.requests)
    old_session = c.runtime.sessions.get(t).session_id
    inputs = c.inputs
    with monkeypatch.context() as patch:
        patch.setattr(
            c,
            "inputs",
            lambda target: replace(
                inputs(target), current_mode=None, eligible_modes=()
            ),
        )
        await transaction(peer, identity="started-after-applied", connector=1)
        assert c.runtime.sessions.get(t).session_id != old_session
        await tick()
        assert c.confirmed_point(t) == confirmed and p.pv_ongoing[t]
        assert c.intent(t).request == request
        assert c.intent(t).generation == generation
        assert len(peer.requests) == count and t not in p.pv_retry_until
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    await tick()
    assert c.confirmed_point(t).current_a == 10
    assert len(peer.requests) == count + 1


@pytest.mark.parametrize("gap", ["phase", "voltage", "capabilities"])
async def test_electrical_evidence_gap_holds_then_resumes(grid, monkeypatch, gap):
    p, t, (c, _, peer, _, source, _), _, tick = await running(grid)
    confirmed = c.confirmed_point(t)
    count, request = len(peer.requests), c.intent(t).request
    with monkeypatch.context() as patch:
        if gap == "phase":
            patch.setattr(source, "proof", False)
        elif gap == "capabilities":
            patch.setattr(source, "snapshot", None)
        else:
            voltage(c, t, 230, invalid="expired")
        await tick()
        assert c.confirmed_point(t) == confirmed and p.pv_ongoing[t]
        assert c.intent(t).request == request
        assert len(peer.requests) == count and t not in p.pv_retry_until
    if gap == "voltage":
        voltage(c, t, 230)
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    await tick()
    assert c.confirmed_point(t).current_a == 10


@pytest.mark.parametrize(
    "entity", ["sensor.pv", "sensor.load", "sensor.soc", "sensor.selected"]
)
@pytest.mark.parametrize("gap", ["unavailable", "stale"])
async def test_measurement_events_hold_confirmed_offer_without_invalidating_task(
    grid, entity, gap
):
    p, t, (c, _, peer, *_), clock, tick = await running(grid)
    confirmed, request = c.confirmed_point(t), c.intent(t).request
    count, generation, task = len(peer.requests), c.intent(t).generation, p.tasks[t]
    for seconds in (5, 15, 25):
        clock[0] = seconds
        state = p.hass.states.get(entity)
        attributes = dict(state.attributes)
        if gap == "stale":
            attributes["valid_until"] = (
                datetime.now(UTC) - timedelta(seconds=1)
            ).isoformat()
        p.hass.states.async_set(
            entity, "unavailable" if gap == "unavailable" else state.state, attributes
        )
        await asyncio.sleep(0)  # Run both real measurement and SoC listeners.
        await tick()
        assert c.confirmed_point(t) == confirmed and p.pv_ongoing[t]
        assert c.intent(t).request == request and c.intent(t).generation == generation
        assert p.tasks[t] is task and len(peer.requests) == count
        assert t not in p.pv_retry_until and t not in p.pv_stop_since
        measurements(p, t, pv=2070, load=0, actual=0, soc=96)
        await tick()
        assert c.confirmed_point(t) == confirmed and len(peer.requests) == count
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    await tick()
    assert c.confirmed_point(t).current_a == 10


@pytest.mark.parametrize("reason", ["surplus", "soc"])
async def test_real_stop_is_continuous_and_gap_restarts_its_delay(grid, reason):
    p, t, (c, _, peer, *_), clock, tick = await running(grid)
    confirmed, count = c.confirmed_point(t), len(peer.requests)

    def low():
        measurements(
            p,
            t,
            pv=0 if reason == "surplus" else 2070,
            load=0,
            actual=0,
            soc=89 if reason == "soc" else 96,
        )

    low()
    await tick()
    assert p.pv_stop_since[t] == 0
    clock[0] = 80
    p.hass.states.async_set("sensor.pv", "unavailable")
    await asyncio.sleep(0)
    await tick()
    assert c.confirmed_point(t) == confirmed and t not in p.pv_stop_since
    clock[0] = 100
    low()
    await tick()
    assert p.pv_stop_since[t] == 100
    clock[0] = 189
    await tick()
    assert len(peer.requests) == count and c.confirmed_point(t) == confirmed
    clock[0] = 190
    await tick()
    assert not c.confirmed_point(t).charging
    assert len(peer.requests) == count + 1
    assert c.runtime.enabled(t) is True


async def test_low_soc_with_phase_gap_cannot_bypass_stop_delay(grid, monkeypatch):
    p, t, (c, _, peer, _, source, _), clock, tick = await running(grid)
    confirmed, count = c.confirmed_point(t), len(peer.requests)
    with monkeypatch.context() as patch:
        patch.setattr(source, "proof", False)
        measurements(p, t, pv=2070, load=0, actual=0, soc=89)
        await asyncio.sleep(0)
        await tick()
        assert c.confirmed_point(t) == confirmed and len(peer.requests) == count
    clock[0] = 5
    await tick()
    assert p.pv_stop_since[t] == 5
    clock[0] = 94
    await tick()
    assert c.confirmed_point(t) == confirmed
    clock[0] = 95
    await tick()
    assert not c.confirmed_point(t).charging


async def test_recovery_before_deadline_resets_timer(grid):
    p, t, (c, _, peer, *_), clock, tick = await running(grid)
    confirmed, count = c.confirmed_point(t), len(peer.requests)
    measurements(p, t, pv=0, load=0, actual=0, soc=96)
    await tick()
    clock[0] = 89
    measurements(p, t, pv=2070, load=0, actual=0, soc=96)
    await tick()
    assert t not in p.pv_stop_since
    clock[0] = 100
    measurements(p, t, pv=0, load=0, actual=0, soc=96)
    await tick()
    assert p.pv_stop_since[t] == 100
    clock[0] = 189
    await tick()
    assert len(peer.requests) == count and c.confirmed_point(t) == confirmed


async def test_real_ended_session_resets_soc_hysteresis_even_between_ticks(grid):
    p, t, (c, _, peer, *_), _, tick = await running(grid)
    identity = c.runtime.sessions.get(t).external_transaction_id
    await transaction(peer, identity=identity, connector=1, kind="Ended")
    assert not p.pv_ongoing[t]
    await transaction(peer, identity="genuinely-new-session", connector=1)
    measurements(p, t, pv=2070, load=0, actual=0, soc=93)
    await tick()
    assert not p.pv_ongoing[t]
    assert not c.confirmed_point(t).charging
    assert p.status[t] == "waiting_battery_soc"


async def test_pv_transport_gap_retains_history_and_reconciles_without_off_on(
    site, monkeypatch
):
    owner, c, bound, peer, p, _ = await prepared(site)
    t = bound.target
    confirmed = c.confirmed_point(t)
    history = c._confirmed_points[t]
    desired = c.intent(t).request
    counts = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    connect = c.runtime.connect

    def reconnect_after_gap(*args, **kwargs):
        assert not c.runtime.get(t.station).connected
        assert c.confirmed_point(t) is None  # Historical proof is not live proof.
        assert c._confirmed_points[t] == history
        assert c.intent(t).request == desired and desired.target_w > 0
        assert len(peer.requests) == counts[0]
        return connect(*args, **kwargs)

    monkeypatch.setattr(c.runtime, "connect", reconnect_after_gap)
    await reconnect(owner, c, bound, peer, p)
    await asyncio.wait_for(asyncio.shield(p.recoveries[t]), 2)
    assert c.confirmed_point(t).same_setpoint(confirmed)
    assert c.recovery_status[t] == "point_adopted"
    assert p.pv_ongoing[t]
    assert counts == (
        len(peer.requests),
        len(peer.permissions),
        len(peer.authority_requests),
    )
