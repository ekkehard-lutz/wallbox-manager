"""Shared, schema-validated 2.x inventory mechanics, not 2.x feature parity.

Adapted from lbbrhzn/ocpp ocppv201.py _get_inventory/on_report at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See ../../../THIRD_PARTY_NOTICES.md. Reports commit only after complete sequencing.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ocpp.routing import on

from ....core.capabilities import EvidenceState
from ....core.events import StationIdentity
from ....core.models import ConnectorId, EvseId
from .adapter import DiscoveryAdapter, evidence


def evse_identity(station, evse_id):
    if type(evse_id) is not int or evse_id <= 0:
        raise ValueError("physical EVSE ID must be positive")
    return EvseId(station, str(evse_id))


def connector_identity(station, evse_id, connector_id):
    if type(connector_id) is not int or connector_id <= 0:
        raise ValueError("physical connector ID must be positive")
    return ConnectorId(evse_identity(station, evse_id), str(connector_id))


@dataclass
class Report:
    request_id: int
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    next_sequence: int = 0
    rows: list = field(default_factory=list)
    error: bool = False


class InventoryAdapter(DiscoveryAdapter):
    """Concrete adapters supply their own library messages and schema version."""

    _report: Report | None = None

    @on("BootNotification")
    def on_boot(self, charging_station, reason, **kwargs):
        self.register_boot(
            StationIdentity(
                charging_station.get("vendor_name"),
                charging_station.get("model"),
                charging_station.get("serial_number"),
                charging_station.get("firmware_version"),
            )
        )
        return self._call_result.BootNotification(
            current_time=datetime.now(UTC).isoformat(), interval=60, status="Accepted"
        )

    @on("Heartbeat")
    def on_heartbeat(self, **kwargs):
        return self._call_result.Heartbeat(current_time=datetime.now(UTC).isoformat())

    @on("StatusNotification")
    def on_status(self, evse_id, connector_id, **kwargs):
        if evse_id > 0 and connector_id > 0 and self.runtime.current(self.token):
            connector = connector_identity(self.token.station, evse_id, connector_id)
            state = self.runtime.get(self.token.station)
            self.runtime.discover(
                self.token,
                discovery=state.discovery,
                charging_schedule=state.charging_schedule,
                connectors=(connector,),
            )
        return self._call_result.StatusNotification()

    @on("NotifyReport")
    def on_report(
        self, request_id, seq_no, generated_at, report_data=None, tbc=False, **kwargs
    ):
        report = self._report
        if (
            report is not None
            and report.request_id == request_id
            and not report.complete.is_set()
        ):
            rows = report_data or []
            if seq_no != report.next_sequence or len(report.rows) + len(rows) > 10000:
                report.error = True
                report.complete.set()
            else:
                report.rows.extend(rows)
                report.next_sequence += 1
                if not tbc:
                    report.complete.set()
        return self._call_result.NotifyReport()

    async def discover(self, token, attempt):
        source = f"ocpp{self._ocpp_version}:GetBaseReport"
        report = self._report = Report(attempt)
        try:
            response = await self.call(
                self._call.GetBaseReport(
                    request_id=attempt, report_base="FullInventory"
                ),
                suppress=False,
            )
            if response.status != "Accepted":
                state = (
                    EvidenceState.UNSUPPORTED
                    if response.status == "NotSupported"
                    else EvidenceState.DEGRADED
                )
                if response.status == "EmptyResultSet":
                    state = EvidenceState.VERIFIED
                self.publish(
                    token,
                    attempt,
                    evidence(state, source, response.status),
                    evidence(EvidenceState.UNKNOWN, source, "no_inventory"),
                )
                return
            await asyncio.wait_for(report.complete.wait(), self._response_timeout)
            if report.error:
                raise ValueError("invalid_inventory_sequence_or_size")
            schedule, evses, connectors = self.parse_inventory(
                token.station, report.rows, source
            )
            self.publish(
                token,
                attempt,
                evidence(EvidenceState.VERIFIED, source),
                schedule,
                evses,
                connectors,
            )
        finally:
            if self._report is report:
                self._report = None

    @staticmethod
    def parse_inventory(station, rows, source):
        evses, connectors = set(), set()
        available = set()
        for row in rows:
            component = row.get("component", {})
            variable = row.get("variable", {})
            scope = component.get("evse")
            if scope is not None:
                evses.add(evse_identity(station, scope["id"]))
                if scope.get("connector_id") is not None:
                    connectors.add(
                        connector_identity(station, scope["id"], scope["connector_id"])
                    )
            # Do not broaden EVSE-specific assertions to the whole station.
            if (
                component.get("name") == "SmartChargingCtrlr"
                and variable.get("name") == "Available"
                and scope is None
            ):
                for attr in row.get("variable_attribute", []):
                    if attr.get("type", "Actual") == "Actual" and "value" in attr:
                        available.add(attr["value"].strip().casefold())
        state = EvidenceState.UNKNOWN
        if available == {"true"}:
            state = EvidenceState.ADVERTISED
        elif available == {"false"}:
            state = EvidenceState.UNSUPPORTED
        elif available:
            state = EvidenceState.DEGRADED
        return (
            evidence(state, source + ":SmartChargingCtrlr/Available"),
            evses,
            connectors,
        )
