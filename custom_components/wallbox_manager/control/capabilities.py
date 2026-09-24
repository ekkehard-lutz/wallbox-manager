"""Build solver envelopes only from complete, individually resolved evidence."""

from datetime import UTC, datetime

from ..core.capabilities import (
    CapabilityEvidence,
    CapabilitySnapshot,
    ChargingEnvelope,
    EvidenceState,
)
from ..core.electrical import resolve_capabilities
from ..core.models import ConnectorId, PhaseMode
from .reference import ConfiguredReference


class CapabilityResolver:
    def __init__(self, runtime, reference=None):
        self.runtime = runtime
        self.reference = reference or ConfiguredReference({})

    def resolved(self, target):
        state = self.runtime.get(target.station)
        if (
            not isinstance(target, ConnectorId)
            or state is None
            or not state.connected
            or state.protocol_version != "2.1"
            or target not in state.connectors
        ):
            return ()
        observed = tuple(
            c for c in state.electrical if c.scope in (target, target.evse)
        )
        return resolve_capabilities(
            observed,
            self.reference.capabilities(target, state.capabilities.observed_at),
        )

    def current_mode(self, target):
        state = self.runtime.get(target.station)
        if state is None or not state.connected:
            return None
        return next(
            (
                o.mode
                for o in state.physical_phases
                if o.scope == target and o.fresh(datetime.now(UTC))
            ),
            None,
        )

    def capabilities(self, target):
        fields = {c.key: c for c in self.resolved(target)}
        if not fields:
            return None
        state = self.runtime.get(target.station)
        unknown = CapabilityEvidence(
            EvidenceState.UNKNOWN, "resolved", state.capabilities.observed_at
        )

        def value(key):
            c = fields.get(key)
            return c.value if c and c.evidence.state == EvidenceState.VERIFIED else None

        counts = value("supported_phases") or ()
        # Counts identify canonical EVSE-local modes, never building conductors.
        # Legacy reference_modes options remain readable but are unnecessary here.
        modes = {PhaseMode.canonical(count) for count in counts}
        envelopes = []
        minimum, step = value("minimum_current"), value("current_step")
        if minimum is not None and step is not None:
            for mode in sorted(modes, key=lambda m: m.phases):
                maximum = value(f"maximum_current_{mode.count}")
                if mode.count in counts and maximum is not None and maximum >= minimum:
                    envelopes.append(
                        ChargingEnvelope(
                            mode,
                            minimum,
                            maximum,
                            step,
                            fields[f"maximum_current_{mode.count}"].evidence,
                        )
                    )
        return CapabilitySnapshot(
            target,
            state.identity.firmware,
            state.token.connection_generation,
            state.token.boot_generation,
            state.capabilities.revision,
            state.capabilities.observed_at,
            "per_capability_resolution",
            tuple(envelopes),
            fields["zero_current"].evidence
            if value("zero_current") is True
            else unknown,
        )

    def permission_evidence(self, target):
        return next(
            (
                c.evidence
                for c in self.resolved(target)
                if c.key == "enable_disable" and c.value is True
            ),
            None,
        )

    def phase_operation_evidence(self, snapshot, mode):
        if self.capabilities(snapshot.scope) != snapshot:
            return None
        current = self.current_mode(snapshot.scope)
        if current is None:
            return None
        if current == mode:
            return next(
                (e.evidence for e in snapshot.envelopes if e.mode == mode), None
            )
        return next(
            (
                c.evidence
                for c in self.resolved(snapshot.scope)
                if c.key == "phase_switching" and c.value is True
            ),
            None,
        )

    def limits(self, target):
        return ()
