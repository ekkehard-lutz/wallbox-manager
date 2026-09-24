"""Hardware permission, zero-current pause and semantic execution fences."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_control_authority import authority as authority
from test_control_runtime import manual as manual
from test_control_runtime import measured
from test_ha_control import controls as controls
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.enabled import EnabledObservation
from custom_components.wallbox_manager.core.telemetry import Quantity
from custom_components.wallbox_manager.protocols.ocpp.v21.enabled import poll_enabled


async def acquire_for_test(control, bound, peer, *, start=False):
    """Prepare primitive tests using beta.2's two separate user actions."""
    result = await control.take_control(bound.target.station)
    assert result.status == CommandStatus.APPLIED
    assert control.runtime.enabled(bound.target) is False
    assert peer.permissions[-1]["attribute_value"] == "false"
    assert not peer.requests  # Takeover must never apply power or enable.
    if start:
        assert (
            await control.request_enabled(bound.target, True)
        ).status == CommandStatus.APPLIED
    peer.permissions.clear()
    peer.requests.clear()
    peer.operations.clear()


def observed(bound, value):
    at = datetime.now(UTC)
    bound.adapter.runtime.observe_enabled(
        bound.adapter.token,
        EnabledObservation(bound.target, value, at, at + timedelta(seconds=15)),
    )


@pytest.mark.parametrize("value", [True, False])
async def test_local_hardware_controls_ha_display(controls, value):
    hass, entities, (_, bound, peer, _, _, _), _, _ = controls
    peer.enabled = value
    await bound.read_enabled()
    assert entities["charging_enabled"].is_on is value
    assert hass.states.get(entities["charging_enabled"].entity_id).state == (
        "on" if value else "off"
    )
    assert not peer.requests and not peer.permissions


@pytest.mark.parametrize("value", [True, False])
async def test_local_ha_attempt_returns_to_actual_without_intent(controls, value):
    _, entities, (control, bound, peer, _, _, _), _, _ = controls
    peer.enabled = value
    await bound.read_enabled()
    bound.adapter.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    switch = entities["charging_enabled"]
    await (switch.async_turn_off() if value else switch.async_turn_on())
    assert switch.is_on is value
    assert control.intent(bound.target).status == "no_authority"
    assert not hasattr(control.intent(bound.target).request, "allowed")
    assert not peer.permissions and not peer.requests


async def test_rejected_enable_is_not_replayed(authority):
    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    control.restore(bound.target, target_w=2300)
    peer.status = "Rejected"
    result = await control.request_enabled(bound.target, True)
    assert result.status != CommandStatus.APPLIED
    assert bound.adapter.runtime.enabled(bound.target) is False
    peer.status = "Accepted"
    await control.change(bound.target, target_w=2400)
    assert not peer.permissions
    assert not hasattr(control.intent(bound.target).request, "allowed")


async def test_readback_mismatch_is_not_confirmation(authority):
    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    control.restore(bound.target, target_w=2300)
    peer.enabled_read_value = "false"
    result = await control.request_enabled(bound.target, True)
    assert result.status == CommandStatus.FAILED
    assert bound.adapter.runtime.enabled(bound.target) is False


async def test_zero_and_resume_preserve_permission_and_transaction(authority):
    control, bound, peer, _ = authority
    peer.enabled = True
    control.restore(bound.target, target_w=2300)
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    state = bound.adapter.runtime.get(bound.target.station)
    sessions = bound.adapter.runtime.sessions.latest
    await control.change(bound.target, target_w=0)
    period = peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][0]
    assert period == {"start_period": 0, "limit": 0}
    assert peer.requests[-1][1]["charging_schedule"][0]["charging_rate_unit"] == "A"
    assert not peer.permissions
    assert bound.adapter.runtime.enabled(bound.target) is True
    assert bound.adapter.runtime.sessions.latest == sessions
    assert (
        bound.adapter.runtime.get(bound.target.station).physical_phases
        == state.physical_phases
    )
    await control.change(bound.target, target_w=2300)
    assert peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][
        0
    ] == {"start_period": 0, "limit": 10, "number_phases": 1}
    assert not peer.permissions


async def test_disabled_target_edits_do_not_enable_and_on_prepares_zero(authority):
    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    await control.change(bound.target, target_w=0)
    await control.change(bound.target, target_w=2300)
    assert not peer.requests and not peer.permissions
    await control.change(bound.target, target_w=0)
    assert (
        await control.request_enabled(bound.target, True)
    ).status == CommandStatus.APPLIED
    assert peer.operations[-3:] == ["profile", "permission", "enabled_get"]
    assert peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"] == [
        {"start_period": 0, "limit": 0}
    ]
    assert bound.adapter.runtime.enabled(bound.target) is True


async def test_zero_needs_no_voltage_or_phase_feedback(authority):
    control, bound, peer, _ = authority
    peer.enabled = True
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    state = bound.adapter.runtime.get(bound.target.station)
    bound.adapter.runtime._publish(replace(state, observations=(), physical_phases=()))
    assert (
        await control.change(bound.target, target_w=0)
    ).status == CommandStatus.APPLIED
    assert peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"] == [
        {"start_period": 0, "limit": 0}
    ]


@pytest.mark.parametrize("change", ["refresh", "voltage", "hardware", "capability"])
async def test_profile_inflight_semantic_fences(authority, change):
    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    control.restore(bound.target, target_w=2300)
    peer.release.clear()
    pending = asyncio.create_task(control.request_enabled(bound.target, True))
    await asyncio.wait_for(peer.received.wait(), 1)
    live = bound.adapter
    if change == "hardware":
        observed(bound, True)
    elif change == "capability":
        state = live.runtime.get(bound.target.station)
        live.runtime._publish(replace(state, electrical=()))
    else:
        measured(live.runtime, live.token, bound.target)
        if change == "voltage":
            state = live.runtime.get(bound.target.station)
            live.runtime._publish(
                replace(
                    state,
                    observations=tuple(
                        replace(o, value=240)
                        if o.channel.quantity == Quantity.VOLTAGE_L1
                        else o
                        for o in state.observations
                    ),
                )
            )
    peer.release.set()
    result = await pending
    if change == "refresh":
        assert result.status == CommandStatus.APPLIED
        assert len(peer.permissions) == 1
        assert live.runtime.enabled(bound.target) is True
    else:
        assert result.reason == CommandReason.STALE
        assert not peer.permissions


async def test_polling_observes_local_changes_without_corrective_writes(
    authority, monkeypatch
):
    from custom_components.wallbox_manager.protocols.ocpp.v21 import enabled

    _, bound, peer, _ = authority
    monkeypatch.setattr(enabled, "POLL_INTERVAL", 0.01)
    live = bound.adapter
    task = asyncio.create_task(poll_enabled(live, live.token))
    try:
        for value in (True, False, True):
            peer.enabled = value
            changed = asyncio.Event()
            unsubscribe = live.runtime.subscribe(
                lambda _, changed=changed: changed.set()
            )
            try:
                async with asyncio.timeout(1):
                    while live.runtime.enabled(bound.target) is not value:
                        changed.clear()
                        await changed.wait()
            finally:
                unsubscribe()
        assert (
            not peer.requests and not peer.permissions and not peer.authority_requests
        )
        old = live.token
        live.token = live.runtime.connect(
            bound.target.station, protocol="ocpp", protocol_version="2.1"
        )
        assert live.runtime.enabled(bound.target) is None
        assert not live.runtime.observe_enabled(
            old,
            EnabledObservation(
                bound.target,
                True,
                datetime.now(UTC),
                datetime.now(UTC) + timedelta(seconds=15),
            ),
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unknown_and_expired_state_never_restore_desired_permission(controls):
    hass, entities, (control, bound, peer, _, _, _), _, _ = controls
    switch = entities["charging_enabled"]
    before = control.intent(bound.target).generation
    from homeassistant.core import State

    switch.restore_state(State(switch.entity_id, "on"))
    assert control.intent(bound.target).generation == before
    peer.enabled_read_value = "unknown"
    await bound.read_enabled()
    assert switch.is_on is None
    assert hass.states.get(switch.entity_id).state == "unknown"
    assert not peer.permissions


@pytest.mark.parametrize(
    "change", ["refresh", "voltage", "expired", "hardware", "transaction"]
)
async def test_enable_queue_rechecks_prepared_target(authority, change):
    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    control.restore(bound.target, target_w=2300)
    queued, release = asyncio.Event(), asyncio.Event()
    live = bound.adapter

    class Lock(asyncio.Lock):
        count = 0

        async def acquire(self):
            self.count += 1
            if self.count == 2:
                queued.set()
                await release.wait()
            return await super().acquire()

    live._call_lock = Lock()
    pending = asyncio.create_task(control.request_enabled(bound.target, True))
    await asyncio.wait_for(queued.wait(), 1)
    if change == "transaction":
        from test_ocpp21_control import transaction

        await transaction(peer, connector=1, kind="Ended")
        await transaction(peer, identity="replacement", connector=1)
    elif change == "hardware":
        observed(bound, True)
    else:
        measured(
            live.runtime,
            live.token,
            bound.target,
            age=120 if change == "expired" else 0,
        )
        if change == "expired":
            state = live.runtime.get(bound.target.station)
            now = datetime.now(UTC)
            live.runtime._publish(
                replace(
                    state,
                    observations=tuple(
                        replace(
                            o,
                            observed_at=now - timedelta(seconds=120),
                            valid_until=now - timedelta(seconds=60),
                        )
                        if o.channel.quantity
                        in (
                            Quantity.VOLTAGE_L1,
                            Quantity.VOLTAGE_L2,
                            Quantity.VOLTAGE_L3,
                        )
                        else o
                        for o in state.observations
                    ),
                )
            )
        if change == "voltage":
            state = live.runtime.get(bound.target.station)
            live.runtime._publish(
                replace(
                    state,
                    observations=tuple(
                        replace(o, value=240)
                        if o.channel.quantity == Quantity.VOLTAGE_L1
                        else o
                        for o in state.observations
                    ),
                )
            )
    release.set()
    result = await pending
    assert len(peer.requests) == 1
    if change == "refresh":
        assert result.status == CommandStatus.APPLIED
        assert len(peer.permissions) == 1
    else:
        assert result.reason == CommandReason.STALE
        assert not peer.permissions


async def test_zero_support_is_never_inferred_from_other_capabilities(authority):
    control, bound, peer, _ = authority
    peer.enabled = True
    control.restore(bound.target, target_w=2300)
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    state = bound.adapter.runtime.get(bound.target.station)
    bound.adapter.runtime._publish(
        replace(
            state,
            electrical=tuple(c for c in state.electrical if c.key != "zero_current"),
        )
    )
    before = len(peer.requests)
    await control.change(bound.target, target_w=0)
    assert control.intent(bound.target).status == "zero_current_unverified"
    assert len(peer.requests) == before
    assert not peer.permissions


async def test_late_enable_readback_cannot_override_authority_loss(
    authority, monkeypatch
):
    from custom_components.wallbox_manager.protocols.ocpp.v21.adapter import (
        EvseControlAdapter,
    )

    control, bound, peer, _ = authority
    await acquire_for_test(control, bound, peer, start=peer.enabled)
    control.restore(bound.target, target_w=2300)
    entered, release = asyncio.Event(), asyncio.Event()
    original = EvseControlAdapter.read_enabled

    async def delayed(self):
        entered.set()
        await release.wait()
        return await original(self)

    monkeypatch.setattr(EvseControlAdapter, "read_enabled", delayed)
    pending = asyncio.create_task(control.request_enabled(bound.target, True))
    await asyncio.wait_for(entered.wait(), 1)
    bound.adapter.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "local"
        ),
    )
    release.set()
    result = await pending
    assert result.reason == CommandReason.STALE
    assert bound.adapter.runtime.enabled(bound.target) is True  # Hardware did change.
    assert control.intent(bound.target).command_result.reason == CommandReason.STALE
    assert len(peer.permissions) == 1  # No corrective write or retry.


async def test_boot_discovery_reads_actual_without_restoring_permission(authority):
    from test_control_authority import inventory

    control, bound, peer, _ = authority
    live = bound.adapter
    control.restore(bound.target, allowed=True, target_w=2300)
    peer.enabled = True
    rows = inventory()
    for value in (True, False):
        peer.enabled = value
        live.token = live.runtime.boot(
            live.token, live.runtime.get(bound.target.station).identity
        )
        assert live.runtime.enabled(bound.target) is None
        live.electrical_inventory(live.token, rows)
        live.inventory_completed(live.token, rows, datetime.now(UTC))
        changed = asyncio.Event()
        unsubscribe = live.runtime.subscribe(lambda _, changed=changed: changed.set())
        try:
            async with asyncio.timeout(1):
                while live.runtime.enabled(bound.target) is not value:
                    changed.clear()
                    await changed.wait()
        finally:
            unsubscribe()
    assert not peer.permissions and not peer.requests and not peer.authority_requests
    assert not hasattr(control.intent(bound.target).request, "allowed")


async def test_expired_hardware_observation_is_unknown(controls):
    _, entities, (control, bound, peer, _, _, _), _, _ = controls
    peer.enabled = True
    await bound.read_enabled()
    live = bound.adapter
    state = live.runtime.get(bound.target.station)
    now = datetime.now(UTC)
    expired = replace(
        state.enabled[0],
        observed_at=now - timedelta(seconds=20),
        valid_until=now - timedelta(seconds=1),
    )
    live.runtime._publish(replace(state, enabled=(expired,)))
    assert entities["charging_enabled"].is_on is None
    await entities["charging_enabled"].async_turn_on()
    assert control.intent(bound.target).status == "enabled_unknown"
    assert not peer.permissions and not peer.requests
