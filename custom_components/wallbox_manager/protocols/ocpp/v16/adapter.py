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
from ..common.adapter import DiscoveryAdapter, evidence


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
    def on_status(self, connector_id, **kwargs):
        # Identity only; charging/error state belongs to the next implementation.
        if connector_id > 0 and self.runtime.current(self.token):
            connector = connector_identity(self.token.station, connector_id)
            state = self.runtime.get(self.token.station)
            self.runtime.discover(
                self.token,
                discovery=state.discovery,
                charging_schedule=state.charging_schedule,
                connectors=(connector,),
            )
        return call_result.StatusNotification()

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
