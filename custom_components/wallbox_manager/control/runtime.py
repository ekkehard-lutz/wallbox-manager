"""Manual charging orchestration; no HA or protocol types and no automatic retry."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from fractions import Fraction

from ..core.capabilities import CapabilitySnapshot, CurrentLimit
from ..core.models import EvseId, PhaseMode, VoltageObservation
from ..core.values import scalar
from ..solver.operating_point import SolverResult
from ..solver.power import solve
from .commands import (
    CommandResult,
    CommandStatus,
    apply_operating_point,
    stale_command_result,
)
from .requests import Direction, PowerRequest


@dataclass(frozen=True)
class ControlInputs:
    """One read of existing normalized contracts, not a capability registry."""

    capabilities: CapabilitySnapshot
    voltage: VoltageObservation
    eligible_modes: tuple[PhaseMode, ...]
    limits: tuple[CurrentLimit, ...] = ()
    current_mode: PhaseMode | None = None


@dataclass
class ManualIntent:
    request: PowerRequest = PowerRequest(0, Direction.NEAREST, False)
    phase_switch_deviation_pct: Fraction = Fraction(5)
    generation: int = 0
    status: str = "idle"
    solver_result: SolverResult | None = None
    command_result: CommandResult | None = None


class ControlRuntime:
    """Providers supply fresh execution inputs and a protocol-neutral adapter.

    Only explicit edits execute. Observations update diagnostics, never desired
    values or dispatch. New edits fence queued work; late results cannot publish
    over newer intent. There is no auto-resume, retry or state-restoration dispatch.
    """

    def __init__(self, runtime, inputs, adapter, blocker=lambda target: None):
        self.runtime = runtime
        self.inputs = inputs
        self.adapter = adapter
        self.blocker = blocker
        self._edited = {}
        self.intents: dict[EvseId, ManualIntent] = {}
        self._listeners = set()
        self._closed = False

    def intent(self, target):
        return self.intents.setdefault(target, ManualIntent())

    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def publish(self, target):
        for listener in tuple(self._listeners):
            listener(target)

    def close(self):
        self._closed = True
        for intent in self.intents.values():
            intent.generation += 1

    def restore(self, target, **changes):
        """Restore only desired fields; never enqueue execution."""
        changes = {
            k: v for k, v in changes.items() if k not in self._edited.get(target, set())
        }
        if changes:
            self._edit(target, changes)
            self.publish(target)

    def _edit(self, target, changes):
        intent = self.intent(target)
        changes = dict(changes)
        tolerance = scalar(
            changes.pop("phase_switch_deviation_pct", intent.phase_switch_deviation_pct)
        )
        if tolerance > 25:
            raise ValueError(
                "phase retention tolerance must be between 0 and 25 percent"
            )
        request = replace(intent.request, **changes)
        intent.request = request
        intent.phase_switch_deviation_pct = tolerance
        intent.generation += 1
        intent.solver_result = intent.command_result = None
        intent.status = "idle"
        return intent

    def resolve(self, target):
        state = self.runtime.get(target.station)
        if self._closed or state is None or not state.connected:
            return None, None, "disconnected"
        inputs = self.inputs(target)
        if inputs is None:
            return None, None, "capabilities_unavailable"
        caps = inputs.capabilities
        if (
            caps.scope != target
            or caps.connection_generation != state.token.connection_generation
            or caps.boot_generation != state.token.boot_generation
            or caps.firmware != state.identity.firmware
        ):
            return None, None, "capabilities_unavailable"
        intent = self.intent(target)
        result = solve(
            intent.request,
            caps,
            inputs.voltage,
            now=datetime.now(UTC),
            eligible_modes=inputs.eligible_modes,
            limits=inputs.limits,
            current_mode=inputs.current_mode,
            phase_switch_deviation_pct=intent.phase_switch_deviation_pct,
        )
        return (
            inputs,
            result,
            self.blocker(target) if result.point is not None else result.reason.value,
        )

    def attributes(self, target):
        intent = self.intent(target)
        _, _, blocked = self.resolve(target)
        return {
            "execution_ready": blocked is None and self.adapter(target) is not None,
            "execution_blocked_reason": blocked
            or ("adapter_unavailable" if self.adapter(target) is None else None),
            "control_status": intent.status,
            "solver_reason": intent.solver_result.reason.value
            if intent.solver_result
            else None,
            "command_status": intent.command_result.status.value
            if intent.command_result
            else None,
            "command_reason": intent.command_result.reason.value
            if intent.command_result and intent.command_result.reason
            else None,
        }

    async def change(self, target, **changes):
        intent = self._edit(target, changes)
        self._edited.setdefault(target, set()).update(changes)
        generation = intent.generation
        inputs, resolved, blocked = self.resolve(target)
        intent.solver_result = resolved
        adapter = self.adapter(target) if blocked is None else None
        if blocked is not None or adapter is None:
            intent.status = blocked or "adapter_unavailable"
            self.publish(target)
            return None
        token = self.runtime.get(target.station).token

        def current():
            if (
                self._closed
                or generation != intent.generation
                or not self.runtime.current(token)
            ):
                return False
            fresh, result, reason = self.resolve(target)
            return reason is None and fresh == inputs and result.point == resolved.point

        intent.status = "pending"
        self.publish(target)
        result = await apply_operating_point(
            adapter, resolved.point, is_current=current
        )
        if generation != intent.generation or (
            result.status == CommandStatus.APPLIED and not current()
        ):
            result = stale_command_result()
        if generation == intent.generation:
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
        return result
