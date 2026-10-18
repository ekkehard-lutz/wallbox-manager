"""Real OCPP2.1 framing/schemas over local WebSockets, with read-only peers."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from conftest import eventually
from ocpp.exceptions import InternalError, OCPPError
from ocpp.routing import after, on
from ocpp.v21 import ChargePoint, call, call_result
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from custom_components.wallbox_manager.ocpp import READ_ONLY


def row(component, variable, value, evse=None):
    return {
        "component": {"name": component, **({"evse": evse} if evse else {})},
        "variable": {"name": variable},
        "variable_attribute": [
            {"type": "Actual", "value": value, "mutability": "ReadOnly"}
        ],
        "variable_characteristics": {
            "data_type": "string",
            "supports_monitoring": False,
        },
    }


class Station(ChargePoint):
    def __init__(self, socket, *, unsupported=False):
        super().__init__("Wallbox01", socket, response_timeout=2)
        self.operations = []
        self.reads = []
        self.fail_reads = set()
        self.background = []
        self.request_id = None
        self.unsupported = unsupported
        self.values = {
            "ControlAuthority": "Local",
            "ChargingEnabled": "true",
            "PhaseRotation": "Rxx",
        }
        self.rows = [
            row("WallboxController", "ControlAuthority", "Local"),
            row("WallboxController", "ChargingEnabled", "true"),
            row("Connector", "PhaseRotation", "Rxx", {"id": 1, "connector_id": 1}),
            row("EVSE", "MinimumCurrent", "6", {"id": 1}),
            row(
                "Connector", "MaximumCurrent1Phase", "16", {"id": 1, "connector_id": 1}
            ),
        ]

    async def route_message(self, raw):
        message = json.loads(raw)
        if message[0] == 2:
            self.operations.append(message[2])
        return await super().route_message(raw)

    @on("GetBaseReport")
    def inventory(self, request_id, report_base):
        self.request_id = request_id
        return call_result.GetBaseReport(
            status="NotSupported" if self.unsupported else "Accepted"
        )

    @after("GetBaseReport")
    def report(self, **kwargs):
        if not self.unsupported:
            self.background.append(
                asyncio.create_task(
                    self.call(
                        call.NotifyReport(
                            request_id=self.request_id,
                            generated_at=datetime.now(UTC).isoformat(),
                            seq_no=0,
                            tbc=False,
                            report_data=self.rows,
                        )
                    )
                )
            )

    @on("GetVariables")
    def read(self, get_variable_data):
        name = get_variable_data[0]["variable"]["name"]
        self.reads.append(name)
        if name in self.fail_reads:
            self.fail_reads.remove(name)
            raise InternalError("Simulated optional read failure")
        return call_result.GetVariables(
            get_variable_result=[
                {
                    **item,
                    "attribute_status": "Accepted",
                    "attribute_value": self.values[item["variable"]["name"]],
                }
                for item in get_variable_data
            ]
        )


@asynccontextmanager
async def peer(entry, *, unsupported=False):
    async with connect(
        f"ws://127.0.0.1:{entry.runtime_data.server.port}/Wallbox01",
        subprotocols=["ocpp2.1"],
    ) as socket:
        station = Station(socket, unsupported=unsupported)
        reader = asyncio.create_task(station.start())
        try:
            yield station
        finally:
            await socket.close()
            reader.cancel()
            await asyncio.gather(reader, *station.background, return_exceptions=True)


async def boot(station):
    response = await station.call(
        call.BootNotification(
            charging_station={
                "vendor_name": "Test",
                "model": "Passive",
                "serial_number": "serial-1",
            },
            reason="PowerUp",
        )
    )
    assert response.status == "Accepted"


@pytest.mark.parametrize("failed", ["ControlAuthority", "ChargingEnabled"])
async def test_optional_read_failure_preserves_later_readbacks_and_permission_poll(
    ha, failed
):
    entry = await ha.install()
    listener, state = entry.runtime_data.server, entry.runtime_data.state
    async with peer(entry) as station:
        station.fail_reads.add(failed)
        await boot(station)
        await eventually(lambda: listener.initialization["status"] == "missing")
        assert station.reads == ["ControlAuthority", "ChargingEnabled", "PhaseRotation"]
        assert (
            state.value(("station", ("Wallbox01", None, None), "physical_phases")) == 1
        )
        assert (
            state.value(("capability", ("Wallbox01", "1", None), "minimum_current"))
            == 6
        )
        authority = ("station", ("Wallbox01", None, None), "control_authority")
        permission = ("control", state.binding, "charging_enabled")
        assert state.value(authority) == (
            None if failed == "ControlAuthority" else "local"
        )
        assert state.value(permission) == (
            None if failed == "ChargingEnabled" else True
        )
        assert listener.adapter.poll is not None
        if failed == "ChargingEnabled":
            await asyncio.sleep(5.2)
            await eventually(lambda: state.value(permission) is True)
            assert station.reads == [
                "ControlAuthority",
                "ChargingEnabled",
                "PhaseRotation",
                "ChargingEnabled",
            ]
        assert set(station.operations) <= READ_ONLY


async def test_real_boot_readback_push_reconnect_and_no_control(ha):
    entry = await ha.install()
    listener, state = entry.runtime_data.server, entry.runtime_data.state
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: listener.initialization["status"] == "missing")
        assert state.identity["serial_number"] == "serial-1"
        assert (
            state.value(("station", ("Wallbox01", None, None), "control_authority"))
            == "local"
        )
        assert state.value(("control", state.binding, "charging_enabled")) is True
        assert (
            state.value(("station", ("Wallbox01", None, None), "physical_phases")) == 1
        )
        response = await station.call(call.Heartbeat())
        assert response.current_time
        await station.call(
            call.StatusNotification(
                timestamp=datetime.now(UTC).isoformat(),
                connector_status="Available",
                evse_id=1,
                connector_id=1,
            )
        )
        await station.call(
            call.MeterValues(
                evse_id=1,
                meter_value=[
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "sampled_value": [
                            {
                                "measurand": "Power.Active.Import",
                                "value": 1500,
                                "unit_of_measure": {"unit": "W"},
                            },
                            {"value": 100000, "unit_of_measure": {"unit": "Wh"}},
                            {
                                "measurand": "Current.Import",
                                "phase": "L2",
                                "value": 0,
                                "unit_of_measure": {"unit": "A"},
                            },
                        ],
                    }
                ],
            )
        )
        assert state.value(("observation", ("Wallbox01", "1", None), "energy")) == 100
        assert "current_l2" not in listener.initialization["missing"]
        assert "charging_state" in listener.initialization["missing"]
        await station.call(
            call.TransactionEvent(
                event_type="Started",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="CablePluggedIn",
                seq_no=0,
                transaction_info={
                    "transaction_id": "real-42",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        assert state.session["external_transaction_id"] == "real-42"
        await entry.runtime_data.settings.set("charging_profile", "PV_MAXIMUM")
        await entry.runtime_data.settings.set("soll_power", 8.5)
        assert set(station.operations) <= READ_ONLY
        old_generation = state.generation
        old_observation = state.observations[
            ("observation", ("Wallbox01", "1", None), "power")
        ]
    await eventually(lambda: not state.connected)
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: listener.initialization["status"] == "missing")
        assert state.generation != old_generation
        assert state.value(("observation", ("Wallbox01", "1", None), "power")) is None
        assert state.session.get("generation") != list(state.generation)
        assert not state.observe(
            old_generation,
            ("observation", ("Wallbox01", "1", None), "power"),
            999,
            "late",
        )
        assert not state.observe(
            state.generation,
            ("observation", ("Wallbox01", "1", None), "power"),
            999,
            "old-source",
            old_observation.observed_at,
        )
        # Steady unchanged telemetry becomes valid only when resent in this generation.
        await station.call(
            call.MeterValues(
                evse_id=1,
                meter_value=[
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "sampled_value": [
                            {
                                "measurand": "Current.Import",
                                "phase": "L2",
                                "value": 0,
                                "unit_of_measure": {"unit": "A"},
                            }
                        ],
                    }
                ],
            )
        )
        assert state.value(("observation", ("Wallbox01", "1", None), "current_l2")) == 0
        assert set(station.operations) <= READ_ONLY
        assert station.operations.count("GetBaseReport") == 1


async def test_unsupported_inventory_missing_fields_and_wrong_station(ha):
    entry = await ha.install()
    async with peer(entry, unsupported=True) as station:
        await boot(station)
        await eventually(
            lambda: entry.runtime_data.server.initialization["status"] == "missing"
        )
        assert station.operations == ["GetBaseReport"]
        assert (
            entry.runtime_data.state.value(
                ("control", ("Wallbox01", "1", "1"), "charging_enabled")
            )
            is None
        )
    async with connect(
        f"ws://127.0.0.1:{entry.runtime_data.server.port}/Other",
        subprotocols=["ocpp2.1"],
    ) as socket:
        with pytest.raises(ConnectionClosed):
            await socket.recv()
    assert entry.runtime_data.state.binding == ("Wallbox01", "1", "1")


async def test_duplicate_boot_replaces_bootstrap_and_connector_isolation(ha):
    entry = await ha.install()
    async with peer(entry) as station:
        await boot(station)
        await eventually(
            lambda: entry.runtime_data.server.initialization["status"] == "missing"
        )
        adapter = entry.runtime_data.server.adapter
        old_task = adapter.poll
        previous = entry.runtime_data.state.generation
        await boot(station)
        await eventually(
            lambda: (
                entry.runtime_data.server.initialization["status"] == "missing"
                and adapter.poll is not old_task
            )
        )
        assert old_task.done()
        assert entry.runtime_data.state.generation[1] == previous[1] + 1
        assert (
            sum(not t.done() for t in adapter.tasks) == 1
        )  # one permission poll, no second heartbeat
        with pytest.raises(OCPPError):
            await station.call(
                call.StatusNotification(
                    timestamp=datetime.now(UTC).isoformat(),
                    connector_status="Occupied",
                    evse_id=1,
                    connector_id=2,
                ),
                suppress=False,
            )
        assert not any(
            key[1][2] == "2" for key in entry.runtime_data.state.observations
        )
        with pytest.raises(RuntimeError, match="forbids"):
            await adapter._send(json.dumps([2, "forbidden", "SetVariables", {}]))


async def test_permission_poll_five_seconds_and_cleanup(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: entry.runtime_data.server.adapter.poll is not None)
        adapter = entry.runtime_data.server.adapter
        # Observe the actual loop delay without waiting five real seconds.
        poll = adapter.poll
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        requested = asyncio.Event()
        delays = []

        async def sleep(seconds):
            delays.append(seconds)
            requested.set()
            await asyncio.Event().wait()

        import custom_components.wallbox_manager.ocpp as transport

        monkeypatch.setattr(transport.asyncio, "sleep", sleep)
        task = asyncio.create_task(
            adapter.poll_permission(
                adapter.generation,
                {
                    "component": {"name": "WallboxController"},
                    "variable": {"name": "ChargingEnabled"},
                },
            )
        )
        await requested.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert delays == [5]
        monkeypatch.undo()
    await eventually(lambda: entry.runtime_data.server.adapter is None)
    assert all(t.done() for t in adapter.tasks)


async def test_real_idle_connection_only_polls_permission(ha):
    entry = await ha.install()
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: entry.runtime_data.server.adapter.poll is not None)
        before = list(station.operations)
        await asyncio.sleep(5.2)
        assert station.operations[len(before) :] == ["GetVariables"]
        assert entry.runtime_data.server.adapter.poll is not None
        assert set(station.operations) <= READ_ONLY


async def test_inventory_rejects_conflicts_invalid_units_and_wrong_scopes(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    async with peer(entry) as station:
        evse, connector = {"id": 1}, {"id": 1, "connector_id": 1}
        wrong_unit = row("Connector", "MaximumCurrent", "16", connector)
        wrong_unit["variable_characteristics"]["unit"] = "kW"
        station.rows = [
            row("EVSE", "MinimumCurrent", "6", evse),
            row("EVSE", "MinimumCurrent", "7", evse),
            row("EVSE", "MinimumCurrent", "7", evse),
            row("EVSE", "CurrentStep", "0", evse),
            row("EVSE", "SupportedPhaseModes", "1,1,3", evse),
            row("EVSE", "PhaseSwitchingSupported", "true", connector),
            wrong_unit,
        ]
        await boot(station)
        await eventually(
            lambda: entry.runtime_data.server.initialization["status"] == "missing"
        )
        for scope, role in (
            (("Wallbox01", "1", None), "minimum_current"),
            (("Wallbox01", "1", None), "current_step"),
            (("Wallbox01", "1", None), "supported_phases"),
            (state.binding, "maximum_current"),
        ):
            assert state.value(("capability", scope, role)) is None
        assert not any(k[2] == "phase_switching" for k in state.observations)
        report = entry.runtime_data.server.adapter.report
        rows = list(report["rows"])
        await station.call(
            call.NotifyReport(
                request_id=station.request_id,
                seq_no=1,
                generated_at=datetime.now(UTC).isoformat(),
                report_data=[wrong_unit],
            )
        )
        assert report["rows"] == rows
        assert not report["error"]


async def test_transaction_measurement_after_its_event_is_not_live_proof(ha):
    from datetime import timedelta

    entry = await ha.install()
    async with peer(entry, unsupported=True) as station:
        await boot(station)
        now = datetime.now(UTC)
        await station.call(
            call.TransactionEvent(
                event_type="Started",
                timestamp=now.isoformat(),
                trigger_reason="Authorized",
                seq_no=0,
                transaction_info={"transaction_id": "actual"},
                evse={"id": 1, "connector_id": 1},
                meter_value=[
                    {
                        "timestamp": (now + timedelta(seconds=2)).isoformat(),
                        "sampled_value": [
                            {
                                "measurand": "Power.Active.Import",
                                "value": 1000,
                                "unit_of_measure": {"unit": "W"},
                            }
                        ],
                    }
                ],
            )
        )
        state = entry.runtime_data.state
        assert state.session["external_transaction_id"] == "actual"
        assert state.session.get("power_channel") is None
        assert not any(k[0] == "transaction" for k in state.observations)


async def test_late_response_from_replaced_boot_is_not_current_evidence(ha):
    entry = await ha.install()
    listener, state = entry.runtime_data.server, entry.runtime_data.state
    async with connect(
        f"ws://127.0.0.1:{listener.port}/Wallbox01", subprotocols=["ocpp2.1"]
    ) as socket:

        async def send(message):
            await socket.send(json.dumps(message))

        async def receive():
            return json.loads(await asyncio.wait_for(socket.recv(), 2))

        notification = {
            "chargingStation": {"vendorName": "Test", "model": "Passive"},
            "reason": "PowerUp",
        }
        await send([2, "boot-1", "BootNotification", notification])
        assert (await receive())[:2] == [3, "boot-1"]
        request = await receive()
        assert request[2] == "GetBaseReport"
        await send([3, request[1], {"status": "Accepted"}])
        rawrow = {
            "component": {"name": "WallboxController"},
            "variable": {"name": "ControlAuthority"},
            "variableAttribute": [
                {"type": "Actual", "value": "Local", "mutability": "ReadOnly"}
            ],
            "variableCharacteristics": {
                "dataType": "string",
                "supportsMonitoring": False,
            },
        }
        await send(
            [
                2,
                "report-1",
                "NotifyReport",
                {
                    "requestId": request[3]["requestId"],
                    "seqNo": 0,
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "reportData": [rawrow],
                },
            ]
        )
        messages = [await receive(), await receive()]
        old_read = next(m for m in messages if m[0] == 2)
        assert old_read[2] == "GetVariables"
        previous = state.generation
        await send([2, "boot-2", "BootNotification", notification])
        assert (await receive())[:2] == [3, "boot-2"]
        assert state.generation != previous
        await send(
            [
                3,
                old_read[1],
                {
                    "getVariableResult": [
                        {
                            "component": {"name": "WallboxController"},
                            "variable": {"name": "ControlAuthority"},
                            "attributeStatus": "Accepted",
                            "attributeValue": "OCPP",
                        }
                    ]
                },
            ]
        )
        current_request = await receive()
        assert current_request[2] == "GetBaseReport"
        assert (
            state.value(("station", ("Wallbox01", None, None), "control_authority"))
            is None
        )
        await send([3, current_request[1], {"status": "NotSupported"}])
        await eventually(lambda: listener.initialization["status"] == "missing")
        assert all(key[2] != "control_authority" for key in state.observations)


@pytest.mark.parametrize("level", [1, 2, 3])
async def test_lifecycle_diagnostics_on_real_reconnect_without_meter_spam(
    ha, caplog, level
):
    entry = await ha.install()
    ha.config_entries.async_update_entry(entry, options={"diagnostic_level": level})
    state = entry.runtime_data.state
    for _ in range(2):
        async with peer(entry) as station:
            await boot(station)
            await eventually(lambda: entry.runtime_data.server.adapter.bootstrap.done())
            before = sum("WBMGR subsystem=ocpp " in r.message for r in caplog.records)
            for _ in range(10):
                await station.call(
                    call.MeterValues(
                        evse_id=1,
                        meter_value=[
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "sampled_value": [
                                    {
                                        "measurand": "Power.Active.Import",
                                        "value": 100,
                                        "unit_of_measure": {"unit": "W"},
                                    }
                                ],
                            }
                        ],
                    )
                )
            assert (
                sum("WBMGR subsystem=ocpp " in r.message for r in caplog.records)
                == before
            )
            assert set(station.operations) <= READ_ONLY
        await eventually(lambda: not state.connected)
    records = [
        json.loads(record.message.split("WBMGR subsystem=ocpp ")[1])
        for record in caplog.records
        if "WBMGR subsystem=ocpp " in record.message
    ]
    events = [record["event"] for record in records]
    assert events.count("connection_accepted") == 1
    assert events.count("reconnect") == 1
    assert events.count("disconnected") == 2
    assert events.count("boot_notification") == 2
    assert events.count("initialization_degraded") == 2
    if level == 1:
        assert len(events) == 8
    else:
        assert "transition_control_authority" in events
    if level == 3:
        assert "readback_PhaseRotation" in events


async def test_real_notify_event_empty_phase_does_not_flap_last_observed_sensor(ha):
    from conftest import entities
    from homeassistant.core import callback

    entry = await ha.install()
    state = entry.runtime_data.state
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    phase = next(e for e in entities(ha, entry) if e.key == key)
    changes = []

    @callback
    def changed(event):
        if event.data["entity_id"] == phase.entity_id:
            changes.append(event.data["new_state"].state)

    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: entry.runtime_data.server.adapter.bootstrap.done())
        generation = state.generation
        stop = None
        for i, (raw, display, observed) in enumerate(
            [
                ("RST", 3, 3),
                ("", 3, 3),
                ("RST", 3, 3),
                ("", 3, 3),
                ("", None, None),
                ("RST", 3, 3),
            ]
        ):
            await station.call(
                call.NotifyEvent(
                    generated_at=datetime.now(UTC).isoformat(),
                    seq_no=i,
                    event_data=[
                        {
                            "event_id": i,
                            "timestamp": datetime.now(UTC).isoformat(),
                            "trigger": "Alerting",
                            "actual_value": raw,
                            "event_notification_type": "HardWiredNotification",
                            "component": {
                                "name": "Connector",
                                "evse": {"id": 1, "connector_id": 1},
                            },
                            "variable": {"name": "PhaseRotation"},
                        }
                    ],
                )
            )
            await ha.async_block_till_done()
            assert state.generation == generation
            assert state.value(key) == observed
            assert phase.native_value == display
            assert phase.available == (display is not None)
            if i == 0:
                stop = ha.bus.async_listen("state_changed", changed)
            elif i <= 2:
                assert changes == []
        assert changes == ["unavailable", "3"]
        assert set(station.operations) <= READ_ONLY
        stop()


async def test_response_timeout_preserves_observation_and_healthy_transport(ha):
    from ocpp.v21 import call as requests

    entry = await ha.install()
    listener, state = entry.runtime_data.server, entry.runtime_data.state
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: listener.adapter.bootstrap.done())
        adapter = listener.adapter
        adapter.poll.cancel()
        await asyncio.gather(adapter.poll, return_exceptions=True)
        adapter._response_timeout = 0.03
        key = ("control", state.binding, "charging_enabled")
        previous = state.observations[key]
        # Stop the peer's receiver while leaving its WebSocket transport alive.
        # No CALLRESULT is produced, so the actual library response timer fires.
        original = station.route_message

        async def ignore_get_variables(raw):
            wire = json.loads(raw)
            if wire[0] == 2 and wire[2] == "GetVariables":
                return
            await original(raw)

        station.route_message = ignore_get_variables
        with pytest.raises(TimeoutError):
            await adapter.read(
                adapter.generation,
                {
                    "component": {"name": "WallboxController"},
                    "variable": {"name": "ChargingEnabled"},
                },
            )
        assert state.observations[key] is previous
        assert state.value(key) is True and state.connected
        assert (await station.call(requests.Heartbeat())).current_time


async def test_ping_timeout_still_invalidates_connection(ha, monkeypatch):
    from websockets.frames import OP_PONG

    from custom_components.wallbox_manager import ocpp

    serve = ocpp.serve
    configured = {}

    def quick_keepalive(*args, **kwargs):
        configured.update(kwargs)
        return serve(*args, **{**kwargs, "ping_interval": 0.03, "ping_timeout": 0.03})

    monkeypatch.setattr(ocpp, "serve", quick_keepalive)
    entry = await ha.install()
    state = entry.runtime_data.state
    assert configured["ping_interval"] == configured["ping_timeout"] == 20
    async with peer(entry) as station:
        await eventually(lambda: state.connected)
        socket = station._connection
        send_frame = socket.protocol.send_frame

        def drop_pong(frame):
            if frame.opcode is not OP_PONG:
                send_frame(frame)

        monkeypatch.setattr(socket.protocol, "send_frame", drop_pong)
        await eventually(lambda: not state.connected)
        assert socket.close_code == 1011
