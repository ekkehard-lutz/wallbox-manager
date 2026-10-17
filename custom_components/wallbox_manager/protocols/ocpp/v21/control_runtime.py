"""Wire a verified capability source to the generic manual-control runtime."""

from ....control.capabilities import CapabilityResolver
from ....control.reference import ConfiguredReference
from ....control.runtime import ControlInputs, ControlRuntime
from ....core.capabilities import EvidenceState
from ....core.events import actively_charging as actively_charging
from ....core.models import ConnectorId, Phase, PhaseVoltage, VoltageObservation
from ....core.telemetry import Channel, Quantity
from .adapter import Adapter


def create_control_runtime(runtime, server, source=None):
    if source is None or isinstance(source, ConfiguredReference):
        source = CapabilityResolver(runtime, source)

    def capabilities(target):
        return source.capabilities(target) if source is not None else None

    def active_transactions(target):
        return [
            session
            for session in runtime.sessions.latest
            if session.active
            and (
                session.scope == target
                if isinstance(target, ConnectorId)
                else session.evse_id == target
            )
        ]

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
            if sample is None and isinstance(target, ConnectorId):
                sample = state.observation(
                    Channel(target.evse, Quantity(f"voltage_{phase.value}"))
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
        transactions = active_transactions(target)
        return ControlInputs(
            caps,
            VoltageObservation(
                target, state.token.connection_generation, tuple(phases)
            ),
            eligible,
            tuple(source.limits(target)),
            source.current_mode(target),
            actively_charging=actively_charging(state, target),
            transaction_id=transactions[0].external_transaction_id
            if len(transactions) == 1
            else None,
        )

    def adapter(target):
        session = server.sessions.get(target.station)
        live = session.adapter if session is not None else None
        if not isinstance(live, Adapter) or not runtime.current(live.token):
            return None
        bound = live.bind_control(
            target,
            lambda: capabilities(target),
            phase_operation_evidence=(
                source.phase_operation_evidence
                if source is not None
                else lambda snapshot, mode: None
            ),
        )

        bound.permission_evidence = lambda: (
            source.permission_evidence(target)
            if hasattr(source, "permission_evidence")
            else None
        )
        return bound

    def blocker(target):
        if adapter(target) is None:
            return "adapter_unavailable"
        active = active_transactions(target)
        return None if len(active) == 1 else "transaction_unavailable"

    def authority_adapter(station):
        session = server.sessions.get(station)
        live = session.adapter if session is not None else None
        return (
            live.bind_authority()
            if isinstance(live, Adapter) and runtime.current(live.token)
            else None
        )

    control = ControlRuntime(
        runtime,
        inputs,
        adapter,
        blocker,
        electrical_capabilities=getattr(source, "resolved", lambda target: ()),
        authority_adapter=authority_adapter,
    )
    control.capability_source = source
    return control
