"""OFF/start invariants across asynchronous PV profile and control boundaries."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.requests import Direction


async def started(grid):
    profile, target, manual = grid
    control = manual[0]
    measurements(profile, target, soc=96)
    await profile.select(target, "PV_SURPLUS")
    sleeping = asyncio.Event()

    async def wait(_):
        sleeping.set()
        await asyncio.Event().wait()

    profile.wait = wait
    profile.debounce_wait = AsyncMock()
    await profile.permission(target, True)
    await asyncio.wait_for(sleeping.wait(), 1)
    assert control.confirmed_point(target).charging
    return grid


async def applied(control, target, charging):
    done = asyncio.Event()

    def changed(_):
        point = control.confirmed_point(target)
        if point and point.charging is charging:
            done.set()

    unsubscribe = control.subscribe(changed)
    try:
        changed(target)
        await asyncio.wait_for(done.wait(), 1)
    finally:
        unsubscribe()


async def test_soc_stop_wakes_parked_regulator_without_revoking_permission(grid):
    p, t, (c, _, _, *_) = await started(grid)
    # No periodic timer or debounce is released by the test.
    measurements(p, t, soc=89)
    await applied(c, t, False)
    assert c.runtime.enabled(t) is True
    assert c.runtime.sessions.get(t).active
    assert not p.pv_ongoing[t]


async def test_common_runtime_off_cannot_leave_continuation_latched(grid):
    p, t, (c, _, _, *_) = await started(grid)
    measurements(p, t, soc=93)
    await c.change(t, target_w=0)
    assert not c.confirmed_point(t).charging
    result = p.pv_edit(t)
    assert not result.point.charging
    assert not p.pv_ongoing[t]


async def test_low_soc_dip_fences_queued_continuation_even_after_recovery(grid):
    p, t, (c, bound, peer, *_) = await started(grid)
    measurements(p, t, soc=93)
    p.pv_edit(t)
    expected = p.pv_request(t)
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(
            c.apply_stored(t, fence=lambda: p.pv_request(t) == expected)
        )
        await asyncio.sleep(0)
        # Both events can arrive before the event loop dispatches either callback.
        measurements(p, t, soc=89)
        measurements(p, t, soc=93)
        await asyncio.sleep(0)
    await pending
    await applied(c, t, False)
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests[count:]
    )
    assert c.runtime.enabled(t) is True
    assert c.intent(t).request.direction == Direction.DOWN


@pytest.mark.parametrize("soc", [90, 93, 95])
async def test_active_charge_continues_at_inclusive_soc_boundaries(grid, soc):
    p, t, (c, _, _, *_) = await started(grid)
    measurements(p, t, soc=soc)
    result = p.pv_edit(t)
    assert result.point.charging
    assert c.intent(t).request.direction == Direction.DOWN
    assert (await c.apply_stored(t)).status.value == "applied"
    p.pv_confirm(t)
    assert p.pv_ongoing[t]
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("soc", [90, 93, 95, 96])
@pytest.mark.parametrize("pause", ["negative_pv", "below_minimum", "low_soc"])
async def test_every_resume_from_off_uses_start_threshold(grid, soc, pause):
    p, t, (c, _, _, *_) = await started(grid)
    session_id = c.runtime.sessions.get(t).session_id
    if pause == "low_soc":
        measurements(p, t, soc=89)
    elif pause == "below_minimum":
        measurements(p, t, pv=1, load=0, actual=0, soc=93)
    else:
        measurements(p, t, pv=0, soc=96)
    result = p.pv_edit(t)
    assert not result.point.charging
    assert (await c.apply_stored(t)).status.value == "applied"
    p.pv_confirm(t)
    assert not p.pv_ongoing[t]
    measurements(p, t, soc=soc)
    result = p.pv_edit(t)
    assert result.point.charging is (soc > 95)
    assert c.runtime.enabled(t) is True
    assert c.runtime.sessions.get(t).active
    assert c.runtime.sessions.get(t).session_id == session_id


@pytest.mark.parametrize("soc", [90, 95])
async def test_shared_primitive_cannot_restart_after_off(grid, soc):
    p, t, (c, _, peer, *_) = await started(grid)
    await c.change(t, target_w=0)
    measurements(p, t, soc=soc)
    count = len(peer.requests)
    result = await c.change(t, target_w=6000, direction=Direction.DOWN)
    assert result.status.value != "applied"
    assert len(peer.requests) == count
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("soc", [90, 93, 95])
async def test_soc_fall_during_positive_restart_delay_prevents_dispatch(grid, soc):
    p, t, (c, _, peer, *_) = await started(grid)
    measurements(p, t, pv=0, soc=96)
    p.pv_edit(t)
    await c.apply_stored(t)
    p.pv_confirm(t)
    entered, release = asyncio.Event(), asyncio.Event()

    async def debounce(_):
        entered.set()
        await release.wait()
        release.clear()

    p.wait = debounce
    p.setting(t)["pv_start_delay"] = 10
    measurements(p, t, soc=96)
    # Restart the existing regulator, with its original (cleared) continuation state.
    p.invalidate(t)
    p.launch(t)
    await asyncio.wait_for(entered.wait(), 1)
    count = len(peer.requests)
    measurements(p, t, soc=soc)
    await asyncio.sleep(0)
    release.set()
    await applied(c, t, False)
    await asyncio.sleep(0)
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests[count:]
    )
    assert not p.pv_ongoing[t]


@pytest.mark.parametrize("soc", [90, 95, 96])
async def test_reload_does_not_restore_continuation(grid, soc):
    from types import SimpleNamespace

    from custom_components.wallbox_manager.profiles import GridProfiles

    p, t, (c, _, peer, *_) = await started(grid)
    await p.close()
    clone = GridProfiles(
        p.hass,
        SimpleNamespace(entry_id=p.entry_id, options=p.references),
        c,
        p.battery,
    )
    c.profiles = clone
    count = len(peer.requests)
    try:
        await clone.load()
        assert not clone.tasks
        assert not clone.pv_ongoing
        assert len(peer.requests) == count
        measurements(clone, t, soc=soc)
        if soc <= 95:
            c.restore(t, target_w=6000, direction=Direction.DOWN)
            result = await c.apply_stored(t)
            assert result.status.value != "applied"
            assert len(peer.requests) == count
        clone.wait = lambda _: asyncio.Event().wait()
        await clone.permission(t, True)
        assert c.confirmed_point(t).charging is (soc > 95)
        assert c.runtime.enabled(t) is True
    finally:
        await clone.close()


async def test_disconnect_reconnect_cannot_reuse_continuation_or_pending_work(grid):
    from dataclasses import replace

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, _, source, _) = await started(grid)
    old = c.runtime.get(t.station)
    c.runtime.disconnect(bound.token)
    assert not p.tasks
    assert not p.pv_ongoing[t]
    count = len(peer.requests)
    token = c.runtime.connect(t.station)
    # Restore fresh discovery/permission observations for a new transport context.
    c.runtime._publish(replace(old, token=token))
    c.runtime.observe_authority(
        token,
        AuthorityObservation(
            t.station, ControlAuthority.REMOTE, old.authority.observed_at, "reconnected"
        ),
    )
    measurements(p, t, soc=93)
    assert not p.pv_ongoing[t]
    assert p.pv_request(t)[0] == 0
    await asyncio.sleep(0)
    assert len(peer.requests) == count


@pytest.mark.parametrize("soc", [90, 95, 96])
async def test_phase_retry_after_off_obeys_start_threshold(grid, soc):
    from test_phase_lockout import setup

    p, t, manual = grid
    c, _, peer, _, locked, _ = await setup(manual)
    p.setting(t).update(profile="PV_SURPLUS", approximation="up")
    measurements(p, t, pv=1700, load=0, actual=0, soc=96)
    parked = asyncio.Event()

    async def wait(_):
        parked.set()
        await asyncio.Event().wait()

    p.wait, p.debounce_wait = wait, AsyncMock()
    p.monotonic = lambda: 100
    p.launch(t)
    await asyncio.wait_for(parked.wait(), 1)
    assert c.intent(t).phase_retry
    assert p.pv_retry_until[t] == 160
    assert c.confirmed_point(t).charging
    # Pause without terminating permission or the transaction, then expire lockout.
    measurements(p, t, pv=0, soc=96)
    p.pv_edit(t)
    await c.apply_stored(t)
    p.pv_confirm(t)
    assert not c.confirmed_point(t).charging
    locked[0] = False
    p.monotonic = lambda: 200
    measurements(p, t, pv=1700, load=0, actual=0, soc=soc)
    parked.clear()
    # Wake the same loop as its next periodic cycle (settings edit uses this path).
    await p.set_value(t, "regulation_interval", 6)
    await asyncio.wait_for(parked.wait(), 1)
    assert c.confirmed_point(t).charging is (soc > 95)
    assert c.runtime.enabled(t) is True
    assert c.runtime.sessions.get(t).active


@pytest.mark.parametrize("soc", [90, 95])
async def test_late_positive_reply_cannot_restore_continuation_after_soc_fall(
    grid, soc
):
    p, t, (c, _, peer, *_) = await started(grid)
    measurements(p, t, pv=0, soc=96)
    p.pv_edit(t)
    await c.apply_stored(t)
    p.pv_confirm(t)
    p.invalidate(t)
    measurements(p, t, soc=96)
    peer.received.clear()
    peer.release.clear()
    p.launch(t)
    await asyncio.wait_for(peer.received.wait(), 1)
    # A dispatched frame cannot be recalled, but its delayed reply must not
    # re-arm continuation; a fenced zero-power command must follow immediately.
    measurements(p, t, soc=soc)
    await asyncio.sleep(0)
    peer.release.set()
    await applied(c, t, False)
    assert not p.pv_ongoing[t]
    assert c.runtime.enabled(t) is True
    assert (
        peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][0][
            "limit"
        ]
        == 0
    )
