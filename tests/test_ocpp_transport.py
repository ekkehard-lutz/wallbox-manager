"""Wire-level discovery and lifecycle regressions using local OCPP peers.

Reconnect/stale-finalizer and missing/false/timeout discovery scenarios adapted
from lbbrhzn/ocpp tests/test_reconnect_lifecycle.py, test_initial_start_lifecycle.py,
test_v201_smart_charging_probe.py and test_v201_probe_timeout.py at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See custom_components/wallbox_manager/THIRD_PARTY_NOTICES.md.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.protocols.ocpp.common.transport import (
    ADAPTERS,
    CentralSystem,
    station_from_path,
)
from custom_components.wallbox_manager.runtime import Runtime

PROTOCOLS = tuple(ADAPTERS)


def inventory(available="true"):
    rows = [
        {
            "component": {"name": "Connector", "evse": {"id": e, "connectorId": c}},
            "variable": {"name": "Available"},
            "variableAttribute": [{"value": "true"}],
        }
        for e, c in ((2, 7), (2, 9), (5, 3))
    ]
    if available is not None:
        rows.append(
            {
                "component": {"name": "SmartChargingCtrlr"},
                "variable": {"name": "Available"},
                "variableAttribute": [{"value": available}],
            }
        )
    return rows


class Peer:
    """A small OCPP JSON peer; server-side library validates every wire message."""

    def __init__(self, ws, protocol, handler=None):
        self.ws, self.protocol = ws, protocol
        self.handler = handler or self.respond
        self.pending = {}
        self.tasks = set()
        self.requests = []
        self.sequence = 0
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg[0] == 2:
                    self.requests.append(msg)
                    task = asyncio.create_task(self.handler(self, msg))
                    self.tasks.add(task)
                elif msg[1] in self.pending:
                    self.pending[msg[1]].set_result(msg)
        except ConnectionClosed:
            pass

    async def send(self, msg):
        await self.ws.send(json.dumps(msg))

    async def call(self, action, payload):
        self.sequence += 1
        uid = str(self.sequence)
        future = self.pending[uid] = asyncio.get_running_loop().create_future()
        try:
            await self.send([2, uid, action, payload])
            return await asyncio.wait_for(future, 2)
        finally:
            self.pending.pop(uid, None)

    async def boot(self):
        if self.protocol == "ocpp1.6":
            payload = {
                "chargePointVendor": "Test Vendor",
                "chargePointModel": "Test Model",
                "firmwareVersion": "v7",
                "chargePointSerialNumber": "serial-a",
            }
        else:
            payload = {
                "chargingStation": {
                    "vendorName": "Test Vendor",
                    "model": "Test Model",
                    "firmwareVersion": "v7",
                    "serialNumber": "serial-a",
                },
                "reason": "PowerUp",
            }
        return await self.call("BootNotification", payload)

    @staticmethod
    async def respond(peer, msg):
        if msg[2] == "GetConfiguration":
            await peer.send(
                [
                    3,
                    msg[1],
                    {
                        "configurationKey": [
                            {
                                "key": "SupportedFeatureProfiles",
                                "readonly": True,
                                "value": "Core, SmartCharging",
                            },
                            {
                                "key": "NumberOfConnectors",
                                "readonly": True,
                                "value": "3",
                            },
                        ]
                    },
                ]
            )
        elif msg[2] == "GetBaseReport":
            await peer.send([3, msg[1], {"status": "Accepted"}])
            result = await peer.call(
                "NotifyReport",
                {
                    "requestId": msg[3]["requestId"],
                    "generatedAt": "2026-09-21T10:00:00Z",
                    "seqNo": 0,
                    "reportData": inventory(),
                },
            )
            assert result[0] == 3, result
        else:
            raise AssertionError(f"unexpected outbound action {msg[2]}")

    async def close(self):
        await self.ws.close()
        await self.reader
        for task in self.tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*self.tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, ConnectionClosed
            ):
                raise result


@asynccontextmanager
async def peer(server, protocol, station="station-a", handler=None):
    ws = await connect(
        f"ws://127.0.0.1:{server.port}/{station}", subprotocols=[protocol], proxy=None
    )
    p = Peer(ws, protocol, handler)
    try:
        yield p
    finally:
        await p.close()


async def state_when(runtime, predicate, station="station-a"):
    future = asyncio.get_running_loop().create_future()

    def check(state):
        if (
            state.token.station.value == station
            and predicate(state)
            and not future.done()
        ):
            future.set_result(state)

    unsubscribe = runtime.subscribe(check)
    try:
        existing = runtime.get(StationId(station))
        if existing is not None:
            check(existing)
        return await asyncio.wait_for(future, 2)
    finally:
        unsubscribe()


@pytest.fixture
async def server():
    server = await CentralSystem(
        Runtime(), "127.0.0.1", 0, response_timeout=0.5
    ).start()
    yield server
    await server.stop()
    assert all(not s.tasks and not s.retirement_tasks for s in server.sessions.values())


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_boot_and_discovery(server, protocol):
    assert server.runtime.stations == ()  # No wallbox needed at startup.
    async with peer(server, protocol) as p:
        assert p.ws.subprotocol == protocol
        boot = await p.boot()
        assert boot[0] == 3 and boot[2]["status"] == "Accepted"
        assert boot[2]["interval"] > 0
        assert datetime.fromisoformat(boot[2]["currentTime"]).utcoffset() is not None
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        assert state.token.connection_generation == state.token.boot_generation == 1
        assert state.protocol == "ocpp"
        assert state.protocol_version == protocol.removeprefix("ocpp")
        assert state.identity.firmware == "v7"
        assert state.identity.serial == "serial-a"
        assert state.charging_schedule.state == EvidenceState.ADVERTISED
        assert state.capabilities.envelopes == ()
        assert state.capabilities.stop.state == EvidenceState.UNKNOWN
        if protocol == "ocpp1.6":
            assert {(c.evse.value, c.value) for c in state.connectors} == {
                ("connector-1", "1"),
                ("connector-2", "2"),
                ("connector-3", "3"),
            }
        else:
            assert {(c.evse.value, c.value) for c in state.connectors} == {
                ("2", "7"),
                ("2", "9"),
                ("5", "3"),
            }
        assert {r[2] for r in p.requests} <= {"GetConfiguration", "GetBaseReport"}
        assert (await p.call("Heartbeat", {}))[0] == 3
        adapter = server.sessions[StationId("station-a")].adapter
        assert adapter._ocpp_version == protocol.removeprefix("ocpp")
        assert (
            adapter._call.__name__
            == {
                "ocpp1.6": "ocpp.v16.call",
                "ocpp2.0.1": "ocpp.v201.call",
                "ocpp2.1": "ocpp.v21.call",
            }[protocol]
        )
    offline = await state_when(server.runtime, lambda s: not s.connected)
    assert offline.connectors == state.connectors
    assert offline.identity == state.identity
    assert offline.charging_schedule.state == EvidenceState.UNKNOWN
    assert offline.capabilities.revision > state.capabilities.revision


@pytest.mark.parametrize("protocols", [None, ["ocpp0.9"], ["ocpp2.0"]])
async def test_reject_missing_or_unsupported_subprotocol(server, protocols):
    with pytest.raises(InvalidStatus):
        async with connect(
            f"ws://127.0.0.1:{server.port}/bad", subprotocols=protocols, proxy=None
        ):
            pass
    assert server.runtime.stations == ()


async def test_server_preference_is_deterministic(server):
    async with connect(
        f"ws://127.0.0.1:{server.port}/a",
        subprotocols=list(reversed(PROTOCOLS)),
        proxy=None,
    ) as ws:
        assert ws.subprotocol == "ocpp2.1"


@pytest.mark.parametrize(
    "path", ["/", "/a/b", "/a%2Fb", "/%20", "/a?token=x", "/a#b", "/%00"]
)
def test_invalid_station_paths(path):
    with pytest.raises(ValueError):
        station_from_path(path)


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_reconnect_without_boot_and_boot_on_existing_socket(server, protocol):
    async with peer(server, protocol) as p:
        await p.boot()
        initial = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
    await state_when(server.runtime, lambda s: not s.connected)
    async with peer(server, protocol) as p:
        reconnected = await state_when(
            server.runtime,
            lambda s: s.connected and s.discovery.state == EvidenceState.VERIFIED,
        )
        assert reconnected.token.connection_generation == 2
        assert reconnected.token.boot_generation == 1  # Reconnect isn't reboot.
        assert reconnected.capabilities.revision > initial.capabilities.revision
        await p.boot()
        reboot = await state_when(
            server.runtime,
            lambda s: (
                s.token.boot_generation == 2
                and s.discovery.state == EvidenceState.VERIFIED
            ),
        )
        assert reboot.token.connection_generation == 2
        assert reboot.capabilities.revision > reconnected.capabilities.revision


async def test_replace_old_connection_and_version(server):
    async with peer(server, "ocpp1.6") as old:
        await old.boot()
        first = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        async with peer(server, "ocpp2.1") as new:
            await new.boot()
            current = await state_when(
                server.runtime,
                lambda s: (
                    s.token.connection_generation == 2
                    and s.discovery.state == EvidenceState.VERIFIED
                ),
            )
            server.runtime.disconnect(first.token)
            assert not server.runtime.discover(
                first.token,
                discovery=first.discovery,
                charging_schedule=first.charging_schedule,
            )
            assert server.runtime.get(first.token.station) == current
            assert (await new.call("Heartbeat", {}))[0] == 3


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_two_stations_and_clean_shutdown(server, protocol):
    async with peer(server, protocol, "a") as a, peer(server, protocol, "b") as b:
        await a.boot()
        await b.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED, "a"
        )
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED, "b"
        )
        assert len(server.runtime.stations) == 2
        await server.stop()
        assert all(not state.connected for state in server.runtime.stations)
        assert a.ws.close_code is not None and b.ws.close_code is not None


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ("true", EvidenceState.ADVERTISED),
        ("false", EvidenceState.UNSUPPORTED),
        (None, EvidenceState.UNKNOWN),
        ("invalid", EvidenceState.DEGRADED),
    ],
)
async def test_inventory_evidence_not_verification(
    server, protocol, available, expected
):
    async def respond(p, msg):
        await p.send([3, msg[1], {"status": "Accepted"}])
        await p.call(
            "NotifyReport",
            {
                "requestId": msg[3]["requestId"],
                "generatedAt": "2026-09-21T00:00:00Z",
                "seqNo": 0,
                "reportData": inventory(available),
            },
        )

    async with peer(server, protocol, handler=respond) as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        assert state.charging_schedule.state == expected
        assert not state.capabilities.envelopes


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_timeout_is_degraded_not_unsupported(server, protocol):
    async def ignore(p, msg):
        pass

    async with peer(server, protocol, handler=ignore) as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.DEGRADED
        )
        assert state.charging_schedule.state == EvidenceState.UNKNOWN
        assert state.connected
        assert state.token.connection_generation == state.token.boot_generation == 1
        assert (await p.call("Heartbeat", {}))[0] == 3


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_discovery_not_implemented_does_not_deny_charging(server, protocol):
    async def reject(p, msg):
        await p.send([4, msg[1], "NotImplemented", "not available", {}])

    async with peer(server, protocol, handler=reject) as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.UNSUPPORTED
        )
        assert state.charging_schedule.state == EvidenceState.UNKNOWN
        assert state.capabilities.stop.state == EvidenceState.UNKNOWN
        assert state.connected
        assert (await p.call("Heartbeat", {}))[0] == 3


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("failure", ["timeout", "sequence", "wrong_request"])
async def test_partial_inventory_never_commits(server, protocol, failure):
    async def respond(p, msg):
        await p.send([3, msg[1], {"status": "Accepted"}])
        await p.call(
            "NotifyReport",
            {
                "requestId": msg[3]["requestId"]
                + (1 if failure == "wrong_request" else 0),
                "generatedAt": "2026-09-21T00:00:00Z",
                "seqNo": 1 if failure == "sequence" else 0,
                "reportData": inventory(),
                "tbc": failure == "timeout",
            },
        )

    async with peer(server, protocol, handler=respond) as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.DEGRADED
        )
        assert state.connectors == ()
        assert state.charging_schedule.state == EvidenceState.UNKNOWN
        assert state.connected
        assert state.token.connection_generation == state.token.boot_generation == 1
        assert (await p.call("Heartbeat", {}))[0] == 3


def test_runtime_restart_changes_incarnation():
    a, b = Runtime(), Runtime()
    token = a.connect(StationId("a"))
    other = b.connect(StationId("a"))
    assert token.connection_generation == other.connection_generation == 1
    assert token.boot_generation == other.boot_generation == 0
    assert token.runtime_id != other.runtime_id
    assert not b.current(token)


async def test_snapshots_contain_no_protocol_objects(server):
    def inspect(value):
        assert not type(value).__module__.startswith(
            ("ocpp", "websockets", "homeassistant")
        )
        if is_dataclass(value):
            for field in fields(value):
                inspect(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                inspect(item)

    async with peer(server, "ocpp2.1") as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        inspect(state)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_complete_multipart_inventory(server, protocol):
    part_sent, release = asyncio.Event(), asyncio.Event()

    async def respond(p, msg):
        await p.send([3, msg[1], {"status": "Accepted"}])
        base = {"requestId": msg[3]["requestId"], "generatedAt": "2026-09-21T00:00:00Z"}
        await p.call(
            "NotifyReport",
            {**base, "seqNo": 0, "reportData": inventory()[:1], "tbc": True},
        )
        part_sent.set()
        await release.wait()
        await p.call(
            "NotifyReport", {**base, "seqNo": 1, "reportData": inventory()[1:]}
        )

    async with peer(server, protocol, handler=respond) as p:
        try:
            await p.boot()
            await asyncio.wait_for(part_sent.wait(), 1)
            assert server.runtime.get(StationId("station-a")).connectors == ()
            release.set()
            state = await state_when(
                server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
            )
            assert len(state.connectors) == 3
        finally:
            release.set()


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_reboot_discards_delayed_inventory(server, protocol):
    first_seen, release = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def respond(p, msg):
        nonlocal attempts
        attempts += 1
        await p.send([3, msg[1], {"status": "Accepted"}])
        if attempts == 1:
            first_seen.set()
            await release.wait()
            rows = inventory("false")
        else:
            rows = inventory("true")
        await p.call(
            "NotifyReport",
            {
                "requestId": msg[3]["requestId"],
                "generatedAt": "2026-09-21T00:00:00Z",
                "seqNo": 0,
                "reportData": rows,
            },
        )

    async with peer(server, protocol, handler=respond) as p:
        try:
            await p.boot()
            await asyncio.wait_for(first_seen.wait(), 1)
            await p.boot()
            state = await state_when(
                server.runtime,
                lambda s: (
                    s.token.boot_generation == 2
                    and s.discovery.state == EvidenceState.VERIFIED
                ),
            )
            release.set()
            await asyncio.gather(*p.tasks)
            assert state == server.runtime.get(StationId("station-a"))
            assert state.charging_schedule.state == EvidenceState.ADVERTISED
        finally:
            release.set()


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_status_identity_is_scoped_and_no_state_or_measurements_added(
    server, protocol
):
    async with peer(server, protocol) as p:
        await p.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        if protocol == "ocpp1.6":
            payload = {"connectorId": 0, "errorCode": "NoError", "status": "Available"}
            before = server.runtime.get(StationId("station-a"))
            assert (await p.call("StatusNotification", payload))[0] == 3
            assert server.runtime.get(StationId("station-a")) == before
            payload["connectorId"] = 8
            expected = ("connector-8", "8")
        else:
            payload = {
                "evseId": 9,
                "connectorId": 6,
                "connectorStatus": "Available",
                "timestamp": "2026-09-21T00:00:00Z",
            }
            expected = ("9", "6")
        assert (await p.call("StatusNotification", payload))[0] == 3
        state = server.runtime.get(StationId("station-a"))
        assert expected in {(c.evse.value, c.value) for c in state.connectors}
        assert state.capabilities.envelopes == ()


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("status", ["Rejected", "NotSupported", "EmptyResultSet"])
async def test_inventory_refusal_is_not_physical_incompatibility(
    server, protocol, status
):
    async def respond(p, msg):
        await p.send([3, msg[1], {"status": status}])

    async with peer(server, protocol, handler=respond) as p:
        await p.boot()
        state = await state_when(server.runtime, lambda s: s.discovery.reason == status)
        assert state.charging_schedule.state == EvidenceState.UNKNOWN
        assert not state.capabilities.envelopes
        assert state.connected
        assert (await p.call("Heartbeat", {}))[0] == 3


@pytest.mark.parametrize("profiles", [None, "", "Core", "Core,SmartCharging"])
async def test_v16_profile_absence_and_advertisement(server, profiles):
    async def respond(p, msg):
        entries = (
            []
            if profiles is None
            else [
                {"key": "SupportedFeatureProfiles", "readonly": True, "value": profiles}
            ]
        )
        await p.send(
            [
                3,
                msg[1],
                {"configurationKey": entries, "unknownKey": ["NumberOfConnectors"]},
            ]
        )

    async with peer(server, "ocpp1.6", handler=respond) as p:
        await p.boot()
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        assert not state.connectors  # Missing count never defaults to connector 1.
        expected = (
            EvidenceState.UNKNOWN
            if not profiles
            else EvidenceState.ADVERTISED
            if "SmartCharging" in profiles
            else EvidenceState.UNSUPPORTED
        )
        assert state.charging_schedule.state == expected


async def test_unknown_actions_are_not_control_endpoints(server):
    async with peer(server, "ocpp1.6") as p:
        await p.boot()
        response = await p.call("DataTransfer", {"vendorId": "anything"})
        assert response[0] == 4
        assert response[2] == "NotImplemented"
