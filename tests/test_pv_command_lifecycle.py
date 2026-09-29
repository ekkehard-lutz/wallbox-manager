"""Transient refusals and pending transitions retain PV continuation."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import apply, prepare
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
)


@pytest.mark.parametrize("reason", [CommandReason.STALE, CommandReason.BUSY])
async def test_refusal_retains_one_phase_and_stop_timer(grid, monkeypatch, reason):
    p, t, (c, _, _, *_), clock = await prepare(grid)
    measurements(p, t, pv=4140, load=0, actual=0)
    await p.permission(t, True)
    confirmed = c.confirmed_point(t)
    assert confirmed.mode.count == 1 and confirmed.current_a == 18
    measurements(p, t, pv=3680, load=0, actual=0)
    p.pv_edit(t)
    with monkeypatch.context() as patch:
        patch.setattr(
            "custom_components.wallbox_manager.control.runtime.apply_operating_point",
            AsyncMock(
                return_value=CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED, reason=reason
                )
            ),
        )
        await c.apply_stored(t)
    p.pv_confirm(t)
    assert c.confirmed_point(t) == confirmed
    assert p.pv_ongoing[t]
    p.setting(t)["pv_stop_delay"] = 90
    measurements(p, t, pv=100, load=0, actual=0)
    assert await apply(p, t) == confirmed
    assert p.pv_stop_since[t] == 0
    clock[0] = 89
    assert await apply(p, t) == confirmed
    measurements(p, t, pv=4140, load=0, actual=0)
    await apply(p, t)
    assert t not in p.pv_stop_since
    measurements(p, t, pv=100, load=0, actual=0)
    await apply(p, t)
    assert p.pv_stop_since[t] == 89
    clock[0] = 179
    assert not (await apply(p, t)).charging
    assert c.runtime.enabled(t) is True


async def test_pending_phase_target_coalesces_measurements_without_overtaking(grid):
    p, t, (c, _, peer, *_), _ = await prepare(grid)
    c.restore(t, allowed_current_1p=18)
    measurements(p, t, pv=4140, load=0, actual=0)
    await p.permission(t, True)
    await asyncio.sleep(0)
    confirmed = c.confirmed_point(t)
    measurements(p, t, pv=6210, load=0, actual=0)
    p.pv_edit(t)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        generation = c.intent(t).generation
        count = len(peer.requests)
        assert c.pending_points[t].mode.count == 3
        for watts in (3680, 8280, 7590):
            measurements(p, t, pv=watts, load=0, actual=0)
            p.pv_edit(t)
            await asyncio.sleep(0)
            assert c.intent(t).generation == generation
            assert c.confirmed_point(t) == confirmed
            assert len(peer.requests) == count
    finally:
        peer.release.set()
        await pending
    assert t not in c.pending_points
    # Fresh measurements are coalesced through the next normal regulation tick.
    assert (await apply(p, t)).offered_power_w == 7590


@pytest.mark.parametrize("reason", [CommandReason.STALE, CommandReason.BUSY])
async def test_retry_deadline_survives_changing_desired_target(
    grid, monkeypatch, reason
):
    p, t, (c, _, _, *_), clock = await prepare(grid)
    await p.permission(t, True)
    await asyncio.sleep(0)
    confirmed = c.confirmed_point(t)
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    # Resume the existing regulator without invalidating its continuation state.
    task = p.tasks.pop(t)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    p.wait = wait
    operation = AsyncMock(
        return_value=CommandResult(CommandStatus.TEMPORARILY_REJECTED, reason=reason)
    )
    monkeypatch.setattr(
        "custom_components.wallbox_manager.control.runtime.apply_operating_point",
        operation,
    )
    measurements(p, t, pv=5000, load=0, actual=0)
    p.launch(t)
    await asyncio.wait_for(parked.wait(), 1)
    assert p.pv_retry_until[t] == 60
    for seconds, watts in ((5, 4500), (10, 7000), (59, 5500)):
        parked.clear()
        clock[0] = seconds
        measurements(p, t, pv=watts, load=0, actual=0)
        gate.set()
        await asyncio.wait_for(parked.wait(), 1)
        assert operation.await_count == 1
        assert c.confirmed_point(t) == confirmed and p.pv_ongoing[t]
    parked.clear()
    clock[0] = 60
    gate.set()
    await asyncio.wait_for(parked.wait(), 1)
    assert operation.await_count == 2


async def test_accepted_reply_survives_new_surplus_until_next_cycle(grid):
    p, t, (c, _, peer, *_), _ = await prepare(grid)
    measurements(p, t, pv=4140, load=0, actual=0)
    await p.permission(t, True)
    await asyncio.sleep(0)
    confirmed = c.confirmed_point(t)
    measurements(p, t, pv=4600, load=0, actual=0)
    p.pv_edit(t)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t, reuse_applied=True))
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        # The accepted increase is no longer permitted by fresh PV policy.
        measurements(p, t, pv=4140, load=0, actual=0)
    finally:
        peer.release.set()
        result = await pending
    assert result.status == CommandStatus.APPLIED
    p.pv_confirm(t)
    assert c.confirmed_point(t).current_a == 20 and p.pv_ongoing[t]
    assert c.intent(t).fence_reason is None
    count = len(peer.requests)
    assert await apply(p, t) == confirmed
    assert len(peer.requests) == count + 1  # Next cycle applies the new 18 A decision.
    assert t not in c._unconfirmed_targets
