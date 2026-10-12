"""A floor witness follows voltage cost but never permits a higher wire point."""

import asyncio
from datetime import UTC, datetime
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_economy_control import (
    cohort,
    exhausted,
    lock_current_phase,
    paused_reset,
    prepared,
)
from test_pv_optimum_hold import evaluate, period
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.control.runtime import PowerSettings
from custom_components.wallbox_manager.pv_economy import ECONOMIC_REASON, LIMIT_WS
from custom_components.wallbox_manager.pv_economy_storage import EconomicStorage


@pytest.mark.parametrize("volts", [229, 231])
async def test_floor_witness_revalues_only_same_representable_point(grid, volts):
    p, t, (c, *_), clock = await prepared(grid, phases=3, current=6)
    lock_current_phase(c, t)
    cohort(
        p, t, clock, 1, actual=4140, discharge=4000, imported=1000, pv=1100, load=6000
    )
    plan = p.pv_edit(t)
    assert p.status[t] == "optimum_minimum_hold"
    assert plan.point.current_a == 6 and plan.point.mode.count == 3
    assert c.intent(t).hard_max_w < plan.point.offered_power_w
    original_cost = plan.point.offered_power_w
    voltage(c, t, volts)
    _, fresh, blocked = c.resolve(
        t, request=PowerSettings(original_cost, Direction.DOWN)
    )
    assert blocked is None
    assert fresh.point.same_setpoint(plan.point)
    assert fresh.point.offered_power_w == Fraction((volts + 230 + 230) * 6)
    # An arbitrary larger request receives no minimum exception.
    _, higher, _ = c.resolve(
        t, request=PowerSettings(original_cost + 1, Direction.DOWN)
    )
    assert not higher.point.charging


async def test_control_limit_threshold_changes_split_the_live_meter_interval(grid):
    p, t, (c, *_), clock = await prepared(grid, phases=3, current=6)
    c.restore(t, allowed_current_1p=5)
    cohort(p, t, clock, 1, actual=4140, imported=1000)
    policy = p.economic_policies[t]
    assert policy.threshold_w == 2070 and policy.energy_ws == 0
    clock[0] = 3
    c.restore(t, allowed_current_1p=32)
    # One cached meter generation is reclassified, never counted twice.
    assert policy.threshold_w == 690 and policy.energy_ws == 0
    cohort(p, t, clock, 6, actual=4140, imported=1000)
    assert policy.energy_ws == 3000
    clock[0] = 7
    c.restore(t, allowed_current_1p=5)
    assert policy.threshold_w == 2070 and policy.energy_ws == 3000
    cohort(p, t, clock, 9, actual=4140, imported=1000)
    assert policy.energy_ws == 4000


async def test_late_sent_off_ack_keeps_restart_guard_after_verified_budget_reset(grid):
    p, t, (c, _, peer, *_), clock = await prepared(grid, current=10)
    p.setting(t)["optimum_max_discharge_w"] = 3400
    cohort(p, t, clock, 1, actual=2300, imported=1800, discharge=1000, pv=0, load=2800)
    policy = p.economic_policies[t]
    capacity, _ = p.economic_capacity(t)
    assert capacity == 2800
    policy.energy_ws = LIMIT_WS
    policy.capture_restart_reference(
        capacity, p.economic_minimum(t).point.offered_power_w
    )
    reference = policy.restart_reference
    assert not p.pv_edit(t).point.charging
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        assert policy.off_in_flight and policy.off_pending and not policy.paused
        # The device can stop drawing before its acknowledgement arrives.
        for second in (2, 10, 18):
            cohort(
                p,
                t,
                clock,
                second,
                source=datetime.now(UTC),
                actual=0,
                imported=0,
                discharge=500,
                pv=0,
                load=500,
            )
            p.economic_observe(t)
        assert policy.energy_ws == 0
        assert policy.off_in_flight and policy.off_pending
        assert policy.restart_reference == reference
        assert policy.pause_reason == ECONOMIC_REASON
        p.economic_checkpoint(t)
        assert await p.economic_store.flush()
        restored = p.economic_store.restore(
            p.economic_identities[t], authority_valid=True, transaction_valid=True
        ).policy
        assert restored.off_pending and restored.off_in_flight
        assert restored.restart_reference == reference and restored.energy_ws == 0
        peer.release.set()
        assert (await asyncio.wait_for(pending, 2)).status == CommandStatus.APPLIED
    finally:
        peer.release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert policy.paused and policy.restart_required and not policy.off_in_flight
    cohort(
        p,
        t,
        clock,
        21,
        source=datetime.now(UTC),
        actual=0,
        imported=0,
        discharge=500,
        pv=0,
        load=500,
    )
    p.pv_request(t)
    assert p.economic_capacity(t)[0] == capacity
    assert not p.pv_edit(t).point.charging
    assert policy.restart_reference == reference


async def test_restored_meter_state_never_supplies_interval_or_reset_proof(grid):
    p, t, (c, *_), clock = await prepared(grid, imported=1200)
    policy = p.economic_policies[t]
    policy.energy_ws = Fraction(25000)
    clock[0] = 5
    meter = p.hass.states.get(p.references["grid_import_power"])
    p.hass.states.async_set(meter.entity_id, 0, {**meter.attributes, "restored": True})
    p.economic_observe(t)
    assert policy.energy_ws == 25000 and policy.below_seconds == 0
    assert p.economic_stop(t)
    assert not p.pv_edit(t).point.charging
    cohort(p, t, clock, 6, imported=0)
    # A fresh live acquisition may reanchor; restored time grants no low dwell.
    meter = p.hass.states.get(p.references["grid_import_power"])
    p.hass.states.async_set(meter.entity_id, 0, {**meter.attributes, "restored": False})
    p.economic_observe(t)
    assert policy.energy_ws == 25000 and policy.below_seconds == 0


async def test_disabled_exhausted_recovery_resumes_on_verified_model_improvement(grid):
    p, t, (c, bound, peer, *_), clock = await exhausted(grid)
    p.economic_checkpoint(t)
    assert await p.economic_store.flush()
    await p.economic_store.close()
    p.economic_store = EconomicStorage(p.hass, p.entry_id)
    await p.economic_store.load()
    p.economic_policies.pop(t)
    p.economic_identities.pop(t)
    peer.enabled = False
    await bound.read_enabled()
    parked = asyncio.Queue()

    async def wait(_):
        parked.put_nowait(None)
        await asyncio.Event().wait()

    p.wait = wait
    p.launch = type(p).launch.__get__(p)
    before = len(peer.requests)
    result = await p.permission(t, True, _resume=True)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert t in p.economic_waits and p.pv_retry_until[t] == clock[0]
    await asyncio.wait_for(parked.get(), 2)
    policy = p.economic_policies[t]
    reference = policy.restart_reference
    for second in (1, 9, 17):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            imported=0,
            discharge=0,
            pv=1000,
            load=1000,
        )
        await p.hass.async_block_till_done()
        await asyncio.wait_for(parked.get(), 2)
        assert all(period(request)["limit"] == 0 for request in peer.requests[before:])
    assert policy.energy_ws == 0 and policy.restart_required
    assert policy.restart_reference == reference
    # The ordinary meter wake and startup path can now prepare a battery-assisted
    # point; no new restart timer or battery availability sensor is involved.
    for second in (18, 19):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            imported=0,
            exported=1001,
            discharge=0,
            pv=2001,
            load=1000,
        )
        await p.hass.async_block_till_done()
        await asyncio.wait_for(parked.get(), 2)
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).charging
    assert c.confirmed_point(t).offered_power_w > 1001
    assert not policy.restart_required
    assert t not in p.economic_waits and t not in p.economic_activation_pending


async def test_actual_positive_response_counts_before_late_positive_ack(grid):
    p, t, (c, _, peer, *_), clock = await paused_reset(grid)
    cohort(
        p,
        t,
        clock,
        18,
        source=datetime.now(UTC),
        actual=0,
        imported=0,
        exported=1001,
        discharge=0,
        pv=2001,
        load=1000,
    )
    await p.hass.async_block_till_done()
    await evaluate(p, t)
    cohort(
        p,
        t,
        clock,
        19,
        source=datetime.now(UTC),
        actual=0,
        imported=0,
        exported=1001,
        discharge=0,
        pv=2001,
        load=1000,
    )
    await p.hass.async_block_till_done()
    plan = p.pv_edit(t)
    assert plan.point.charging
    policy = p.economic_policies[t]
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        assert policy.restart_required and not policy.active
        for second in (20, 28, 29):
            cohort(
                p,
                t,
                clock,
                second,
                source=datetime.now(UTC),
                actual=plan.point.offered_power_w,
                imported=20000,
                discharge=0,
                pv=1000,
                load=21000 + plan.point.offered_power_w,
            )
            await p.hass.async_block_till_done()
        assert policy.active and policy.exhausted
        assert policy.energy_ws == LIMIT_WS
        reference = policy.restart_reference
        assert reference is not None
        peer.release.set()
        await asyncio.wait_for(pending, 2)
        assert policy.exhausted and policy.restart_reference == reference
        assert policy.pause_reason == ECONOMIC_REASON
        assert not p.pv_edit(t).point.charging
    finally:
        peer.release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_reconnect_during_stop_checkpoint_revokes_original_command(
    grid, monkeypatch
):
    p, t, (c, bound, peer, *_), _ = await exhausted(grid)
    assert not p.pv_edit(t).point.charging
    entered, release = asyncio.Event(), asyncio.Event()
    original_flush = p.economic_store.flush

    async def flush():
        entered.set()
        await release.wait()
        return await original_flush()

    monkeypatch.setattr(p.economic_store, "flush", flush)
    before = len(peer.requests)
    pending = asyncio.create_task(c.apply_stored(t))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        c.runtime.disconnect(bound.token)
        c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
        release.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.status == CommandStatus.TEMPORARILY_REJECTED
        assert len(peer.requests) == before
        assert p.economic_policies[t].exhausted
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_explicit_off_during_initial_checkpoint_cannot_restore_ended_scope(
    grid, monkeypatch
):
    p, t, (c, bound, peer, *_), clock = await prepared(grid)
    await p.economic_end_scope(t)
    peer.enabled = False
    await bound.read_enabled()
    cohort(
        p,
        t,
        clock,
        1,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        imported=0,
        pv=1000,
        load=1000,
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original_flush = p.economic_store.flush

    async def flush():
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original_flush()

    monkeypatch.setattr(p.economic_store, "flush", flush)
    pending = asyncio.create_task(p.permission(t, True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert t in p.economic_policies
        assert (await p.permission(t, False)).status == CommandStatus.APPLIED
        assert t not in p.economic_policies
        release.set()
        assert await asyncio.wait_for(pending, 2) is None
        assert t not in p.economic_policies
        assert t not in p.economic_identities
        assert p.economic_store.identity_for_target(t) is None
        assert c.runtime.enabled(t) is False
        assert c.intent(t).command_result.status == CommandStatus.APPLIED
        cohort(
            p,
            t,
            clock,
            2,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            pv=1000,
            load=1000,
        )
        assert (await p.permission(t, True)).status == CommandStatus.APPLIED
        assert c.confirmed_point(t).charging
        assert p.economic_policies[t].energy_ws == 0
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
