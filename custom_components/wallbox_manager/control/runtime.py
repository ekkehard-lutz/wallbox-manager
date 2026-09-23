"""Manual charging orchestration; no HA or protocol types and no automatic retry."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from fractions import Fraction

from ..core.capabilities import CapabilitySnapshot, CurrentLimit, EvidenceState
from ..core.models import ConnectorId, EvseId, PhaseMode, VoltageObservation
from ..core.values import scalar
from ..solver.operating_point import SolverResult
from ..solver.power import solve
from .commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
    ControlArea,
    apply_charging_permission,
    apply_operating_point,
    permission_available,
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
    actively_charging: bool = False


@dataclass
class ManualIntent:
    request: PowerRequest = PowerRequest(0, Direction.NEAREST, False)
    phase_switch_deviation_pct: Fraction = Fraction(5)
    current_limits: dict[int, Fraction] = field(default_factory=dict)
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

    def __init__(
        self,
        runtime,
        inputs,
        adapter,
        blocker=lambda target: None,
        *,
        electrical_capabilities=lambda target: (),
    ):
        self.runtime = runtime
        self.inputs = inputs
        self.adapter = adapter
        self.blocker = blocker
        self._edited = {}
        self.intents: dict[EvseId | ConnectorId, ManualIntent] = {}
        self._listeners = set()
        self._closed = False
        self._inactive = set()
        self.electrical_capabilities = electrical_capabilities
        self._unsubscribe_defaults = runtime.subscribe(self._initialize_defaults)
        for snapshot in runtime.stations:
            self._initialize_defaults(snapshot)

    def _initialize_defaults(self, snapshot):
        if self._closed or not snapshot.connected:
            return
        for target in snapshot.connectors:
            self.initialize_current_limits(target)

    def initialize_current_limits(self, target):
        """Fill absent intent only. No dispatch and no capability mutation.

        Restore may replace an initial default when HA loads saved state later.
        Defaults never mark fields as explicitly edited; live edits still fence
        both initialization and late restoration. This synchronous read/write has
        no await at which a user/controller edit could race it.
        """
        if self._closed or not isinstance(target, ConnectorId):
            return
        maxima = {
            c.key: c.value
            for c in self.electrical_capabilities(target)
            if c.scope == target
            and c.evidence.state == EvidenceState.VERIFIED
            and c.value is not None
            and c.key
            in (
                "maximum_current",
                "maximum_current_1",
                "maximum_current_2",
                "maximum_current_3",
            )
        }
        generic = maxima.get("maximum_current")
        highest = max(
            (v for k, v in maxima.items() if k != "maximum_current"), default=None
        )
        intent = self.intent(target)
        changes = {}
        for count in (1, 2, 3):
            if count in intent.current_limits:
                continue
            value = maxima.get(
                f"maximum_current_{count}", generic if generic is not None else highest
            )
            if value is not None:
                changes[f"allowed_current_{count}p"] = value
        if changes:
            self._edit(target, changes)
            self.publish(target)

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
        self._unsubscribe_defaults()
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
        limits = dict(intent.current_limits)
        for count in (1, 2, 3):
            key = f"allowed_current_{count}p"
            if key in changes:
                limits[count] = scalar(changes.pop(key))
        request = replace(intent.request, **changes)
        intent.current_limits = limits
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
        if intent.request.allowed and intent.request.target_w == 0:
            from ..solver.operating_point import Reason, ResultStatus

            return (
                inputs,
                SolverResult(ResultStatus.UNREACHABLE, Reason.STOP_UNVERIFIED),
                "zero_target_unsupported",
            )
        result = solve(
            intent.request,
            caps,
            inputs.voltage,
            now=datetime.now(UTC),
            eligible_modes=inputs.eligible_modes,
            limits=inputs.limits
            + tuple(
                CurrentLimit(
                    e.mode,
                    0,
                    intent.current_limits[e.mode.count],
                    "desired_control_limit",
                )
                for e in caps.envelopes
                if e.mode.count in intent.current_limits
            ),
            current_mode=inputs.current_mode,
            actively_charging=inputs.actively_charging
            and intent.request.allowed
            and target not in self._inactive,
            phase_switch_deviation_pct=intent.phase_switch_deviation_pct,
        )
        return (
            inputs,
            result,
            self.blocker(target) if result.point is not None else result.reason.value,
        )

    def attributes(self, target):
        intent = self.intent(target)
        inputs, _, blocked = self.resolve(target)
        return {
            "physical_phase_mode": [p.value for p in inputs.current_mode.phases]
            if inputs and inputs.current_mode
            else None,
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
        was_allowed = self.intent(target).request.allowed
        intent = self._edit(target, changes)
        if (
            not was_allowed
            or not intent.request.allowed
            or intent.request.target_w == 0
        ):
            self._inactive.add(target)
        self._edited.setdefault(target, set()).update(changes)
        generation = intent.generation
        if "allowed" in changes and not intent.request.allowed:
            return await self._permission(target, intent, generation, False)
        if not intent.request.allowed:
            intent.status = "charging_disabled"
            self.publish(target)
            return None
        inputs, resolved, blocked = self.resolve(target)
        intent.solver_result = resolved
        if (
            inputs is not None
            and "allowed" in changes
            and not permission_available(self.adapter(target))
        ):
            result = CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.UNSUPPORTED_OPERATION,
            )
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
            return result
        adapter = self.adapter(target) if blocked is None else None
        if blocked is not None or adapter is None:
            intent.status = blocked or "adapter_unavailable"
            self.publish(target)
            return None
        token = self.runtime.get(target.station).token

        def current(*, after_dispatch=False):
            if (
                self._closed
                or generation != intent.generation
                or not self.runtime.current(token)
            ):
                return False
            fresh, result, reason = self.resolve(target)
            if after_dispatch and fresh is not None:
                # Relay feedback is expected to change during an accepted phase
                # transition. It must not relabel that acceptance as rejection.
                # Keep desired/generation, electrical evidence and limit fences.
                fresh = replace(
                    fresh,
                    current_mode=inputs.current_mode,
                    actively_charging=inputs.actively_charging,
                    eligible_modes=inputs.eligible_modes,
                )
                return fresh == inputs and (
                    not resolved.point.charging
                    or fresh.voltage.active_voltages(
                        resolved.point.mode, datetime.now(UTC)
                    )
                    == resolved.point.phase_voltages_v
                )
            return reason is None and fresh == inputs and result.point == resolved.point

        intent.status = "pending"
        self.publish(target)
        result = await apply_operating_point(
            adapter, resolved.point, is_current=current
        )
        if (
            result.status == CommandStatus.APPLIED
            and "allowed" in changes
            and intent.request.allowed
        ):
            result = await self._permission(
                target,
                intent,
                generation,
                True,
                publish=False,
                fence=lambda: current(after_dispatch=True),
            )
        if generation != intent.generation or (
            result.status == CommandStatus.APPLIED and not current(after_dispatch=True)
        ):
            result = stale_command_result()
        if generation == intent.generation:
            if result.status == CommandStatus.APPLIED and resolved.point.charging:
                self._inactive.discard(target)
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
        return result

    async def _permission(
        self, target, intent, generation, enabled, *, publish=True, fence=lambda: True
    ):
        state = self.runtime.get(target.station)
        adapter = self.adapter(target)
        if self._closed or state is None or not state.connected or adapter is None:
            intent.status = "adapter_unavailable"
            self.publish(target)
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.UNSUPPORTED_OPERATION,
            )
        token = state.token

        def current():
            return (
                not self._closed
                and intent.generation == generation
                and self.runtime.current(token)
                and fence()
            )

        intent.status = "pending"
        self.publish(target)
        result = await apply_charging_permission(adapter, enabled, is_current=current)
        if not current():
            result = stale_command_result()
        if publish and generation == intent.generation:
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
        return result
