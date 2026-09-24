"""Confirmed operating-point display is read-only and fenced by runtime context."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_grid_profiles import grid as grid
from test_grid_refinements import prepare
from test_ocpp21_control import connected as connected
from test_phase_lockout import setup
from test_profile_ownership import site as site

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)


def displayed(control, target):
    attrs = control.attributes(target)
    return attrs["applied_phase_count"], attrs["applied_current_a"]


@pytest.mark.parametrize("watts,expected", [(3680, (1, 16)), (6210, (3, 9))])
async def test_confirmed_point_survives_separate_permission_confirmation(
    manual, watts, expected
):
    control, bound, peer, *_ = manual
    control.restore(bound.target, allowed_current_1p=16)
    await control.change(bound.target, target_w=watts, allowed=True)
    assert control.intent(bound.target).command_result.status == CommandStatus.APPLIED
    assert displayed(control, bound.target) == expected
    before = list(peer.operations)
    for _ in range(3):
        assert displayed(control, bound.target) == expected
    assert peer.operations == before
    # An intent edit is not a new applied point.
    control._edit(bound.target, {"target_w": 10000})
    assert displayed(control, bound.target) == expected
    await control.request_enabled(bound.target, False)
    assert displayed(control, bound.target) == (None, None)


async def test_lockout_shows_confirmed_substitute_until_target_confirmation(manual):
    control, bound, peer, source, locked, _ = await setup(manual)
    await control.change(bound.target, target_w=1700)
    assert control.intent(bound.target).phase_retry
    assert displayed(control, bound.target) == (3, 6)
    # Editing the desired point while the retry waits preserves the substitute.
    control._edit(bound.target, {"target_w": 1840})
    assert displayed(control, bound.target) == (3, 6)
    locked[0] = False
    await control.apply_stored(bound.target)
    assert displayed(control, bound.target) == (1, 8)


async def test_pending_and_rejected_target_are_not_applied(manual):
    control, bound, peer, *_ = manual
    control.restore(bound.target, allowed_current_1p=16)
    await control.change(bound.target, target_w=3680, allowed=True)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(control.change(bound.target, target_w=6210))
    await asyncio.wait_for(peer.received.wait(), 1)
    assert displayed(control, bound.target) == (None, None)
    peer.release.set()
    await pending
    assert displayed(control, bound.target) == (3, 9)
    peer.profile_response = lambda _: call_result.SetChargingProfile(status="Rejected")
    await control.change(bound.target, target_w=3680)
    assert displayed(control, bound.target) == (None, None)


@pytest.mark.parametrize(
    "event", ["authority", "disconnect", "boot", "permission_epoch", "close"]
)
async def test_applied_snapshot_cannot_cross_invalidating_context(manual, event):
    control, bound, peer, *_ = manual
    await control.change(bound.target, target_w=3680, allowed=True)
    assert displayed(control, bound.target) == (1, 16)
    runtime = control.runtime
    if event == "authority":
        for value in (ControlAuthority.LOCAL, ControlAuthority.REMOTE):
            runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    bound.target.station, value, datetime.now(UTC), "test"
                ),
            )
            assert displayed(control, bound.target) == (None, None)
    elif event == "disconnect":
        runtime.disconnect(bound.token)
        assert displayed(control, bound.target) == (None, None)
        runtime.connect(bound.target.station)
    elif event == "boot":
        state = runtime.get(bound.target.station)
        runtime._publish(
            replace(
                state,
                token=replace(
                    state.token, boot_generation=state.token.boot_generation + 1
                ),
            )
        )
    elif event == "permission_epoch":
        old = runtime.enabled_observation(bound.target)
        runtime.observe_enabled(
            bound.token, replace(old, enabled=False, observed_at=datetime.now(UTC))
        )
        runtime.observe_enabled(
            bound.token, replace(old, enabled=True, observed_at=datetime.now(UTC))
        )
    else:
        control.close()
    assert displayed(control, bound.target) == (None, None)


async def test_unexpected_apply_exception_leaves_unknown(manual, monkeypatch):
    import custom_components.wallbox_manager.control.runtime as module

    control, bound, *_ = manual
    await control.change(bound.target, target_w=3680, allowed=True)
    monkeypatch.setattr(
        module,
        "apply_operating_point",
        AsyncMock(side_effect=RuntimeError("uncertain")),
    )
    with pytest.raises(RuntimeError):
        await control.change(bound.target, target_w=6210)
    assert displayed(control, bound.target) == (None, None)


async def test_late_stale_result_cannot_publish_applied_target(manual):
    control, bound, peer, *_ = manual
    await control.change(bound.target, target_w=3680, allowed=True)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(control.change(bound.target, target_w=6210))
    await asyncio.wait_for(peer.received.wait(), 1)
    control._edit(bound.target, {"target_w": 2000})
    peer.release.set()
    await pending
    assert displayed(control, bound.target) == (None, None)


async def test_hold_like_edits_keep_backend_debounce_and_previous_applied_point(grid):
    profile, target, (control, _, peer, *_) = grid
    clock = await prepare(profile, target)
    previous = displayed(control, target)
    count = len(peer.requests)
    # Pointer press, 450 ms delay, then 150 ms repetitions: only final apply.
    for delay, power in [(0, 9.9), (450, 10), (150, 11), (150, 12)]:
        await clock.advance(delay)
        await profile.set_value(target, "power_kw", power)
        assert displayed(control, target) == previous
        assert len(peer.requests) == count
    pending = profile.debounce_tasks[target]
    await clock.advance(999)
    assert len(peer.requests) == count
    await clock.advance(1)
    await pending
    assert len(peer.requests) == count + 1
    assert displayed(control, target) != previous


async def test_owner_switch_takeover_and_reload_hide_previous_applied_state(site):
    owner, (a, b), _ = site
    for watts, (control, bound, _, profile, key) in zip(
        (3.68, 6.21), (a, b), strict=True
    ):
        await owner.activate(key)
        assert displayed(control, bound.target) == (None, None)
        await profile.set_value(bound.target, "power_kw", watts)
        await profile.permission(bound.target, True)
        assert displayed(control, bound.target)[1] is not None
    assert displayed(a[0], a[1].target) == (None, None)
    assert displayed(b[0], b[1].target) == (3, 9)
    await owner.activate(b[4])  # Explicit takeover forces OFF and invalidates display.
    assert displayed(b[0], b[1].target) == (None, None)
    from custom_components.wallbox_manager.control.runtime import ControlRuntime

    restored = ControlRuntime(b[0].runtime, b[0].inputs, b[0].adapter)
    assert displayed(restored, b[1].target) == (None, None)
    restored.close()


async def test_single_phase_substitute_for_blocked_three_phase_target(manual):
    from custom_components.wallbox_manager.core.models import PhaseMode

    control, bound, peer, _, source, _ = manual
    source.mode = PhaseMode.canonical(1)
    control.restore(bound.target, allowed_current_1p=16)
    await control.change(bound.target, target_w=3680, allowed=True)
    locked = True

    def response(profile):
        period = profile["charging_schedule"][0]["charging_schedule_period"][0]
        reject = locked and period.get("number_phases") == 3
        return call_result.SetChargingProfile(
            status="Rejected" if reject else "Accepted",
            status_info={"reason_code": "PhaseSwitchLockout"} if reject else None,
        )

    peer.profile_response = response
    await control.change(bound.target, target_w=6210)
    assert control.intent(bound.target).phase_retry
    assert displayed(control, bound.target) == (1, 16)
    locked = False
    await control.apply_stored(bound.target)
    assert displayed(control, bound.target) == (3, 9)
