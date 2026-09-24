"""Explicit target fallback through real OCPP frames and response schemas."""

import asyncio
from dataclasses import replace

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_control_runtime import measured
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.capabilities import (
    CurrentLimit,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import Phase, PhaseMode


def period(profile):
    return profile["charging_schedule"][0]["charging_schedule_period"][0]


async def setup(manual):
    control, bound, peer, _, source, _ = manual
    source.mode = PhaseMode(tuple(Phase))
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, max_current_a=16) for e in source.snapshot.envelopes
        ),
        stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
    )
    await control.change(bound.target, target_w=6000, allowed=True)
    assert period(peer.requests[-1][1])["limit"] == 9
    locked = [True]
    restart = [False]

    def response(profile):
        p = period(profile)
        reason = (
            "RestartLockout"
            if restart[0] and p["limit"] > 0
            else "PhaseSwitchLockout"
            if locked[0] and p.get("number_phases", 3) != 3
            else None
        )
        return call_result.SetChargingProfile(
            status="Rejected" if reason else "Accepted",
            status_info={"reason_code": reason} if reason else None,
        )

    peer.profile_response = response
    return control, bound, peer, source, locked, restart


async def test_substitute_repeated_expiry_and_explicit_recalculation(manual):
    control, bound, peer, source, locked, _ = await setup(manual)
    for _ in range(2):
        before = len(peer.requests)
        result = await control.change(bound.target, target_w=1700)
        assert result.status == CommandStatus.APPLIED
        assert len(peer.requests) == before + 2
        assert period(peer.requests[-1][1]) == {
            "start_period": 0,
            "limit": 6,
            "number_phases": 3,
        }
        point = control.intent(bound.target).solver_result.point
        assert point.mode == source.mode and point.current_a == 6
        assert point.offered_power_w == 4140
        attributes = control.attributes(bound.target)
        assert attributes["applied_current_a"] == 6
        assert attributes["applied_phase_count"] == 3
        assert attributes["applied_offered_power_w"] == 4140
    locked[0] = False
    before = len(peer.requests)
    measured(bound.adapter.runtime, bound.token, bound.target)
    await asyncio.sleep(0)
    assert len(peer.requests) == before
    assert control.intent(bound.target).solver_result.point.mode.count == 3
    await control.change(bound.target, target_w=1700)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1][1])["number_phases"] == 1
    assert period(peer.requests[-1][1])["limit"] == 7


@pytest.mark.parametrize(
    "minimum,maximum,expected", [(8, 10, 8), (0, 5, None), (6, 6, 6)]
)
async def test_vehicle_limits(manual, minimum, maximum, expected):
    control, bound, peer, source, _, _ = await setup(manual)
    source.permitted = (CurrentLimit(source.mode, minimum, maximum, "vehicle"),)
    before = len(peer.requests)
    result = await control.change(bound.target, target_w=1700)
    assert len(peer.requests) == before + (2 if expected else 1)
    if expected:
        assert result.status == CommandStatus.APPLIED
        assert period(peer.requests[-1][1])["limit"] == expected
    else:
        assert result.status == CommandStatus.TEMPORARILY_REJECTED


async def test_direction_is_not_relaxed(manual):
    control, bound, peer, _, _, _ = await setup(manual)
    before = len(peer.requests)
    result = await control.change(bound.target, target_w=1700, direction=Direction.DOWN)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == before + 1


async def test_zero_pause_even_with_both_locks(manual):
    control, bound, peer, _, _, restart = await setup(manual)
    restart[0] = True
    before = len(peer.requests)
    assert (
        await control.change(bound.target, target_w=0)
    ).status == CommandStatus.APPLIED
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1][1]) == {"start_period": 0, "limit": 0}


@pytest.mark.parametrize(
    "reason",
    [None, "HardwareError", "NotSupported", "NoAuthority", "RestartLockout"],
)
async def test_unrelated_rejections_do_not_retry(manual, reason):
    control, bound, peer, _, _, _ = await setup(manual)
    peer.profile_response = lambda _: call_result.SetChargingProfile(
        status="Rejected", status_info={"reason_code": reason} if reason else None
    )
    before = len(peer.requests)
    assert (
        await control.change(bound.target, target_w=1700)
    ).status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == before + 1


async def test_restart_lock_rechecked_on_substitute(manual):
    control, bound, peer, _, _, restart = await setup(manual)
    response = peer.profile_response

    def reject_restart(profile):
        result = response(profile)
        restart[0] = True
        return result

    peer.profile_response = reject_restart
    before = len(peer.requests)
    result = await control.change(bound.target, target_w=1700)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == before + 2
    assert control.intent(bound.target).status != "applied"


@pytest.mark.parametrize("change", ["unknown", "changed", "authority", "limit"])
async def test_changed_inputs_prevent_fallback(manual, change):
    control, bound, peer, source, _, _ = await setup(manual)
    response = peer.profile_response

    def mutate(profile):
        if change == "unknown":
            source.mode = None
        elif change == "changed":
            source.mode = PhaseMode((Phase.L1,))
        elif change == "limit":
            source.permitted = (CurrentLimit(source.mode, 0, 5, "vehicle"),)
        else:
            state = bound.adapter.runtime.get(bound.target.station)
            bound.adapter.runtime._publish(
                replace(state, authority_revision=state.authority_revision + 1)
            )
        return response(profile)

    peer.profile_response = mutate
    before = len(peer.requests)
    await control.change(bound.target, target_w=1700)
    assert len(peer.requests) == before + 1


async def test_new_explicit_target_fences_late_lockout(manual):
    control, bound, peer, _, _, _ = await setup(manual)
    peer.received.clear()
    peer.release.clear()
    before = len(peer.requests)
    old = asyncio.create_task(control.change(bound.target, target_w=1700))
    await asyncio.wait_for(peer.received.wait(), 1)
    newer = asyncio.create_task(control.change(bound.target, target_w=0))
    await asyncio.sleep(0)
    peer.release.set()
    await newer
    from custom_components.wallbox_manager.control.commands import CommandReason

    assert (await old).reason == CommandReason.STALE
    assert len(peer.requests) == before + 2
    assert period(peer.requests[-1][1])["limit"] == 0


async def test_device_grid_and_desired_limit(manual):
    control, bound, peer, source, _, _ = await setup(manual)
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, min_current_a=7, current_step_a=2, max_current_a=11)
            if e.mode == source.mode
            else e
            for e in source.snapshot.envelopes
        ),
    )
    await control.change(bound.target, target_w=1700, allowed_current_3p=8)
    assert period(peer.requests[-1][1])["limit"] == 7
    before = len(peer.requests)
    await control.change(bound.target, target_w=1700, allowed_current_3p=6)
    assert len(peer.requests) == before + 1
    assert control.intent(bound.target).status == "temporarily_rejected"
