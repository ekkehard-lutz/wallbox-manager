"""Fresh desired phase stays separate from temporary executable phase holds."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_diagnostics import records
from test_pv_optimum_hold import evaluate, period, phase_start, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.pv_diagnostics import cycle


def set_budget(p, t, amps):
    # Real measured deficit calculation, no mocked regulator or solver.
    samples(p, t, actual=4140, discharge=3300, imported=4140 - 230 * amps)


def selected(result):
    return result.point.mode.count, result.point.current_a


async def test_blocked_desire_and_hold_are_distinct_and_diagnosed(grid, caplog):
    p, t, (c, _, peer, *_), clock, _ = await phase_start(grid)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    clock[0] = 1
    set_budget(p, t, 10)
    with cycle(p, t, "regulation"):
        await evaluate(p, t)
    assert selected(c.intent(t).energy_desired) == (1, 10)
    assert selected(c.intent(t).solver_result) == (3, 6)
    assert c.phase_restricted(t)
    count = len(peer.requests)
    for second, amps in ((30, 10), (40, 11), (50, 10)):
        clock[0] = second
        set_budget(p, t, amps)
        p.optimum_refresh(datetime.now(UTC))
        with cycle(p, t, "regulation"):
            await evaluate(p, t)
        assert p.optimum_regulators[t].requested == 230 * amps
        assert selected(c.intent(t).energy_desired) == (1, amps)
        assert selected(c.intent(t).solver_result) == (3, 6)
    assert len(peer.requests) == count
    line = records(caplog)[-1]
    assert line["raw_regulator_target_w"] == 2300
    assert line["desired"]["phases"] == 1
    assert line["desired"]["current_a"] == 10
    assert line["executable"]["phases"] == 3
    assert line["executable"]["current_a"] == 6
    assert line["phase_transition_blocked"]
    assert line["minimum_positive_hold"]
    assert line["decision"] == "WAIT_PHASE_LOCKOUT"
    assert all(period(r)["limit"] > 0 for r in peer.requests)


@pytest.mark.parametrize("recover", [False, True])
async def test_automatic_probe_uses_latest_desire_or_stays_three_phase(grid, recover):
    p, t, (c, _, peer, *_), clock, locked = await phase_start(grid)
    set_budget(p, t, 10)
    gate, parked = asyncio.Event(), asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    async def tick(second):
        clock[0] = second
        gate.set()
        await asyncio.wait_for(parked.get(), 2)

    p.wait = wait
    task = asyncio.create_task(p.pv_sequence(t, p.epochs[t]))
    p.tasks[t] = task
    before = len(peer.requests)
    try:
        await asyncio.wait_for(parked.get(), 2)
        assert [
            (period(r)["number_phases"], period(r)["limit"])
            for r in peer.requests[before:]
        ] == [(1, 10), (3, 6)]
        assert p.pv_retry_until[t] == 60
        for second in range(1, 10):
            set_budget(p, t, 10)
            p.optimum_refresh(datetime.now(UTC))
            await tick(second)
            assert selected(c.intent(t).energy_desired) == (1, 10)
            assert len(peer.requests) == before + 2
            assert p.pv_retry_until[t] == 60
        if recover:
            samples(p, t, actual=4140, discharge=1900)
            await tick(30)
            assert selected(c.intent(t).energy_desired) == (3, 7)
            assert c.confirmed_point(t).current_a == 7
            count = len(peer.requests)
            for second in (60, 120, 180):
                await tick(second)
                assert selected(c.intent(t).energy_desired) == (3, 7)
            assert len(peer.requests) == count
            assert all(
                period(r)["number_phases"] == 3 for r in peer.requests[before + 1 :]
            )
        else:
            set_budget(p, t, 11)
            await tick(59)
            assert len(peer.requests) == before + 2
            locked[0] = False
            await tick(60)
            assert period(peer.requests[-1])["number_phases"] == 1
            assert period(peer.requests[-1])["limit"] == 11
            assert c.confirmed_point(t).current_a == 11
            assert not c.phase_restricted(t)
        assert all(period(r)["limit"] > 0 for r in peer.requests)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("recover_phase", [False, True])
async def test_probe_revalidates_desired_phase_at_transport_lock(grid, recover_phase):
    p, t, (c, adapter, peer, *_), clock, locked = await phase_start(grid)
    set_budget(p, t, 10)
    await evaluate(p, t)
    locked[0] = False
    clock[0] = 60
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    transport_lock = ObservedLock()
    adapter.adapter._call_lock = transport_lock
    await transport_lock.acquire()

    async def probe():
        with c.phase_probe(t, enabled=True):
            return await evaluate(p, t)

    before = len(peer.requests)
    task = asyncio.create_task(probe())
    try:
        await asyncio.wait_for(queued.wait(), 2)
        assert selected(c.intent(t).energy_desired) == (1, 10)
        if recover_phase:
            samples(p, t, actual=4140, discharge=6000)
        else:
            set_budget(p, t, 11)  # Same phase: safe lower current remains allowed.
        p.optimum_refresh(datetime.now(UTC))
    finally:
        transport_lock.release()
    result = await asyncio.wait_for(task, 2)
    if recover_phase:
        assert result.status != CommandStatus.APPLIED
        assert len(peer.requests) == before  # Nothing reached the peer.
        assert c.intent(t).fence_reason in (
            "pre_dispatch_setpoint_changed",
            "pre_dispatch_pv_policy",
        )
        assert c.intent(t).hard_max_w < 2300
        await evaluate(p, t)
        assert c.confirmed_point(t).offered_power_w <= c.intent(t).hard_max_w
    else:
        assert result.status == CommandStatus.APPLIED
        assert period(peer.requests[-1])["number_phases"] == 1
        assert period(peer.requests[-1])["limit"] == 10


async def test_cancelled_probe_replans_next_cycle_without_busy_delay(grid):
    p, t, (c, adapter, peer, *_), clock, locked = await phase_start(grid)
    set_budget(p, t, 10)
    gate, parked, queued = asyncio.Event(), asyncio.Queue(), asyncio.Event()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    p.wait = wait
    task = asyncio.create_task(p.pv_sequence(t, p.epochs[t]))
    p.tasks[t] = task
    transport_lock = ObservedLock()
    try:
        await asyncio.wait_for(parked.get(), 2)
        assert c.phase_restricted(t)
        adapter.adapter._call_lock = transport_lock
        await transport_lock.acquire()
        locked[0] = False
        clock[0] = 60
        gate.set()
        await asyncio.wait_for(queued.wait(), 2)
        before = len(peer.requests)
        samples(p, t, actual=4140, discharge=6000)
        p.optimum_refresh(datetime.now(UTC))
        transport_lock.release()
        await asyncio.wait_for(parked.get(), 2)
        assert len(peer.requests) == before
        assert c.intent(t).fence_reason in (
            "pre_dispatch_setpoint_changed",
            "pre_dispatch_pv_policy",
        )
        assert p.pv_retry_until[t] == 120  # Phase probes remain bounded.
        clock[0] = 61
        samples(p, t, actual=4140, discharge=1900)
        gate.set()
        await asyncio.wait_for(parked.get(), 2)
        assert len(peer.requests) == before + 1
        assert period(peer.requests[-1])["number_phases"] == 3
        assert period(peer.requests[-1])["limit"] == 7
    finally:
        if transport_lock.locked():
            transport_lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
