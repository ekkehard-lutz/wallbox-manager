"""Explicit operator-attested reference fixture, never automatic discovery.

Selection requires an explicitly attested OCPP station/vendor/model/firmware identity,
plus an acknowledgement that physical L1 / L1-L2-L3 atomic switching and electrical
bounds were verified. A setting alone is not automatic hardware verification.
"""

from datetime import UTC, datetime

from ..core.capabilities import (
    CapabilityEvidence,
    CapabilitySnapshot,
    ChargingEnvelope,
    CurrentLimit,
    EvidenceState,
)
from ..core.models import EvseId, Phase, PhaseMode, StationId


class WallboxStationaryReference:
    def __init__(self, runtime, options):
        self.runtime = runtime
        self.options = options
        self.target = EvseId(StationId(options["reference_station_id"]), "1")
        self.modes = (PhaseMode((Phase.L1,)), PhaseMode(tuple(Phase)))

    def matches_identity(self, target):
        """One fail-closed identity gate for capabilities and physical feedback."""
        state = self.runtime.get(target.station)
        required = ("station_id", "vendor", "model", "firmware")
        if any(
            not isinstance(self.options.get(f"reference_{key}"), str)
            or not self.options[f"reference_{key}"].strip()
            for key in required
        ):
            return False
        return (
            target == self.target
            and target.station.value == self.options["reference_station_id"]
            and state is not None
            and state.connected
            and state.protocol_version == "2.1"
            and self.options.get("reference_verified") is True
            and state.identity.vendor == self.options["reference_vendor"]
            and state.identity.model == self.options["reference_model"]
            and state.identity.firmware == self.options["reference_firmware"]
            and (
                "reference_serial" not in self.options
                or (
                    bool(self.options["reference_serial"])
                    and state.identity.serial == self.options["reference_serial"]
                )
            )
        )

    def capabilities(self, target):
        if not self.matches_identity(target):
            return None
        state = self.runtime.get(target.station)
        evidence = CapabilityEvidence(
            EvidenceState.VERIFIED,
            "operator_verified:wallbox-stationary",
            state.capabilities.observed_at,
        )
        return CapabilitySnapshot(
            target,
            state.identity.firmware,
            state.token.connection_generation,
            state.token.boot_generation,
            state.capabilities.revision,
            state.capabilities.observed_at,
            evidence.source,
            tuple(
                ChargingEnvelope(
                    mode,
                    self.options["reference_min_a"],
                    self.options[f"reference_max_{mode.count}a"],
                    1,
                    evidence,
                )
                for mode in self.modes
            ),
            CapabilityEvidence(
                EvidenceState.UNSUPPORTED,
                evidence.source,
                evidence.observed_at,
                "stop_unimplemented",
            ),
        )

    def phase_operation_evidence(self, snapshot, mode):
        current = self.capabilities(snapshot.scope)
        if (
            current == snapshot
            and mode in self.modes
            and self.current_mode(snapshot.scope) is not None
        ):
            return next(e.evidence for e in snapshot.envelopes if e.mode == mode)
        return None

    def limits(self, target):
        return tuple(
            CurrentLimit(
                mode, 0, self.options[f"limit_{mode.count}a"], "configured_site_limit"
            )
            for mode in self.modes
        )

    def current_mode(self, target):
        if self.capabilities(target) is None:
            return None
        state = self.runtime.get(target.station)
        for observed in state.physical_phases:
            if (
                observed.scope == target
                and observed.source
                == "ocpp2.1:wallbox-stationary:Connector.PhaseRotation"
                and observed.fresh(datetime.now(UTC))
            ):
                return observed.mode
        return None
