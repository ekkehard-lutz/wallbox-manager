"""OCPP 1.6J read-only adapter.

Adapted from lbbrhzn/ocpp ocppv16.py (BootNotification/get_supported_features),
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See ../../../THIRD_PARTY_NOTICES.md.
"""

from datetime import UTC, datetime

from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result

from ....core.capabilities import EvidenceState
from ....core.events import StationIdentity
from ....core.models import ConnectorId, EvseId
from ....core.sessions import SessionEvent, SessionEventKind
from ....core.telemetry import Quantity, State
from ..common.adapter import DiscoveryAdapter, evidence
from ..common.metering import (
    CONNECTOR_STATES,
    STATUS16_CHARGING,
    meter_observations,
    reported_time,
    state_observation,
)


def connector_identity(station, connector_id: int):
    """1.6 has no EVSE hierarchy: explicitly map each positive connector to an EVSE."""
    if type(connector_id) is not int or connector_id <= 0:
        raise ValueError("a physical 1.6 connector must be positive")
    evse = EvseId(station, f"connector-{connector_id}")
    return ConnectorId(evse, str(connector_id))


class Adapter(DiscoveryAdapter, ChargePoint):
    @on("BootNotification")
    def on_boot(self, charge_point_vendor, charge_point_model, **kwargs):
        self.register_boot(
            StationIdentity(
                charge_point_vendor,
                charge_point_model,
                kwargs.get("charge_point_serial_number")
                or kwargs.get("charge_box_serial_number"),
                kwargs.get("firmware_version"),
            )
        )
        return call_result.BootNotification(
            current_time=datetime.now(UTC).isoformat(), interval=60, status="Accepted"
        )

    @on("Heartbeat")
    def on_heartbeat(self):
        return call_result.Heartbeat(current_time=datetime.now(UTC).isoformat())

    @on("StatusNotification")
    def on_status(self, connector_id, status, timestamp=None, **kwargs):
        scope = (
            self.token.station
            if connector_id == 0
            else connector_identity(self.token.station, connector_id)
        )
        timestamp = timestamp or datetime.now(UTC).isoformat()
        source = "ocpp1.6:StatusNotification"
        state = CONNECTOR_STATES.get(
            status,
            State.OCCUPIED
            if status
            in ("Preparing", "Charging", "SuspendedEV", "SuspendedEVSE", "Finishing")
            else State.UNKNOWN,
        )
        observations = state_observation(
            scope, Quantity.CONNECTOR_STATE, state, timestamp, source
        )
        if connector_id > 0:
            observations += state_observation(
                scope,
                Quantity.CHARGING_STATE,
                STATUS16_CHARGING.get(status, State.UNKNOWN),
                timestamp,
                source,
            )
        self.runtime.observe(self.token, observations)
        return call_result.StatusNotification()

    @on("MeterValues")
    def on_meter_values(self, connector_id, meter_value, transaction_id=None, **kwargs):
        scope = (
            self.token.station
            if connector_id == 0
            else connector_identity(self.token.station, connector_id)
        )
        self.runtime.observe(
            self.token,
            meter_observations(scope, meter_value, "ocpp1.6:MeterValues", legacy=True),
            session_external_id=str(transaction_id)
            if transaction_id is not None
            else None,
        )
        return call_result.MeterValues()

    @on("StartTransaction")
    def on_start_transaction(self, connector_id, meter_start, timestamp, **kwargs):
        scope = connector_identity(self.token.station, connector_id)
        at = reported_time(timestamp, datetime.now(UTC))
        if at is None or not self.runtime.current(self.token):
            return call_result.StartTransaction(
                transaction_id=0, id_tag_info={"status": "Invalid"}
            )
        external = self.runtime.sessions.start_legacy(
            scope, at, meter_start if meter_start >= 0 else None
        )
        if external is None:
            return call_result.StartTransaction(
                transaction_id=0, id_tag_info={"status": "Invalid"}
            )
        # Record the already station-initiated transaction. No credential lookup,
        # remote start or authorization service is introduced by this response.
        return call_result.StartTransaction(
            transaction_id=int(external), id_tag_info={"status": "Accepted"}
        )

    @on("StopTransaction")
    def on_stop_transaction(
        self, transaction_id, meter_stop, timestamp, reason=None, **kwargs
    ):
        known = self.runtime.sessions.find(self.token.station, str(transaction_id))
        at = reported_time(timestamp, datetime.now(UTC))
        if known is not None and at is not None:
            self.runtime.session_event(
                self.token,
                SessionEvent(
                    known.scope,
                    str(transaction_id),
                    SessionEventKind.ENDED,
                    at,
                    end_reason=reason,
                    meter_wh=meter_stop if meter_stop >= 0 else None,
                ),
            )
        return call_result.StopTransaction()

    async def discover(self, token, attempt):
        response = await self.call(
            call.GetConfiguration(
                key=["SupportedFeatureProfiles", "NumberOfConnectors"]
            ),
            suppress=False,
        )
        values = {
            entry["key"]: entry.get("value")
            for entry in response.configuration_key or []
        }
        source = "ocpp1.6:GetConfiguration:SupportedFeatureProfiles"
        raw = values.get("SupportedFeatureProfiles")
        state = EvidenceState.UNKNOWN
        if isinstance(raw, str) and raw.strip():
            profiles = {p.strip().replace(" ", "") for p in raw.split(",")}
            state = (
                EvidenceState.ADVERTISED
                if "SmartCharging" in profiles
                else EvidenceState.UNSUPPORTED
            )
        schedule = evidence(state, source)
        connectors = ()
        count = values.get("NumberOfConnectors")
        discovery = evidence(EvidenceState.VERIFIED, "ocpp1.6:GetConfiguration")
        if count is not None:
            try:
                count = int(count)
                if not 0 <= count <= 1024:
                    raise ValueError("connector inventory bound")
                connectors = tuple(
                    connector_identity(token.station, n) for n in range(1, count + 1)
                )
            except ValueError, TypeError:
                discovery = evidence(
                    EvidenceState.DEGRADED,
                    "ocpp1.6:GetConfiguration",
                    "invalid_connector_count",
                )
        self.publish(token, attempt, discovery, schedule, connectors=connectors)
