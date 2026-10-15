"""Permission execution uses SetVariables with independent capability/queue fences."""

import asyncio
from dataclasses import replace

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_ocpp21_control import transaction

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
    apply_charging_permission,
)
from custom_components.wallbox_manager.core.capabilities import EvidenceState


@pytest.mark.parametrize("enabled", [True, False])
async def test_permission_wire_operation(manual, enabled):
    control, bound, peer, _, _, _ = manual
    adapter = control.adapter(bound.target)
    result = await apply_charging_permission(adapter, enabled, is_current=lambda: True)
    assert result.status == CommandStatus.APPLIED
    assert not peer.requests
    assert peer.permissions == [
        {
            "component": {
                "name": "WallboxController",
                "evse": {"id": 1, "connector_id": 1},
            },
            "variable": {"name": "ChargingEnabled"},
            "attribute_type": "Actual",
            "attribute_value": "true" if enabled else "false",
        }
    ]


@pytest.mark.parametrize(
    "missing", ["evidence", "writable", "scope", "generation", "endpoint"]
)
async def test_unsupported_permission_no_side_effect(manual, missing):
    control, bound, peer, _, source, _ = manual
    live = bound.adapter
    if missing == "evidence":
        source.snapshot = replace(
            source.snapshot,
            envelopes=tuple(
                replace(e, evidence=replace(e.evidence, state=EvidenceState.ADVERTISED))
                for e in source.snapshot.envelopes
            ),
        )
    elif missing == "endpoint":
        live.permission_inventory = None
    else:
        token, rows = live.permission_inventory
        row = {**rows[0]}
        if missing == "writable":
            row["variable_attribute"] = [{"mutability": "ReadOnly"}]
        elif missing == "scope":
            row["component"] = {
                "name": "WallboxController",
                "evse": {"id": 1, "connector_id": 2},
            }
        else:
            token = replace(token, boot_generation=token.boot_generation + 1)
        live.permission_inventory = (token, (row,))
    result = await control.change(bound.target, target_w=4600, allowed=True)
    assert result.status == CommandStatus.UNSUPPORTED
    assert not peer.permissions and not peer.requests


async def test_disable_retains_power_without_transaction(manual):
    control, bound, peer, _, _, _ = manual
    control.restore(bound.target, target_w=10000, allowed=True)
    await transaction(peer, connector=1, kind="Ended")
    assert control.blocker(bound.target) == "transaction_unavailable"
    result = await control.change(bound.target, allowed=False)
    assert result.status == CommandStatus.APPLIED
    assert control.intent(bound.target).request.target_w == 10000
    assert not peer.requests


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Rejected", CommandStatus.TEMPORARILY_REJECTED),
        ("Accepted", CommandStatus.APPLIED),
    ],
)
async def test_confirmation(manual, status, expected):
    control, bound, peer, _, _, _ = manual
    peer.status = status
    result = await control.change(bound.target, allowed=False)
    assert result.status == expected


@pytest.mark.parametrize("change", ["boot", "reconnect", "intent", "evidence"])
async def test_permission_queued_fence(manual, change):
    control, bound, peer, _, source, _ = manual
    live = bound.adapter
    entered = asyncio.Event()

    class Lock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                entered.set()
            return await super().acquire()

    live._call_lock = Lock()
    await live._call_lock.acquire()
    pending = asyncio.create_task(control.change(bound.target, allowed=False))
    await asyncio.wait_for(entered.wait(), 1)
    if change == "boot":
        live.token = live.runtime.boot(
            live.token, live.runtime.get(bound.target.station).identity
        )
    elif change == "reconnect":
        live.token = live.runtime.connect(
            bound.target.station, protocol="ocpp", protocol_version="2.1"
        )
    elif change == "intent":
        control.restore(bound.target, target_w=1234)
    else:
        source.snapshot = None
    live._call_lock.release()
    result = await pending
    assert result.status != CommandStatus.APPLIED
    assert not peer.permissions
    if change != "evidence":
        assert result.reason == CommandReason.STALE


async def test_global_permission_requires_single_known_connector(manual):
    from custom_components.wallbox_manager.core.models import ConnectorId

    control, bound, peer, _, _, _ = manual
    live = bound.adapter
    token, rows = live.permission_inventory
    live.permission_inventory = (
        token,
        ({**rows[0], "component": {"name": "WallboxController"}},),
    )
    assert (
        await control.change(bound.target, allowed=False)
    ).status == CommandStatus.APPLIED
    state = live.runtime.get(bound.target.station)
    live.runtime._publish(
        replace(
            state, connectors=(*state.connectors, ConnectorId(bound.target.evse, "2"))
        )
    )
    assert (
        await control.change(bound.target, allowed=False)
    ).status == CommandStatus.UNSUPPORTED
    assert len(peer.permissions) == 1


async def test_multiple_active_connectors_cannot_overwrite_evse_profile(manual):
    control, bound, peer, _, _, _ = manual
    await transaction(peer, identity="other", evse=1, connector=2)
    result = await control.change(bound.target, allowed=True, target_w=4000)
    assert result.status != CommandStatus.APPLIED
    assert not peer.requests
    assert len(peer.permissions) == 1  # Permission is independently scoped.
    assert control.runtime.enabled(bound.target) is True


async def test_on_without_transaction_or_electrical_point_is_idempotent(manual):
    control, bound, peer, _, source, _ = manual
    runtime = control.runtime
    await transaction(peer, connector=1, kind="Ended")
    assert control.blocker(bound.target) == "transaction_unavailable"
    state = runtime.get(bound.target.station)
    runtime._publish(replace(state, observations=(), physical_phases=()))
    source.mode, source.proof = None, False
    control.inputs = lambda target: None
    sessions = runtime.sessions.latest
    assert (
        await control.request_enabled(bound.target, True)
    ).status == CommandStatus.APPLIED
    generation = control.intent(bound.target).generation
    assert (
        await control.request_enabled(bound.target, True)
    ).status == CommandStatus.APPLIED
    assert control.intent(bound.target).generation == generation
    assert peer.operations == ["permission", "enabled_get"]
    assert not peer.requests and control.confirmed_point(bound.target) is None
    assert runtime.sessions.latest == sessions


@pytest.mark.parametrize("watts", [0, 2300])
async def test_permission_on_does_not_relax_transaction_profile_fence(manual, watts):
    control, bound, peer, _, source, _ = manual
    source.snapshot = replace(
        source.snapshot,
        stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
    )
    await transaction(peer, connector=1, kind="Ended")
    assert control.blocker(bound.target) == "transaction_unavailable"
    assert (
        await control.request_enabled(bound.target, True)
    ).status == CommandStatus.APPLIED
    result = await control.change(bound.target, target_w=watts)
    assert result is None
    assert control.intent(bound.target).status == "transaction_unavailable"
    assert not peer.requests
    assert control.confirmed_point(bound.target) is None


async def test_newer_off_revokes_queued_on_before_dispatch(manual):
    control, bound, peer, *_ = manual
    live = bound.adapter
    queued = asyncio.Event()

    class Lock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    live._call_lock = Lock()
    await live._call_lock.acquire()
    on = asyncio.create_task(control.request_enabled(bound.target, True))
    try:
        await asyncio.wait_for(queued.wait(), 1)
        queued.clear()
        off = asyncio.create_task(control.request_enabled(bound.target, False))
        await asyncio.wait_for(queued.wait(), 1)
    finally:
        live._call_lock.release()
    assert (await on).reason == CommandReason.STALE
    assert (await off).status == CommandStatus.APPLIED
    assert [p["attribute_value"] for p in peer.permissions] == ["false"]
    assert control.runtime.enabled(bound.target) is False
    assert not peer.requests


@pytest.mark.parametrize("change", ["off", "local", "roundtrip", "boot", "reconnect"])
async def test_inflight_on_late_ack_cannot_restore_obsolete_intent(manual, change):
    from datetime import UTC, datetime

    from ocpp.v21 import call

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    control, bound, peer, *_ = manual
    live = bound.adapter
    acknowledged, release = asyncio.Event(), asyncio.Event()
    original = live.call

    async def delayed(payload, **kwargs):
        response = await original(payload, **kwargs)
        if (
            isinstance(payload, call.SetVariables)
            and payload.set_variable_data[0]["attribute_value"] == "true"
        ):
            acknowledged.set()
            await release.wait()
        return response

    live.call = delayed
    pending = asyncio.create_task(control.request_enabled(bound.target, True))
    try:
        await asyncio.wait_for(acknowledged.wait(), 1)
        if change == "off":
            assert (
                await control.request_enabled(bound.target, False)
            ).status == CommandStatus.APPLIED
        elif change in ("local", "roundtrip"):
            for value in (
                [ControlAuthority.LOCAL, ControlAuthority.REMOTE]
                if change == "roundtrip"
                else [ControlAuthority.LOCAL]
            ):
                live.runtime.observe_authority(
                    live.token,
                    AuthorityObservation(
                        bound.target.station, value, datetime.now(UTC), "test"
                    ),
                )
        elif change == "boot":
            live.token = live.runtime.boot(
                live.token, live.runtime.get(bound.target.station).identity
            )
        else:
            live.token = live.runtime.connect(
                bound.target.station, protocol="ocpp", protocol_version="2.1"
            )
    finally:
        release.set()
    assert (await pending).reason == CommandReason.STALE
    assert control.confirmed_point(bound.target) is None and not peer.requests
    assert not peer.authority_requests
    if change == "off":
        assert control.runtime.enabled(bound.target) is False
        assert (
            control.intent(bound.target).command_result.status == CommandStatus.APPLIED
        )
        assert [p["attribute_value"] for p in peer.permissions] == ["true", "false"]


@pytest.mark.parametrize("roundtrip", [False, True])
async def test_on_loses_authority_while_queued(manual, roundtrip):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    control, bound, peer, *_ = manual
    live = bound.adapter
    queued = asyncio.Event()

    class Lock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    live._call_lock = Lock()
    await live._call_lock.acquire()
    pending = asyncio.create_task(control.request_enabled(bound.target, True))
    try:
        await asyncio.wait_for(queued.wait(), 1)
        values = (
            [ControlAuthority.LOCAL, ControlAuthority.REMOTE]
            if roundtrip
            else [ControlAuthority.LOCAL]
        )
        for value in values:
            live.runtime.observe_authority(
                live.token,
                AuthorityObservation(
                    bound.target.station, value, datetime.now(UTC), "test"
                ),
            )
    finally:
        live._call_lock.release()
    assert (await pending).reason == CommandReason.STALE
    assert not peer.permissions and not peer.requests and not peer.authority_requests
