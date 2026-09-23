"""Wire a verified capability source to the generic manual-control runtime."""

from ....control.reference_wallbox_stationary import WallboxStationaryReference
from ....control.runtime import ControlInputs, ControlRuntime
from ....core.capabilities import EvidenceState
from ....core.models import Phase, PhaseVoltage, VoltageObservation
from ....core.telemetry import Channel, Quantity
from .adapter import Adapter


def create_control_runtime(runtime, server, source=None):
    runtime.physical_phase_authorized = (
        source.matches_identity
        if isinstance(source, WallboxStationaryReference)
        else lambda target: False
    )

    def capabilities(target):
        return source.capabilities(target) if source is not None else None

    def inputs(target):
        caps = capabilities(target)
        state = runtime.get(target.station)
        if caps is None or state is None:
            return None
        phases = []
        for phase in Phase:
            sample = state.observation(
                Channel(target, Quantity(f"voltage_{phase.value}"))
            )
            if (
                sample is not None
                and sample.value is not None
                and sample.value > 0
                and sample.valid_until > sample.observed_at
            ):
                phases.append(
                    PhaseVoltage(
                        phase,
                        sample.value,
                        sample.observed_at,
                        sample.valid_until,
                        sample.source,
                    )
                )
        eligible = tuple(
            e.mode
            for e in caps.envelopes
            if e.evidence.state == EvidenceState.VERIFIED
            and (proof := source.phase_operation_evidence(caps, e.mode)) is not None
            and proof.state == EvidenceState.VERIFIED
        )
        return ControlInputs(
            caps,
            VoltageObservation(
                target, state.token.connection_generation, tuple(phases)
            ),
            eligible,
            tuple(source.limits(target)),
            source.current_mode(target),
        )

    def adapter(target):
        session = server.sessions.get(target.station)
        live = session.adapter if session is not None else None
        if not isinstance(live, Adapter) or not runtime.current(live.token):
            return None
        return live.bind_control(
            target,
            lambda: capabilities(target),
            phase_operation_evidence=(
                source.phase_operation_evidence
                if source is not None
                else lambda snapshot, mode: None
            ),
        )

    def blocker(target):
        if adapter(target) is None:
            return "adapter_unavailable"
        active = [
            s for s in runtime.sessions.latest if s.active and s.evse_id == target
        ]
        return None if len(active) == 1 else "transaction_unavailable"

    return ControlRuntime(runtime, inputs, adapter, blocker)
