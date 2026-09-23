"""Control through real OCPP 2.1 framing, schemas and a hardware-free peer."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from ocpp.exceptions import InternalError
from ocpp.routing import on
from ocpp.v21 import ChargePoint, call, call_result

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
    apply_operating_point,
)
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    CapabilitySnapshot,
    ChargingEnvelope,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import (
    EvseId,
    Phase,
    PhaseMode,
    StationId,
)
from custom_components.wallbox_manager.protocols.ocpp.common.sessions import Session
from custom_components.wallbox_manager.protocols.ocpp.v21.adapter import Adapter
from custom_components.wallbox_manager.runtime import Runtime
from custom_components.wallbox_manager.solver.operating_point import OperatingPoint


class Wire:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.error = None

    async def send(self, message):
        if self.error:
            raise self.error
        self.sent.append(message)
        self.other.incoming.put_nowait(message)

    async def recv(self):
        return await self.incoming.get()


class Peer(ChargePoint):
    def __init__(self, wire):
        super().__init__("station", wire)
        self.requests = []
        self.permissions = []
        self.status = "Accepted"
        self.error = None
        self.received = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    @on("SetVariables")
    async def variables(self, set_variable_data):
        self.permissions.extend(set_variable_data)
        return call_result.SetVariables(
            set_variable_result=[
                {
                    "component": item["component"],
                    "variable": item["variable"],
                    "attribute_status": self.status,
                }
                for item in set_variable_data
            ]
        )

    @on("SetChargingProfile")
    async def profile(self, evse_id, charging_profile):
        self.requests.append((evse_id, charging_profile))
        self.received.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return call_result.SetChargingProfile(status=self.status)


@pytest.fixture
async def connected():
    wire, remote = Wire(), Wire()
    wire.other, remote.other = remote, wire
    runtime = Runtime()
    token = runtime.connect(StationId("station"))
    session = Session(wire)
    adapter = Adapter("station", wire, runtime, session, token, response_timeout=0.1)
    peer = Peer(remote)
    tasks = [asyncio.create_task(adapter.start()), asyncio.create_task(peer.start())]
    try:
        yield reference_control(adapter), peer, wire
    finally:
        tasks += list(session.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def evidence(state=EvidenceState.VERIFIED):
    return CapabilityEvidence(state, "test-device-verification", datetime.now(UTC))


def bind_control(adapter, evse, envelopes, operation):
    target = EvseId(adapter.token.station, str(evse))
    snapshot = CapabilitySnapshot(
        target,
        None,
        adapter.token.connection_generation,
        adapter.token.boot_generation,
        1,
        datetime.now(UTC),
        "test-device",
        tuple(envelopes),
        evidence(EvidenceState.UNKNOWN),
    )
    return adapter.bind_control(
        target, lambda: snapshot, phase_operation_evidence=operation
    )


def reference_control(adapter):
    # wallbox-stationary is explicit test data, never a production fallback.
    modes = (PhaseMode((Phase.L1,)), PhaseMode(tuple(Phase)))
    return bind_control(
        adapter,
        1,
        [ChargingEnvelope(mode, 6, 32, 1, evidence()) for mode in modes],
        lambda snapshot, mode: evidence() if mode in modes else None,
    )


async def transaction(peer, identity="active", evse=1, connector=None, kind="Started"):
    await peer.call(
        call.TransactionEvent(
            event_type=kind,
            timestamp=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            trigger_reason="CablePluggedIn",
            seq_no=0,
            transaction_info={"transaction_id": identity},
            evse={"id": evse, **({"connector_id": connector} if connector else {})},
        ),
        suppress=False,
    )


def point(phases=(Phase.L1,), current=Fraction(7)):
    return OperatingPoint(
        True,
        PhaseMode(phases),
        current,
        current * 230 * len(phases),
        (Fraction(230),) * len(phases),
    )


async def apply(adapter, target=None, is_current=lambda: True):
    return await apply_operating_point(
        adapter, target or point(), is_current=is_current
    )


@pytest.mark.parametrize("phases", [(Phase.L1,), tuple(Phase)])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("Accepted", CommandStatus.APPLIED),
        ("Rejected", CommandStatus.TEMPORARILY_REJECTED),
    ],
)
async def test_profile_on_wire(connected, phases, status, expected):
    adapter, peer, _ = connected
    await transaction(peer, connector=7)
    await transaction(peer, "other-evse", evse=2)
    peer.status = status
    assert (await apply(adapter, point(phases))).status == expected
    assert len(peer.requests) == 1
    evse, profile = peer.requests[0]
    assert evse == 1
    assert profile == {
        "id": 1,
        "stack_level": 0,
        "charging_profile_purpose": "TxProfile",
        "charging_profile_kind": "Absolute",
        "transaction_id": "active",
        "charging_schedule": [
            {
                "id": 1,
                "charging_rate_unit": "A",
                "charging_schedule_period": [
                    {
                        "start_period": 0,
                        "limit": 7,
                        "number_phases": len(phases),
                    }
                ],
            }
        ],
    }
    assert (
        type(profile["charging_schedule"][0]["charging_schedule_period"][0]["limit"])
        is int
    )


@pytest.mark.parametrize("evse", [1, 2])
@pytest.mark.parametrize("scope", ["absent", "other_evse", "ended", "ambiguous"])
async def test_transaction_required(connected, scope, evse):
    adapter, peer, wire = connected
    adapter = bind_control(
        adapter.adapter,
        evse,
        adapter._capabilities().envelopes,
        adapter._phase_operation_evidence,
    )
    if scope == "other_evse":
        await transaction(peer, evse=3 - evse)
    elif scope == "ended":
        await transaction(peer, evse=evse)
        await transaction(peer, kind="Ended", evse=evse)
    elif scope == "ambiguous":
        await transaction(peer, connector=1, evse=evse)
        await transaction(peer, "second", connector=2, evse=evse)
    before = len(wire.sent)
    result = await apply(adapter)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert result.reason == CommandReason.TRANSACTION_UNAVAILABLE
    assert len(wire.sent) == before


@pytest.mark.parametrize(
    "target",
    [
        OperatingPoint.off(),
        point(current=Fraction(15, 2)),
        point((Phase.L2,)),
        point((Phase.L1, Phase.L2)),
    ],
)
async def test_unsupported_sends_nothing(connected, target):
    adapter, peer, wire = connected
    await transaction(peer)
    before = len(wire.sent)
    assert (await apply(adapter, target)).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


async def test_stale_before_entry(connected):
    adapter, _, wire = connected
    assert (
        await apply(adapter, is_current=lambda: False)
    ).reason == CommandReason.STALE
    assert wire.sent == []


@pytest.mark.parametrize(
    "change", ["stale", "ended", "replacement", "disconnect", "capabilities", "phase"]
)
async def test_fence_after_library_queue(connected, change):
    adapter, peer, wire = connected
    await transaction(peer)
    current = True
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    adapter.adapter._call_lock = ObservedLock()
    await adapter.adapter._call_lock.acquire()
    task = asyncio.create_task(apply(adapter, is_current=lambda: current))
    # Wait until the session-owned call is actually queued on the library lock.
    await asyncio.wait_for(queued.wait(), 1)
    if change == "stale":
        current = False
    elif change == "ended":
        await transaction(peer, kind="Ended")
    elif change == "replacement":
        await transaction(peer, "new")
    elif change == "capabilities":
        adapter._capabilities = lambda: None
    elif change == "phase":
        adapter._phase_operation_evidence = lambda snapshot, mode: None
    else:
        adapter.adapter.runtime.disconnect(adapter.token)
    before = len(wire.sent)
    adapter.adapter._call_lock.release()
    result = await task
    assert (
        result.reason
        == {
            "stale": CommandReason.STALE,
            "ended": CommandReason.TRANSACTION_UNAVAILABLE,
            "replacement": CommandReason.TRANSACTION_UNAVAILABLE,
            "disconnect": CommandReason.COMMUNICATION_ERROR,
            "capabilities": CommandReason.UNSUPPORTED_OPERATION,
            "phase": CommandReason.UNSUPPORTED_OPERATION,
        }[change]
    )
    assert len(wire.sent) == before


@pytest.mark.parametrize("failure", ["timeout", "connection", "protocol"])
async def test_failures(connected, failure):
    adapter, peer, wire = connected
    await transaction(peer)
    if failure == "timeout":
        peer.release.clear()
    elif failure == "connection":
        wire.error = ConnectionError("private peer data")
    else:
        peer.error = InternalError("private peer data")
    result = await apply(adapter)
    assert result.status == CommandStatus.FAILED
    assert result.reason == (
        CommandReason.TIMEOUT
        if failure == "timeout"
        else CommandReason.COMMUNICATION_ERROR
    )
    assert result.detail is None


async def test_late_result_is_not_rolled_back(connected):
    adapter, peer, _ = connected
    await transaction(peer)
    current = True
    peer.release.clear()
    task = asyncio.create_task(apply(adapter, is_current=lambda: current))
    await peer.received.wait()
    current = False
    peer.release.set()
    assert (await task).status == CommandStatus.APPLIED
    assert len(peer.requests) == 1


@pytest.mark.parametrize("current", [Fraction(13, 2), Fraction(61, 10)])
async def test_evse2_fixed_l2_fractional_grid(connected, current):
    reference, peer, _ = connected
    mode = PhaseMode((Phase.L2,))
    control = bind_control(
        reference.adapter,
        2,
        [ChargingEnvelope(mode, 6, 16, Fraction(1, 10), evidence())],
        # Fixed L2 wiring: count 1 preserves that mapping, no switching support.
        lambda snapshot, requested: evidence() if requested == mode else None,
    )
    await transaction(peer, "evse-one", evse=1)
    await transaction(peer, "evse-two", evse=2, connector=3)
    assert (
        await apply(control, point(mode.phases, current))
    ).status == CommandStatus.APPLIED
    assert len(peer.requests) == 1
    evse, profile = peer.requests[0]
    assert evse == 2
    assert profile["transaction_id"] == "evse-two"
    period = profile["charging_schedule"][0]["charging_schedule_period"][0]
    assert Fraction(str(period["limit"])) == current
    assert period["number_phases"] == 1
    assert "phase_to_use" not in period


@pytest.mark.parametrize("current", [Fraction(5), Fraction(17), Fraction(25, 4)])
async def test_verified_grid_bounds_and_step(connected, current):
    reference, peer, wire = connected
    mode = PhaseMode((Phase.L1,))
    control = bind_control(
        reference.adapter,
        1,
        [ChargingEnvelope(mode, 6, 16, Fraction(1, 2), evidence())],
        lambda snapshot, requested: evidence(),
    )
    await transaction(peer)
    before = len(wire.sent)
    assert (
        await apply(control, point(current=current))
    ).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


async def test_grid_is_anchored_at_minimum(connected):
    reference, peer, _ = connected
    control = bind_control(
        reference.adapter,
        1,
        [
            ChargingEnvelope(
                PhaseMode((Phase.L1,)), Fraction(25, 4), 16, Fraction(1, 2), evidence()
            )
        ],
        lambda snapshot, mode: evidence(),
    )
    await transaction(peer)
    assert (
        await apply(control, point(current=Fraction(27, 4)))
    ).status == CommandStatus.APPLIED
    assert (
        await apply(control, point(current=Fraction(13, 2)))
    ).status == CommandStatus.UNSUPPORTED
    assert len(peer.requests) == 1


async def test_valid_but_unrepresentable_current(connected):
    reference, peer, wire = connected
    control = bind_control(
        reference.adapter,
        1,
        [ChargingEnvelope(PhaseMode((Phase.L1,)), 6, 16, Fraction(1, 3), evidence())],
        lambda snapshot, mode: evidence(),
    )
    await transaction(peer)
    before = len(wire.sent)
    assert (
        await apply(control, point(current=Fraction(19, 3)))
    ).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


@pytest.mark.parametrize(
    "state",
    [
        EvidenceState.UNKNOWN,
        EvidenceState.ADVERTISED,
        EvidenceState.DEGRADED,
        EvidenceState.UNSUPPORTED,
    ],
)
@pytest.mark.parametrize("source", ["envelope", "operation"])
async def test_unverified_support_is_refused(connected, state, source):
    control, peer, wire = connected
    snapshot = control._capabilities()
    if source == "envelope":
        snapshot = replace(
            snapshot,
            envelopes=tuple(
                replace(envelope, evidence=evidence(state))
                for envelope in snapshot.envelopes
            ),
        )
        control._capabilities = lambda: snapshot
    else:
        control._phase_operation_evidence = lambda snapshot, mode: evidence(state)
    await transaction(peer)
    before = len(wire.sent)
    assert (await apply(control)).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


@pytest.mark.parametrize(
    "mismatch", ["none", "station", "evse", "connection", "boot", "firmware"]
)
async def test_capabilities_must_match_binding(connected, mismatch):
    control, peer, wire = connected
    snapshot = control._capabilities()
    changes = {
        "station": {"scope": control.target.station},
        "evse": {"scope": EvseId(control.target.station, "2")},
        "connection": {"connection_generation": 99},
        "boot": {"boot_generation": 99},
        "firmware": {"firmware": "different"},
    }
    snapshot = None if mismatch == "none" else replace(snapshot, **changes[mismatch])
    control._capabilities = lambda: snapshot
    await transaction(peer)
    before = len(wire.sent)
    assert (await apply(control)).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


async def test_phase_transition_requires_operation_proof(connected):
    control, peer, wire = connected
    # Both envelopes are verified, but the device is currently fixed to L1.
    # Knowing a 3-phase envelope does not prove that this operation can switch.
    control._phase_operation_evidence = lambda snapshot, mode: (
        evidence()
        if mode == PhaseMode((Phase.L1,))
        else evidence(EvidenceState.UNSUPPORTED)
    )
    await transaction(peer)
    assert (await apply(control)).status == CommandStatus.APPLIED
    before = len(wire.sent)
    assert (
        await apply(control, point(tuple(Phase)))
    ).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


async def test_reference_atomic_transition(connected):
    control, peer, _ = connected
    await transaction(peer)
    assert (
        await apply(control, point(current=Fraction(16)))
    ).status == CommandStatus.APPLIED
    assert (
        await apply(control, point(tuple(Phase), Fraction(7)))
    ).status == CommandStatus.APPLIED
    assert len(peer.requests) == 2
    assert peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"] == [
        {"start_period": 0, "limit": 7, "number_phases": 3}
    ]


@pytest.mark.parametrize("value", ["0", "-1", "01", "1.0", "x"])
async def test_invalid_binding(connected, value):
    control, _, _ = connected
    with pytest.raises(ValueError, match="canonical positive EVSE"):
        control.adapter.bind_control(
            EvseId(control.target.station, value),
            lambda: None,
            phase_operation_evidence=lambda snapshot, mode: None,
        )


async def test_other_station_binding(connected):
    control, _, _ = connected
    with pytest.raises(ValueError, match="this station"):
        control.adapter.bind_control(
            EvseId(StationId("other"), "1"),
            lambda: None,
            phase_operation_evidence=lambda snapshot, mode: None,
        )


async def test_same_phase_count_does_not_authorize_conductor_selection(connected):
    reference, peer, wire = connected
    fixed = PhaseMode((Phase.L2,))
    control = bind_control(
        reference.adapter,
        1,
        [
            ChargingEnvelope(PhaseMode((p,)), 6, 16, 1, evidence())
            for p in (Phase.L1, Phase.L2)
        ],
        lambda snapshot, mode: evidence() if mode == fixed else None,
    )
    await transaction(peer)
    assert (await apply(control, point(fixed.phases))).status == CommandStatus.APPLIED
    before = len(wire.sent)
    assert (
        await apply(control, point((Phase.L1,)))
    ).status == CommandStatus.UNSUPPORTED
    assert len(wire.sent) == before


async def test_multiple_evse_bindings_share_transport_without_mixing_profiles(
    connected,
):
    first, peer, _ = connected
    second = bind_control(
        first.adapter,
        2,
        first._capabilities().envelopes,
        first._phase_operation_evidence,
    )
    await transaction(peer, "first", evse=1)
    await transaction(peer, "second", evse=2)
    results = await asyncio.gather(apply(first), apply(second, point(tuple(Phase))))
    assert all(result.status == CommandStatus.APPLIED for result in results)
    assert len(peer.requests) == 2
    profiles = dict(peer.requests)
    assert profiles[1]["id"] != profiles[2]["id"]
    assert profiles[1]["transaction_id"] == "first"
    assert profiles[2]["transaction_id"] == "second"
