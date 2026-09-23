"""Authority takeover/readback over real OCPP framing, followed by saved intent."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.v21 import call
from test_control_runtime import manual as manual
from test_control_runtime import physical_report
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    EvidenceState,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.authority import (
    COMPONENT,
    VARIABLE,
    accept_authority_events,
    inventory_observation,
    normalized,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)


def inventory():
    result = []
    for name, value, component in (
        ("SupportedPhaseModes", "1,3", {"name": "EVSE", "evse": {"id": 1}}),
        ("MinimumCurrent", "6", {"name": "EVSE", "evse": {"id": 1}}),
        ("CurrentStep", "1", {"name": "EVSE", "evse": {"id": 1}}),
        ("PhaseSwitchingSupported", "true", {"name": "EVSE", "evse": {"id": 1}}),
        (
            "MaximumCurrent1Phase",
            "20",
            {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        ),
        (
            "MaximumCurrent3Phase",
            "27",
            {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        ),
        (
            "ChargingEnableDisableSupported",
            "true",
            {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        ),
        ("ChargingEnabled", "false", COMPONENT),
        ("ControlAuthority", "Local", COMPONENT),
    ):
        result.append(
            {
                "component": component,
                "variable": {"name": name},
                "variable_attribute": [
                    {
                        "type": "Actual",
                        "value": value,
                        "mutability": "ReadWrite"
                        if component == COMPONENT
                        else "ReadOnly",
                    }
                ],
            }
        )
    return result


@pytest.fixture
async def authority(manual):
    _, bound, peer, wire, _, server = manual
    live = bound.adapter
    runtime = live.runtime
    runtime._publish(replace(runtime.get(bound.target.station), protocol_version="2.1"))
    rows = inventory()
    proof = CapabilityEvidence(
        EvidenceState.VERIFIED, "test_inventory", datetime.now(UTC)
    )
    runtime.discover(
        live.token,
        discovery=proof,
        charging_schedule=proof,
        connectors=(bound.target,),
        electrical=live.electrical_inventory(live.token, rows),
    )
    live.inventory_completed(live.token, rows, datetime.now(UTC))
    peer.authority = "Local"
    control = create_control_runtime(runtime, server)
    await physical_report(peer, "RST")
    try:
        yield control, bound, peer, wire
    finally:
        control.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Local", ControlAuthority.LOCAL),
        ("OCPP", ControlAuthority.REMOTE),
        ("remote", ControlAuthority.UNKNOWN),
        ("", ControlAuthority.UNKNOWN),
    ],
)
def test_normalization(raw, expected):
    assert normalized(raw) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"target_w": 2300},
        {"allowed_current_1p": 12.5},
        {"allowed_current_2p": 0},
        {"allowed_current_3p": 24},
        {"allowed": True},
        {"allowed": False},
        {"direction": Direction.UP},
        {"phase_switch_deviation_pct": 8},
    ],
)
async def test_local_edits_only_store_intent(authority, changes):
    control, bound, peer, _ = authority
    await control.change(bound.target, **changes)
    assert control.intent(bound.target).status == "no_authority"
    assert control.attributes(bound.target)["control_authority"] == "local"
    assert (
        control.attributes(bound.target)["execution_blocked_reason"] == "no_authority"
    )
    assert peer.operations == []


@pytest.mark.parametrize("enabled", [True, False])
async def test_takeover_confirms_then_applies_saved_once(authority, enabled):
    control, bound, peer, _ = authority
    control.restore(bound.target, target_w=2300, allowed=enabled)
    saved = replace(control.intent(bound.target))
    result = await control.take_control(bound.target.station)
    assert result.status == CommandStatus.APPLIED
    assert (
        bound.adapter.runtime.authority(bound.target.station) == ControlAuthority.REMOTE
    )
    assert control.intent(bound.target).request == saved.request
    assert control.intent(bound.target).current_limits == saved.current_limits
    assert control.intent(bound.target).generation == saved.generation
    assert peer.authority_requests == [
        {
            "component": COMPONENT,
            "variable": VARIABLE,
            "attribute_type": "Actual",
            "attribute_value": "OCPP",
        }
    ]
    assert peer.operations == [
        "authority_set",
        "authority_get",
        *(["profile"] if enabled else []),
        "permission",
    ]
    assert peer.permissions[0]["attribute_value"] == str(enabled).lower()
    if enabled:
        point = control.intent(bound.target).solver_result.point
        assert point.mode.count == 1 and point.current_a == 10
    else:
        assert not peer.requests
    # Ordinary edits under confirmed Remote never request authority again.
    await control.change(bound.target, target_w=2400)
    assert len(peer.authority_requests) == 1


@pytest.mark.parametrize("outcome", ["Rejected", "Local", "unknown", "timeout"])
async def test_unconfirmed_takeover_does_not_apply(authority, outcome):
    control, bound, peer, _ = authority
    control.restore(bound.target, allowed=True, target_w=2300)
    if outcome == "Rejected":
        peer.authority_status = outcome
    elif outcome == "timeout":
        peer.authority_read_release.clear()
    else:
        peer.authority_read_value = outcome
    result = await control.take_control(bound.target.station)
    assert result.status != CommandStatus.APPLIED
    assert (
        bound.adapter.runtime.authority(bound.target.station) != ControlAuthority.REMOTE
    )
    assert not peer.requests and not peer.permissions
    peer.authority_read_release.set()


@pytest.mark.parametrize(
    "change", ["boot", "reconnect", "edit", "local_event", "close"]
)
async def test_takeover_fences_late_confirmation(authority, change):
    control, bound, peer, _ = authority
    live = bound.adapter
    control.restore(bound.target, allowed=True, target_w=2300)
    peer.authority_read_release.clear()
    pending = asyncio.create_task(control.take_control(bound.target.station))
    await asyncio.wait_for(peer.authority_read_received.wait(), 1)
    if change == "boot":
        live.token = live.runtime.boot(
            live.token, live.runtime.get(bound.target.station).identity
        )
    elif change == "reconnect":
        live.token = live.runtime.connect(
            bound.target.station, protocol="ocpp", protocol_version="2.1"
        )
    elif change == "edit":
        await control.change(bound.target, target_w=4600)
    elif change == "close":
        control.close()
    else:
        live.runtime.observe_authority(
            live.token,
            AuthorityObservation(
                live.token.station,
                ControlAuthority.LOCAL,
                datetime.now(UTC),
                "local-event",
            ),
        )
    peer.authority_read_release.set()
    assert (await pending).reason == CommandReason.STALE
    assert not peer.requests and not peer.permissions
    assert live.runtime.authority(bound.target.station) != ControlAuthority.REMOTE


async def test_local_loss_notification_blocks_normal_commands(authority):
    control, bound, peer, _ = authority
    control.restore(bound.target, target_w=2300, allowed=True)
    await control.take_control(bound.target.station)
    before = list(peer.operations)
    at = datetime.now(UTC).isoformat()
    await peer.call(
        call.NotifyEvent(
            generated_at=at,
            seq_no=0,
            event_data=[
                {
                    "event_id": 0,
                    "timestamp": at,
                    "component": COMPONENT,
                    "variable": VARIABLE,
                    "actual_value": "Local",
                    "event_notification_type": "HardWiredNotification",
                    "trigger": "Delta",
                }
            ],
        ),
        suppress=False,
    )
    await control.change(bound.target, target_w=4600)
    await control.change(bound.target, allowed=False)
    assert control.intent(bound.target).request.target_w == 4600
    assert peer.operations == before
    assert control.intent(bound.target).status == "no_authority"


async def test_inventory_cannot_overwrite_newer_authority_event(authority):
    _, bound, _, _ = authority
    live = bound.adapter
    old = datetime.now(UTC)
    live.runtime.observe_authority(
        live.token,
        AuthorityObservation(
            live.token.station, ControlAuthority.LOCAL, datetime.now(UTC), "fresh-local"
        ),
    )
    rows = inventory()
    rows[-1]["variable_attribute"][0]["value"] = "OCPP"
    inventory_observation(live.runtime, live.token, rows, old)
    assert live.runtime.authority(live.token.station) == ControlAuthority.LOCAL
    event = {
        "component": COMPONENT,
        "variable": VARIABLE,
        "actual_value": "OCPP",
        "event_notification_type": "HardWiredNotification",
        "timestamp": (old - timedelta(seconds=60)).isoformat(),
    }
    accept_authority_events(live.runtime, live.token, [event])
    assert live.runtime.authority(live.token.station) == ControlAuthority.LOCAL


@pytest.mark.parametrize("operation", ["takeover", "power", "disable"])
async def test_local_event_fences_commands_waiting_for_transport(authority, operation):
    control, bound, peer, _ = authority
    live = bound.adapter
    if operation != "takeover":
        await control.take_control(bound.target.station)
    control.restore(bound.target, target_w=2300, allowed=True)
    before = list(peer.operations)
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    live._call_lock = ObservedLock()
    await live._call_lock.acquire()
    pending = asyncio.create_task(
        control.take_control(bound.target.station)
        if operation == "takeover"
        else control.change(
            bound.target,
            **({"target_w": 2400} if operation == "power" else {"allowed": False}),
        )
    )
    await asyncio.wait_for(queued.wait(), 1)
    live.runtime.observe_authority(
        live.token,
        AuthorityObservation(
            live.token.station, ControlAuthority.LOCAL, datetime.now(UTC), "local-event"
        ),
    )
    live._call_lock.release()
    assert (await pending).reason == CommandReason.STALE
    assert peer.operations == before


async def test_local_loss_during_profile_prevents_enable(authority):
    control, bound, peer, _ = authority
    control.restore(bound.target, target_w=2300, allowed=True)
    peer.release.clear()
    pending = asyncio.create_task(control.take_control(bound.target.station))
    await asyncio.wait_for(peer.received.wait(), 1)
    live = bound.adapter
    live.runtime.observe_authority(
        live.token,
        AuthorityObservation(
            live.token.station, ControlAuthority.LOCAL, datetime.now(UTC), "local-event"
        ),
    )
    peer.release.set()
    await pending
    assert peer.operations == ["authority_set", "authority_get", "profile"]
    assert control.intent(bound.target).command_result.reason == CommandReason.STALE


@pytest.mark.parametrize("invalid", ["scope", "instance", "readonly", "unknown"])
async def test_takeover_requires_exact_discovered_writable_endpoint(authority, invalid):
    control, bound, peer, _ = authority
    rows = inventory()
    if invalid == "scope":
        rows[-1]["component"] = {**COMPONENT, "evse": {"id": 1}}
    elif invalid == "instance":
        rows[-1]["variable"] = {**VARIABLE, "instance": "other"}
    elif invalid == "readonly":
        rows[-1]["variable_attribute"][0]["mutability"] = "ReadOnly"
    else:
        rows[-1]["variable_attribute"][0]["value"] = "remote"
    bound.adapter.permission_inventory = (bound.adapter.token, tuple(rows))
    result = await control.take_control(bound.target.station)
    assert result.status == CommandStatus.UNSUPPORTED
    assert not peer.operations


@pytest.mark.parametrize("value", [None, 0])
async def test_invalid_or_zero_phase_voltage_preserves_valid_one_phase(
    authority, value
):
    from custom_components.wallbox_manager.core.telemetry import Quantity

    control, bound, peer, _ = authority
    live = bound.adapter
    state = live.runtime.get(bound.target.station)
    live.runtime._publish(
        replace(
            state,
            observations=tuple(
                replace(o, value=value)
                if o.channel.quantity in (Quantity.VOLTAGE_L2, Quantity.VOLTAGE_L3)
                else o
                for o in state.observations
            ),
        )
    )
    assert peer.operations == []  # Telemetry never applies a new point.
    control.restore(bound.target, target_w=2300, allowed=True)
    await control.take_control(bound.target.station)
    point = control.intent(bound.target).solver_result.point
    assert point.mode.count == 1 and point.current_a == 10
