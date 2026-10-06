"""Explicit activation survives temporary preparation and initializes SoC once."""

import asyncio
from datetime import UTC, datetime

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.commands import CommandStatus


async def test_explicit_enable_initializes_fast_at_night(grid):
    p, t, (c, *_rest) = grid
    setup_optimum(p, t, soc=84, pv=0, load=500, actual=0)
    p.optimum_modes[t] = "PV_BALANCE"
    p.wait = lambda _: asyncio.Event().wait()
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert p.optimum_modes[t] == "FAST_DISCHARGE"
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).charging


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS"])
async def test_transaction_blocker_retains_enable_and_reconciles(grid, profile):
    p, t, (c, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.setting(t)["profile"] = profile
    clock = [0]
    p.monotonic = lambda: clock[0]
    gate, waiting = asyncio.Event(), asyncio.Event()

    async def wait(_):
        waiting.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    blocker = c.blocker
    c.blocker = lambda _: "transaction_unavailable"
    result = await p.permission(t, True)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert c.runtime.enabled(t) is False
    assert t in p.pv_startups
    await asyncio.wait_for(waiting.wait(), 2)
    task = p.tasks[t]
    c.blocker = blocker
    measurements(p, t, soc=96)
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(task, 2)
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS"])
async def test_pending_and_confirmed_enable_are_idempotent(grid, profile):
    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.setting(t)["profile"] = profile
    p.wait = lambda _: asyncio.Event().wait()
    blocker = c.blocker
    c.blocker = lambda _: "transaction_unavailable"
    await p.permission(t, True)
    generation = c.intent(t).generation
    worker = p.tasks[t]
    for _ in range(3):
        await p.permission(t, True)
    assert c.intent(t).generation == generation
    assert p.tasks[t] is worker
    await p.permission(t, False)
    assert t not in p.enable_requests and t not in p.pv_startups
    c.blocker = blocker
    await p.permission(t, True)
    generation = c.intent(t).generation
    commands = len(peer.requests), len(peer.permissions)
    await p.permission(t, True)
    assert c.intent(t).generation == generation
    assert (len(peer.requests), len(peer.permissions)) == commands
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize(
    "soc,expected", [(84, "FAST_DISCHARGE"), (80, "PV_BALANCE"), (79, "PV_BALANCE")]
)
async def test_activation_boundary_and_input_recovery(grid, soc, expected):
    p, t, _ = grid
    setup_optimum(p, t, soc=soc, pv=0)
    p.optimum_initialize(t)
    p.optimum_policy(t)
    assert p.optimum_modes[t] == expected
    p.optimum_modes[t] = "PV_BALANCE"
    p.invalidate(t)
    measurements(p, t, soc=84, pv=0)
    p.optimum_policy(t)
    assert p.optimum_modes[t] == "PV_BALANCE"
    p.hass.states.async_remove("sensor.pv")
    p.optimum_refresh(datetime.now(UTC))
    assert p.optimum_modes[t] == "PV_BALANCE"
    measurements(p, t, soc=84, pv=0)
    p.optimum_policy(t)
    assert p.optimum_modes[t] == "PV_BALANCE"


async def test_genuine_vehicle_return_initializes_fast(grid):
    from test_ocpp21_control import transaction

    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=84, pv=0, actual=0)
    p.optimum_initialize(t)
    p.optimum_policy(t)
    p.optimum_modes[t] = "PV_BALANCE"
    identity = c.runtime.sessions.get(t).external_transaction_id
    await transaction(peer, identity=identity, connector=1, kind="Ended")
    await transaction(peer, identity="new-activation", connector=1)
    p.optimum_policy(t)
    assert p.optimum_modes[t] == "FAST_DISCHARGE"


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS"])
async def test_enable_survives_permission_evidence_gap(grid, profile):
    from datetime import timedelta

    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    p, t, (c, bound, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.setting(t)["profile"] = profile
    clock = [0]
    p.monotonic = lambda: clock[0]
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    blocker = c.blocker
    c.blocker = lambda _: "transaction_unavailable"
    await p.permission(t, True)
    task = p.tasks[t]
    await asyncio.wait_for(parked.wait(), 2)
    for value in (None, False):
        now = datetime.now(UTC)
        c.runtime.observe_enabled(
            bound.token, EnabledObservation(t, value, now, now + timedelta(seconds=15))
        )
    assert p.pv_startup_valid(t, p.epochs[t])
    c.blocker = blocker
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(task, 2)
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS"])
async def test_enable_without_transaction_waits_for_new_session(grid, profile):
    from test_ocpp21_control import transaction

    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.setting(t)["profile"] = profile
    identity = c.runtime.sessions.get(t).external_transaction_id
    await transaction(peer, identity=identity, connector=1, kind="Ended")
    gate, parked = asyncio.Event(), asyncio.Event()
    clock = [0]
    p.monotonic = lambda: clock[0]

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    await p.permission(t, True)
    task = p.tasks[t]
    await asyncio.wait_for(parked.wait(), 2)
    # A repeated OFF read is not a new departure and cannot cancel this request.
    await c.adapter(t).read_enabled()
    assert t in p.enable_requests
    await transaction(peer, identity="arriving-car", connector=1)
    measurements(p, t, soc=96)
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(task, 2)
    assert c.runtime.enabled(t) is True


async def test_enable_drains_obsolete_point_and_coalesces_during_io(grid):
    from custom_components.wallbox_manager.control.requests import Direction

    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.wait = lambda _: asyncio.Event().wait()
    c._edit(t, {"target_w": 2000, "direction": Direction.DOWN})
    peer.release.clear()
    peer.received.clear()
    prior = asyncio.create_task(c.apply_stored(t, prepare=True))
    await asyncio.wait_for(peer.received.wait(), 2)
    enabling = asyncio.create_task(p.permission(t, True))
    await asyncio.sleep(0)
    generation = c.intent(t).generation
    await p.permission(t, True)
    assert c.intent(t).generation == generation
    peer.release.set()
    await asyncio.wait_for(prior, 2)
    await asyncio.wait_for(enabling, 2)
    assert c.runtime.enabled(t) is True and c.confirmed_point(t).charging


async def test_permission_readback_failure_is_terminal_not_optimistic(grid):
    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=96)
    peer.enabled_read_value = "false"
    result = await p.permission(t, True)
    assert result.status == CommandStatus.FAILED
    assert c.runtime.enabled(t) is False
    assert t not in p.enable_requests and t not in p.pv_startups


@pytest.mark.parametrize(
    "profile,soc", [("PV_OPTIMUM", 80), ("PV_OPTIMUM", 79), ("PV_SURPLUS", 96)]
)
async def test_enable_permission_can_coexist_with_night_pause(grid, profile, soc):
    p, t, (c, *_rest) = grid
    setup_optimum(p, t, soc=soc, pv=0, load=500, actual=0)
    p.setting(t)["profile"] = profile
    p.wait = lambda _: asyncio.Event().wait()
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).charging is False
    if profile == "PV_OPTIMUM":
        assert p.optimum_modes[t] == "PV_BALANCE"


@pytest.mark.parametrize("profile", ["PV_OPTIMUM", "PV_SURPLUS"])
async def test_first_busy_retains_enable_without_phase_inference(grid, profile):
    from ocpp.v21 import call_result

    from custom_components.wallbox_manager.control.commands import CommandReason

    p, t, (c, _, peer, *_rest) = grid
    setup_optimum(p, t, soc=96)
    p.setting(t)["profile"] = profile
    clock = [0]
    p.monotonic = lambda: clock[0]
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    peer.profile_response = lambda _: call_result.SetChargingProfile(status="Rejected")
    result = await p.permission(t, True)
    assert result.reason == CommandReason.BUSY
    assert not c.phase_restricted(t)
    task = p.tasks[t]
    await asyncio.wait_for(parked.wait(), 2)
    count = len(peer.requests)
    for tick in (5, 59):
        parked.clear()
        clock[0] = tick
        gate.set()
        await asyncio.wait_for(parked.wait(), 2)
        assert len(peer.requests) == count
    peer.profile_response = lambda _: call_result.SetChargingProfile(status="Accepted")
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(task, 2)
    assert c.runtime.enabled(t) is True
    assert not c.phase_restricted(t)
