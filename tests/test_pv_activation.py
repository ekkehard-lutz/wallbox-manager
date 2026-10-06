"""Explicit activation survives temporary preparation and initializes SoC once."""

import asyncio

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
