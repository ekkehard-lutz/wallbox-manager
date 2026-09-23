"""Control through real OCPP 2.1 framing, schemas and a hardware-free peer."""

import asyncio
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
from custom_components.wallbox_manager.core.models import Phase, PhaseMode, StationId
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
        self.status = "Accepted"
        self.error = None
        self.received = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

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
        yield adapter, peer, wire
    finally:
        tasks += list(session.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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


@pytest.mark.parametrize("scope", ["absent", "other_evse", "ended", "ambiguous"])
async def test_transaction_required(connected, scope):
    adapter, peer, wire = connected
    if scope == "other_evse":
        await transaction(peer, evse=2)
    elif scope == "ended":
        await transaction(peer)
        await transaction(peer, kind="Ended")
    elif scope == "ambiguous":
        await transaction(peer, connector=1)
        await transaction(peer, "second", connector=2)
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


@pytest.mark.parametrize("change", ["stale", "ended", "replacement", "disconnect"])
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

    adapter._call_lock = ObservedLock()
    await adapter._call_lock.acquire()
    task = asyncio.create_task(apply(adapter, is_current=lambda: current))
    # Wait until the session-owned call is actually queued on the library lock.
    await asyncio.wait_for(queued.wait(), 1)
    if change == "stale":
        current = False
    elif change == "ended":
        await transaction(peer, kind="Ended")
    elif change == "replacement":
        await transaction(peer, "new")
    else:
        adapter.runtime.disconnect(adapter.token)
    before = len(wire.sent)
    adapter._call_lock.release()
    result = await task
    assert (
        result.reason
        == {
            "stale": CommandReason.STALE,
            "ended": CommandReason.TRANSACTION_UNAVAILABLE,
            "replacement": CommandReason.TRANSACTION_UNAVAILABLE,
            "disconnect": CommandReason.COMMUNICATION_ERROR,
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
