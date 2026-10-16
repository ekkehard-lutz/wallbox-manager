"""Permission-first startup and separate, fenced profile preparation/retry."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import prepare
from test_pv_diagnostics import records
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
)
from custom_components.wallbox_manager.core.models import PhaseMode


async def startup(grid):
    p, t, context, clock = await prepare(grid, soc=96)
    p.setting(t)["approximation"] = "up"
    measurements(p, t, pv=2331, load=358, actual=0, soc=96)
    return p, t, context, clock


async def test_first_start_accepts_expected_phase_feedback_gap(grid):
    p, t, (c, _, peer, _, source, _), _ = await startup(grid)
    assert c.confirmed_point(t) is None and c.runtime.enabled(t) is False

    def response(profile):
        period = profile["charging_schedule"][0]["charging_schedule_period"][0]
        assert period["number_phases"] == 1 and period["limit"] == 8
        # Same effect as missing/expired phase feedback in CapabilityResolver:
        # current_mode is unknown and phase-operation eligibility vanishes.
        source.mode, source.proof = None, False
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).current_a == 8
    assert p.pv_ongoing[t], (p.status[t], c.runtime.sessions.get(t))
    source.mode, source.proof = PhaseMode.canonical(1), True


@pytest.mark.parametrize("reason", [CommandReason.STALE, CommandReason.BUSY])
async def test_first_rejection_schedules_retry_without_command_storm(
    grid, monkeypatch, reason, caplog
):
    p, t, (c, _, peer, *_), clock = await startup(grid)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    import custom_components.wallbox_manager.control.runtime as module

    real_apply = module.apply_operating_point
    operation = AsyncMock(
        return_value=CommandResult(CommandStatus.TEMPORARILY_REJECTED, reason=reason)
    )
    monkeypatch.setattr(module, "apply_operating_point", operation)
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED  # Permission succeeded first.
    assert c.intent(t).command_result.reason == reason
    assert c.confirmed_point(t) is None
    assert c.runtime.enabled(t) is True
    assert p.pv_retry_until[t] == 60 and t in p.pv_startups
    line = next(r for r in records(caplog) if r["trigger"] == "permission")
    assert line["startup_pending"] is True
    assert line["retry_remaining_s"] == 60
    assert line["applied"] is None and line["enabled"] is True
    assert not p.attributes(t)["enable_pending"]
    assert line["policy_allows_charging"] is True
    await asyncio.wait_for(parked.wait(), 1)
    for seconds in (5, 10, 59):
        parked.clear()
        clock[0] = seconds
        gate.set()
        await asyncio.wait_for(parked.wait(), 1)
        assert operation.await_count == 1
    monkeypatch.setattr(module, "apply_operating_point", real_apply)
    clock[0] = 60
    parked.clear()
    gate.set()
    await asyncio.wait_for(parked.wait(), 1)
    assert c.confirmed_point(t).current_a == 8
    assert c.runtime.enabled(t) is True
    assert t not in p.pv_startups and t not in p.pv_retry_until
    assert len(peer.requests) == 1


async def test_uncertain_first_reply_reconciles_after_retry_deadline(grid):
    p, t, (c, _, peer, _, source, _), clock = await startup(grid)
    gate, parked = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await gate.wait()
        gate.clear()

    p.wait = wait

    def response(_):
        # A non-volatile capability change still invalidates confirmation.
        source.snapshot = replace(
            source.snapshot, revision=source.snapshot.revision + 1
        )
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED  # Separate permission result.
    assert c.intent(t).command_result.reason == CommandReason.STALE
    assert c.intent(t).fence_reason == "capabilities_changed"
    assert t in c._unconfirmed_targets
    assert c.confirmed_point(t) is None and p.pv_retry_until[t] == 60
    await asyncio.wait_for(parked.wait(), 1)
    measurements(p, t, pv=2331, load=358, actual=0, soc=96)
    peer.profile_response = lambda _: call_result.SetChargingProfile(status="Accepted")
    clock[0] = 60
    parked.clear()
    gate.set()
    await asyncio.wait_for(parked.wait(), 1)
    assert c.confirmed_point(t).current_a == 8
    assert t not in c._unconfirmed_targets
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("action", ["off", "generation", "authority", "disconnect"])
async def test_startup_retry_cannot_outlive_authorization(grid, monkeypatch, action):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, *_), clock = await startup(grid)
    gate = asyncio.Event()
    p.wait = lambda _: gate.wait()
    with monkeypatch.context() as patch:
        patch.setattr(
            "custom_components.wallbox_manager.control.runtime.apply_operating_point",
            AsyncMock(
                return_value=CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED, reason=CommandReason.STALE
                )
            ),
        )
        await p.permission(t, True)
    await asyncio.sleep(0)
    if action == "off":
        await p.permission(t, False)
    elif action == "generation":
        c._edit(t, {"target_w": 0})
    elif action == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    else:
        c.runtime.disconnect(bound.token)
    count = len(peer.requests)
    clock[0] = 60
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(peer.requests) == count
    assert not p.pv_startups and not p.tasks


@pytest.mark.parametrize(
    "action", ["generation", "authority", "authority_roundtrip", "off"]
)
async def test_superseded_first_command_does_not_authorize_startup_retry(grid, action):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, *_), _ = await startup(grid)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(p.permission(t, True))
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        if action == "generation":
            c._edit(t, {"target_w": 0})
        elif action in ("authority", "authority_roundtrip"):
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
                ),
            )
            if action == "authority_roundtrip":
                c.runtime.observe_authority(
                    bound.token,
                    AuthorityObservation(
                        t.station, ControlAuthority.REMOTE, datetime.now(UTC), "test"
                    ),
                )
        else:
            # The user OFF path invalidates intent before its own serialized write.
            stopping = asyncio.create_task(p.permission(t, False))
            await asyncio.sleep(0)
    finally:
        peer.release.set()
        result = await pending
        if action == "off":
            await stopping
    assert result.status == CommandStatus.APPLIED  # ON preceded the point attempt.
    assert c.confirmed_point(t) is None
    assert not p.pv_startups and not p.tasks


async def test_permission_on_without_vehicle_transaction_or_economic_scope(grid):
    from test_ocpp21_control import transaction

    p, t, (c, _, peer, *_), _ = await startup(grid)
    await transaction(peer, connector=1, kind="Ended", at=datetime.now(UTC))
    await p.hass.async_block_till_done()
    assert c.blocker(t) == "transaction_unavailable"
    assert t not in p.economic_policies
    peer.operations.clear()
    peer.permissions.clear()
    sessions = c.runtime.sessions.latest
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True and c.confirmed_point(t) is None
    assert peer.operations == ["permission", "enabled_get"] and not peer.requests
    assert t not in p.economic_policies and t not in p.economic_identities
    attrs = p.attributes(t)
    assert not attrs["enable_pending"] and attrs["profile_start_pending"]
    assert attrs["pv_economic_inhibition"] == "economic_recovery_transaction"
    assert attrs["retained_current_accounting"] == "unsupported"
    assert not p.pv_ongoing.get(t, False)
    generation = c.intent(t).generation
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert c.intent(t).generation == generation
    assert c.runtime.sessions.latest == sessions and len(peer.permissions) == 1
    assert not peer.requests


async def test_transaction_ending_while_permission_queued_does_not_cancel_on(grid):
    from test_ocpp21_control import transaction

    p, t, (c, bound, peer, *_), _ = await startup(grid)
    live = bound.adapter
    queued = asyncio.Event()

    class Lock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    live._call_lock = Lock()
    await live._call_lock.acquire()
    pending = asyncio.create_task(p.permission(t, True))
    try:
        await asyncio.wait_for(queued.wait(), 1)
        await transaction(peer, connector=1, kind="Ended", at=datetime.now(UTC))
    finally:
        live._call_lock.release()
    assert (await pending).status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert not peer.requests and c.confirmed_point(t) is None


@pytest.mark.parametrize("profile", ["NETZ", "PV_SURPLUS"])
async def test_real_transaction_after_standby_continues_without_second_on(
    grid, profile
):
    from test_ocpp21_control import transaction

    p, t, (c, _, peer, *_), clock = await startup(grid)
    if profile == "NETZ":
        await p.select(t, "NETZ")
    await transaction(peer, connector=1, kind="Ended", at=datetime.now(UTC))
    await p.hass.async_block_till_done()
    parked, release = asyncio.Event(), asyncio.Event()

    async def wait(_):
        parked.set()
        await release.wait()
        release.clear()

    p.wait = wait
    peer.permissions.clear()
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    await asyncio.wait_for(parked.wait(), 1)
    await transaction(peer, identity="new-real", connector=1, at=datetime.now(UTC))
    measurements(p, t, pv=2331, load=358, actual=0, soc=96)
    clock[0] = 60
    parked.clear()
    release.set()
    await asyncio.wait_for(parked.wait(), 1)
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).charging
    assert len(peer.permissions) == 1
    assert peer.requests[-1][1]["transaction_id"] == "new-real"


@pytest.mark.parametrize(
    "missing", ["leistung_pv", "soc_speicher_aktuell", "grid_import_power"]
)
async def test_permission_on_with_unavailable_economic_inputs(grid, missing):
    p, t, (c, _, peer, *_), _ = await startup(grid)
    p.hass.states.async_set(p.references[missing], "unavailable")
    peer.operations.clear()
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert peer.operations[:2] == ["permission", "enabled_get"]
    # Existing protective zero remains allowed after independently confirmed ON.
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests
    )
    assert c.confirmed_point(t) is None or not c.confirmed_point(t).charging
    assert not p.attributes(t)["enable_pending"]
    assert not p.economic_policies[t].active


async def test_permission_on_with_unavailable_optimum_forecast(grid):
    from test_pv_optimum import setup_optimum

    p, t, (c, _, peer, *_), _ = await startup(grid)
    setup_optimum(p, t, actual=0)
    p.hass.states.async_set(p.references["remaining_pv_energy"], "unavailable")
    peer.operations.clear()
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert peer.operations[:2] == ["permission", "enabled_get"]
    assert not p.attributes(t)["enable_pending"]


async def test_permission_on_preserves_exhausted_stop_and_restart_inhibition(grid):
    from test_hardware_enabled import observed
    from test_pv_economy_control import exhausted

    from custom_components.wallbox_manager.pv_economy import LIMIT_WS

    p, t, (c, bound, peer, *_), _ = await exhausted(grid)
    policy = p.economic_policies[t]
    policy.confirm_off(at=p.monotonic())
    assert policy.exhausted and policy.restart_required
    reference = policy.restart_reference
    observed(bound, False)
    count = len(peer.requests)
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert p.economic_policies[t] is policy
    assert policy.energy_ws == LIMIT_WS and policy.restart_required
    assert policy.restart_reference == reference
    for _, profile in peer.requests[count:]:
        assert (
            profile["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        )


async def test_permission_retry_with_exhausted_history_uses_paused_regulator(
    grid, monkeypatch
):
    from test_hardware_enabled import observed
    from test_pv_economy_control import exhausted

    from custom_components.wallbox_manager.control.commands import ControlArea

    p, t, (c, bound, peer, *_), clock = await exhausted(grid)
    policy = p.economic_policies[t]
    observed(bound, False)
    reference = policy.restart_reference
    p.launch = type(p).launch.__get__(p)
    parked, release = asyncio.Queue(), asyncio.Event()

    async def wait(_):
        parked.put_nowait(None)
        await release.wait()
        release.clear()

    p.wait = wait
    with monkeypatch.context() as patch:
        patch.setattr(
            "custom_components.wallbox_manager.control.runtime.apply_charging_permission",
            AsyncMock(
                return_value=CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED,
                    ControlArea.CHARGING_PERMISSION,
                    CommandReason.BUSY,
                )
            ),
        )
        assert (await p.permission(t, True, _resume=True)).reason == CommandReason.BUSY
    await asyncio.wait_for(parked.get(), 1)
    count = len(peer.requests)
    clock[0] = 60
    release.set()
    await asyncio.wait_for(parked.get(), 1)
    assert c.runtime.enabled(t) is True
    assert t not in p.pv_startups and t in p.tasks
    assert p.economic_policies[t] is policy and policy.restart_required
    assert policy.restart_reference == reference
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests[count:]
    )
