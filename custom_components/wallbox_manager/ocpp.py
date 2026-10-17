"""Single-station, read-only OCPP2.1 listener and generation-scoped bootstrap."""

import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

from ocpp.exceptions import OCPPError
from ocpp.routing import on
from ocpp.v21 import ChargePoint, call, call_result
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from . import session
from .meter import CHARGING, CONNECTOR, reported_time, samples

_LOGGER = logging.getLogger(__name__)
CAPABILITIES = {
    "SupportedPhaseModes": "supported_phases",
    "MinimumCurrent": "minimum_current",
    "CurrentStep": "current_step",
    "PhaseSwitchingSupported": "phase_switching",
    "MaximumCurrent": "maximum_current",
    "MaximumCurrent1Phase": "maximum_current_1",
    "MaximumCurrent2Phase": "maximum_current_2",
    "MaximumCurrent3Phase": "maximum_current_3",
    "ZeroCurrentSupported": "zero_current",
    "ChargingEnableDisableSupported": "enable_disable",
}
READ_ONLY = {"GetBaseReport", "GetVariables"}


class Listener:
    def __init__(self, state, host, port, update_device=lambda: None, *, timeout=10):
        self.state, self.host, self.port = state, host, port
        self.update_device, self.timeout = update_device, timeout
        self.server = self.adapter = None
        self._initialization = "disconnected"
        self.lock = asyncio.Lock()

    @property
    def initialization(self):
        status = self._initialization
        if status in ("ready", "missing"):
            status = "missing" if self.state.missing else "ready"
        return {
            "status": status,
            "missing": sorted(self.state.missing),
            "push_only": True,
        }

    async def start(self):
        self.server = await serve(
            self.accept,
            self.host,
            self.port,
            subprotocols=["ocpp2.1"],
            ping_interval=20,
            ping_timeout=20,
            max_size=1048576,
            max_queue=16,
            close_timeout=5,
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def accept(self, connection):
        path = urlsplit(connection.request.path)
        if (
            connection.subprotocol != "ocpp2.1"
            or path.query
            or path.fragment
            or unquote(path.path) != "/" + self.state.binding[0]
        ):
            await connection.close(
                code=1008, reason="Station identity or OCPP protocol mismatch"
            )
            return
        async with self.lock:
            if self.adapter:
                await self.adapter.close()
            generation = self.state.reset(connected=True)
            adapter = Adapter(connection, self, generation)
            self.adapter = adapter
            self._initialization = "awaiting_registration"
        try:
            await adapter.start()
        except ConnectionClosed:
            pass
        except Exception:
            _LOGGER.exception("Passive OCPP connection failed")
        finally:
            await adapter.close()
            if self.adapter is adapter:
                self.state.reset(connected=False)
                self._initialization = "disconnected"
                self.adapter = None

    async def stop(self):
        if self.server:
            self.server.close()
            if self.adapter:
                await self.adapter.close()
            await self.server.wait_closed()
            self.server = None


class Adapter(ChargePoint):
    def __init__(self, connection, listener, generation):
        super().__init__(
            listener.state.binding[0], connection, response_timeout=listener.timeout
        )
        self.listener, self.state, self.generation = (
            listener,
            listener.state,
            generation,
        )
        self.tasks = set()
        self.bootstrap = self.poll = None
        self.report = None
        self.request_id = 0
        self.after_boot = False
        self.closed = False

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def settled(done):
            self.tasks.discard(done)
            # A cancelled bootstrap can leave its shielded response consumer
            # settling later; consume that task's result even without an awaiter.
            if not done.cancelled():
                error = done.exception()
                if error and not isinstance(
                    error, (TimeoutError, OSError, OCPPError, ConnectionClosed)
                ):
                    _LOGGER.error("Passive OCPP task failed", exc_info=error)

        task.add_done_callback(settled)
        return task

    async def request(self, payload):
        # Don't release the library's serial response consumer while an obsolete
        # boot request is still in flight. Teardown owns and joins the pending call.
        pending = self.spawn(super().call(payload, suppress=False))
        return await asyncio.shield(pending)

    async def _send(self, message):
        wire = json.loads(message)
        if wire[0] == 2 and wire[2] not in READ_ONLY:
            raise RuntimeError("Passive OCPP boundary forbids charging commands")
        await super()._send(message)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self._connection.close()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    def live(self, generation):
        return (
            not self.closed
            and self.generation == generation
            and self.state.current(generation)
        )

    def station(self, role, value, source="ocpp2.1:bootstrap"):
        self.state.observe(
            self.generation,
            ("station", (self.state.binding[0], None, None), role),
            value,
            source,
        )

    @on("BootNotification")
    def boot(self, charging_station, reason, **kwargs):
        if not self.live(self.generation):
            return call_result.BootNotification(
                current_time=datetime.now(UTC).isoformat(),
                interval=60,
                status="Rejected",
            )
        for task in (self.bootstrap, self.poll):
            if task:
                task.cancel()
        self.generation = self.state.reset(connected=True, boot=True)
        self.state.identity = {
            k: charging_station[v]
            for k, v in {
                "manufacturer": "vendor_name",
                "model": "model",
                "serial_number": "serial_number",
                "sw_version": "firmware_version",
            }.items()
            if charging_station.get(v)
        }
        self.listener.update_device()
        self.after_boot = True
        return call_result.BootNotification(
            current_time=datetime.now(UTC).isoformat(), interval=60, status="Accepted"
        )

    async def _handle_call(self, message):
        await super()._handle_call(message)
        if self.after_boot:
            self.after_boot = False
            self.bootstrap = self.spawn(self.initialize(self.generation))

    @on("Heartbeat")
    def heartbeat(self, **kwargs):
        return call_result.Heartbeat(current_time=datetime.now(UTC).isoformat())

    def scope(self, evse_id, connector_id=None):
        scope = (
            self.state.binding[0],
            str(evse_id) if evse_id else None,
            str(connector_id) if connector_id else None,
        )
        if (
            type(evse_id) is not int
            or evse_id < 0
            or (
                connector_id is not None
                and (type(connector_id) is not int or connector_id <= 0)
            )
            or not self.state.accepts(scope)
        ):
            raise ValueError("Unexpected station connector")
        return scope

    @on("StatusNotification")
    def status(self, evse_id, connector_id, connector_status, timestamp, **kwargs):
        scope = self.scope(evse_id, connector_id)
        at = reported_time(timestamp, datetime.now(UTC))
        if at:
            self.state.observe(
                self.generation,
                ("observation", scope, "connector_state"),
                CONNECTOR.get(connector_status, "unknown"),
                "ocpp2.1:StatusNotification",
                at,
            )
        return call_result.StatusNotification()

    @on("MeterValues")
    def meters(self, evse_id, meter_value, **kwargs):
        scope = self.scope(evse_id)
        for role, value, at, unit in samples(meter_value):
            if self.state.observe(
                self.generation,
                ("observation", scope, role),
                value,
                "ocpp2.1:MeterValues",
                at,
                unit=unit,
            ):
                session.meter(
                    self.state, role, value, at, channel=("observation", scope, role)
                )
        return call_result.MeterValues()

    @on("TransactionEvent")
    def transaction(
        self,
        event_type,
        timestamp,
        trigger_reason,
        seq_no,
        transaction_info,
        evse=None,
        meter_value=None,
        offline=False,
        id_token=None,
        **kwargs,
    ):
        known = self.state.session
        external_id = transaction_info["transaction_id"]
        if evse:
            scope = self.scope(evse["id"], evse.get("connector_id"))
            if (
                known
                and known["external_transaction_id"] == external_id
                and scope[2] is None
                and tuple(known["scope"])[:2] == scope[:2]
            ):
                scope = tuple(known["scope"])
        elif known and known["external_transaction_id"] == external_id:
            scope = tuple(known["scope"])
        else:
            return call_result.TransactionEvent(
                id_token_info={"status": "Unknown"} if id_token else None
            )
        at = reported_time(timestamp, datetime.now(UTC))
        if (
            at
            and not offline
            and session.event(
                self.state, self.generation, scope, external_id, event_type, at, seq_no
            )
        ):
            if "charging_state" in transaction_info:
                self.state.observe(
                    self.generation,
                    ("observation", scope, "charging_state"),
                    CHARGING.get(transaction_info["charging_state"], "unknown"),
                    "ocpp2.1:TransactionEvent",
                    at,
                )
            for role, value, observed, _unit in samples(meter_value, session=True):
                # Transaction boundary energy is session-only, never lifetime energy.
                session.meter(self.state, role, value, observed, boundary=True)
        return call_result.TransactionEvent(
            id_token_info={"status": "Unknown"} if id_token else None
        )

    @on("NotifyReport")
    def notify_report(
        self, request_id, seq_no, generated_at, report_data=None, tbc=False, **kwargs
    ):
        report = self.report
        if (
            report
            and report["request"] == request_id
            and self.live(report["generation"])
        ):
            if (
                seq_no != report["sequence"]
                or len(report["rows"]) + len(report_data or []) > 10000
            ):
                report["error"] = True
                report["complete"].set()
            else:
                report["sequence"] += 1
                report["rows"].extend(report_data or [])
                if not tbc:
                    report["complete"].set()
        return call_result.NotifyReport()

    @on("NotifyEvent")
    def notify_event(self, event_data, **kwargs):
        for event in event_data:
            if event.get("event_notification_type") == "HardWiredNotification":
                at = reported_time(event.get("timestamp"), datetime.now(UTC))
                if at:
                    self.variable(
                        self.generation,
                        event["component"],
                        event["variable"],
                        event.get("actual_value"),
                        at,
                        "NotifyEvent",
                    )
        return call_result.NotifyEvent()

    def variable(self, generation, component, variable, value, at, source):
        name = variable.get("name")
        evse = component.get("evse")
        scope = (
            self.scope(evse["id"], evse.get("connector_id"))
            if evse
            else (self.state.binding[0], None, None)
        )
        if (
            component.get("name") == "WallboxController"
            and name == "ControlAuthority"
            and evse is None
        ):
            self.state.observe(
                generation,
                ("station", scope, "control_authority"),
                {"Local": "local", "OCPP": "remote"}.get(value, "unknown"),
                "ocpp2.1:" + source,
                at,
            )
        elif component.get("name") == "WallboxController" and name == "ChargingEnabled":
            self.state.observe(
                generation,
                ("control", self.state.binding, "charging_enabled"),
                {"true": True, "false": False}.get(value),
                "ocpp2.1:" + source,
                at,
                valid_until=at + timedelta(seconds=15),
            )
        elif (
            component.get("name") == "Connector"
            and name == "PhaseRotation"
            and scope == self.state.binding
        ):
            self.state.observe(
                generation,
                ("station", (scope[0], None, None), "physical_phases"),
                {"Rxx": 1, "RST": 3}.get(value),
                "ocpp2.1:" + source,
                at,
                valid_until=at + timedelta(seconds=5),
            )

    async def read(self, generation, endpoint):
        at = datetime.now(UTC)
        response = await self.request(
            call.GetVariables(
                get_variable_data=[{**endpoint, "attribute_type": "Actual"}]
            )
        )
        if not self.live(generation):
            return
        rows = response.get_variable_result
        if len(rows) != 1:
            return
        row = rows[0]
        if (
            row.get("component") != endpoint["component"]
            or row.get("variable") != endpoint["variable"]
            or row.get("attribute_type", "Actual") != "Actual"
        ):
            return
        self.variable(
            generation,
            row["component"],
            row["variable"],
            row.get("attribute_value")
            if row.get("attribute_status") == "Accepted"
            else None,
            at,
            "GetVariables",
        )

    async def initialize(self, generation):
        self.listener._initialization = "initializing"
        self.station("discovery", "unknown")
        self.request_id += 1
        report = self.report = {
            "request": self.request_id,
            "generation": generation,
            "sequence": 0,
            "rows": [],
            "error": False,
            "complete": asyncio.Event(),
        }
        permission = None
        try:
            response = await self.request(
                call.GetBaseReport(
                    request_id=self.request_id, report_base="FullInventory"
                )
            )
            if not self.live(generation):
                return
            if response.status != "Accepted":
                self.station("discovery", "unsupported")
                return
            await asyncio.wait_for(report["complete"].wait(), self._response_timeout)
            if not self.live(generation):
                return
            if report["error"]:
                raise ValueError("Invalid report sequence")
            permissions = []
            for row in report["rows"]:
                component = row.get("component", {})
                if evse := component.get("evse"):
                    self.scope(evse["id"], evse.get("connector_id"))
                if component.get("name") == "WallboxController" and row.get(
                    "variable"
                ) == {"name": "ChargingEnabled"}:
                    permissions.append(row)
            if len(permissions) > 1:
                raise ValueError("Ambiguous permission inventory")
            for row in report["rows"]:
                component, variable = row.get("component", {}), row.get("variable", {})
                evse = component.get("evse")
                scope = (
                    self.scope(evse["id"], evse.get("connector_id"))
                    if evse
                    else (self.state.binding[0], None, None)
                )
                attrs = [
                    a
                    for a in row.get("variable_attribute", [])
                    if a.get("type", "Actual") == "Actual"
                ]
                if len(attrs) != 1:
                    continue
                attr = attrs[0]
                name = variable.get("name")
                key = CAPABILITIES.get(name)
                if key and component.get("name") == (
                    "EVSE"
                    if key
                    in (
                        "supported_phases",
                        "minimum_current",
                        "current_step",
                        "phase_switching",
                    )
                    else "Connector"
                ):
                    raw = attr.get("value")
                    try:
                        value = (
                            ",".join(str(int(n.strip())) for n in raw.split(","))
                            if key == "supported_phases"
                            else {"true": True, "false": False}[raw.lower()]
                            if key
                            in ("phase_switching", "zero_current", "enable_disable")
                            else float(raw)
                        )
                        if type(value) is float and (
                            not math.isfinite(value) or value < 0
                        ):
                            value = None
                        if key == "supported_phases" and (
                            not value
                            or any(n not in ("1", "2", "3") for n in value.split(","))
                        ):
                            value = None
                    except ValueError, TypeError, KeyError, AttributeError:
                        value = None
                    self.state.observe(
                        generation,
                        ("capability", scope, key),
                        value,
                        "ocpp2.1:FullInventory",
                        status="verified" if value is not None else "degraded",
                    )
                if name in (
                    "ControlAuthority",
                    "ChargingEnabled",
                    "PhaseRotation",
                ) and attr.get("mutability", "ReadOnly") in ("ReadOnly", "ReadWrite"):
                    endpoint = {"component": component, "variable": variable}
                    # Inventory identifies the endpoint, not a fresh live value.
                    await self.read(generation, endpoint)
                    if name == "ChargingEnabled":
                        if permission is not None:
                            raise ValueError("Ambiguous permission endpoints")
                        permission = endpoint
            self.station("revision", self.request_id)
            self.station("discovery", "verified")
        except TimeoutError, OSError, OCPPError, ConnectionClosed, ValueError:
            if self.live(generation):
                self.station("discovery", "degraded")
        finally:
            if self.live(generation):
                self.listener._initialization = (
                    "missing" if self.state.missing else "ready"
                )
                self.state.publish(
                    ("station", (self.state.binding[0], None, None), "discovery")
                )
                if permission:
                    self.poll = self.spawn(self.poll_permission(generation, permission))

    async def poll_permission(self, generation, endpoint):
        while self.live(generation):
            await asyncio.sleep(5)
            try:
                await self.read(generation, endpoint)
            except TimeoutError, OSError, OCPPError, ConnectionClosed:
                pass
