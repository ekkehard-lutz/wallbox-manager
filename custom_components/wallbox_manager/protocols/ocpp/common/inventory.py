"""Shared, schema-validated 2.x inventory mechanics, not 2.x feature parity.

Adapted from lbbrhzn/ocpp ocppv201.py _get_inventory/on_report at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See ../../../THIRD_PARTY_NOTICES.md. Reports commit only after complete sequencing.
Reporting/acknowledgement patterns also adapted from upstream ocppv201.py.
Normalization lives in metering.py; concrete adapters select response schemas.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ocpp.routing import on

from ....core.capabilities import EvidenceState
from ....core.events import StationIdentity
from ....core.models import ConnectorId, EvseId
from ....core.sessions import SessionEvent, SessionEventKind
from ....core.telemetry import Quantity, State
from .adapter import DiscoveryAdapter, evidence
from .metering import (
    CHARGING_STATES,
    CONNECTOR_STATES,
    meter_observations,
    reported_time,
    state_observation,
)


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
    def on_status(self, evse_id, connector_id, connector_status, timestamp, **kwargs):
        if evse_id > 0 and connector_id > 0 and self.runtime.current(self.token):
            connector = connector_identity(self.token.station, evse_id, connector_id)
            self.runtime.observe(
                self.token,
                state_observation(
                    connector,
                    Quantity.CONNECTOR_STATE,
                    CONNECTOR_STATES.get(connector_status, State.UNKNOWN),
                    timestamp,
                    f"ocpp{self._ocpp_version}:StatusNotification",
                ),
            )
        return self._call_result.StatusNotification()

    @on("MeterValues")
    def on_meter_values(self, evse_id, meter_value, **kwargs):
        """Normalize only explicitly scoped, supported measurements."""
        scope = (
            self.token.station
            if evse_id == 0
            else evse_identity(self.token.station, evse_id)
        )
        self.runtime.observe(
            self.token,
            meter_observations(
                scope, meter_value, f"ocpp{self._ocpp_version}:MeterValues"
            ),
        )
        return self._call_result.MeterValues()

    @on("TransactionEvent")
    def on_transaction_event(
        self,
        timestamp,
        transaction_info,
        event_type,
        seq_no,
        trigger_reason,
        evse=None,
        meter_value=None,
        offline=False,
        id_token=None,
        **kwargs,
    ):
        """Map transaction lifecycle without authorization or charging controls."""
        known = self.runtime.sessions.find(
            self.token.station, transaction_info["transaction_id"]
        )
        scope = None
        if evse and evse.get("id", 0) > 0:
            scope = evse_identity(self.token.station, evse["id"])
            if evse.get("connector_id") is not None:
                scope = connector_identity(
                    self.token.station, evse["id"], evse["connector_id"]
                )
        elif known is not None:
            scope = known.scope
        # Optional connector information may be omitted on later events. Reuse
        # only a previously explicit connector on that same EVSE; never invent
        # one. Conflicting explicit scopes cannot duplicate/reassign a transaction.
        if known is not None and scope is not None:
            if scope == known.evse_id:
                scope = known.scope
            elif scope != known.scope:
                scope = None
        at = reported_time(timestamp, datetime.now(UTC))
        if scope is not None and at is not None:
            event = SessionEvent(
                scope,
                transaction_info["transaction_id"],
                SessionEventKind(event_type.lower()),
                at,
                CHARGING_STATES.get(transaction_info.get("charging_state")),
                transaction_info.get("stopped_reason")
                or (trigger_reason if event_type == "Ended" else None),
                seq_no,
            )
            self.runtime.session_event(
                self.token,
                event,
                meter_observations(
                    scope,
                    meter_value,
                    f"ocpp{self._ocpp_version}:TransactionEvent",
                    session=True,
                ),
                live=not offline,
            )
        if evse and evse.get("id", 0) > 0 and not offline:
            scope = evse_identity(self.token.station, evse["id"])
            if evse.get("connector_id") is not None:
                scope = connector_identity(
                    self.token.station, evse["id"], evse["connector_id"]
                )
            source = f"ocpp{self._ocpp_version}:TransactionEvent"
            # Embedded measurements belong to the session ledger, not to
            # ordinary metering capability/entity advertisement.
            observations = ()
            if "charging_state" in transaction_info:
                observations += state_observation(
                    scope,
                    Quantity.CHARGING_STATE,
                    CHARGING_STATES.get(
                        transaction_info["charging_state"], State.UNKNOWN
                    ),
                    timestamp,
                    source,
                )
            self.runtime.observe(self.token, observations, track_sessions=False)
        # Neither version requires fields for the reference station's tokenless
        # events. If a token is supplied, include idTokenInfo without pretending
        # to have authorized it. The charging station owns the transaction ID.
        return self._call_result.TransactionEvent(
            id_token_info={"status": "Unknown"} if id_token is not None else None
        )

    @on("NotifyEvent")
    def on_notify_event(self, **kwargs):
        """Acknowledge device events without interpreting authority or state."""
        return self._call_result.NotifyEvent()

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
                self.electrical_inventory(token, report.rows),
            )
        finally:
            if self._report is report:
                self._report = None

    def electrical_inventory(self, token, rows):
        return ()

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
