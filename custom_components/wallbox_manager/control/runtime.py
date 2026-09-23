"""Manual charging orchestration; no HA or protocol types and no automatic retry."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from fractions import Fraction

from ..core.authority import ControlAuthority
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
    take_control,
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
    transaction_id: str | None = None


@dataclass(frozen=True)
class PowerSettings:
    target_w: Fraction = Fraction(0)
    direction: Direction = Direction.NEAREST

    def __post_init__(self):
        object.__setattr__(self, "target_w", scalar(self.target_w))
        if not isinstance(self.direction, Direction):
            raise ValueError("invalid direction")


@dataclass
class ManualIntent:
    request: PowerSettings = PowerSettings()
    phase_switch_deviation_pct: Fraction = Fraction(5)
    current_limits: dict[int, Fraction] = field(default_factory=dict)
    generation: int = 0
    status: str = "idle"
    solver_result: SolverResult | None = None
    command_result: CommandResult | None = None


class ControlRuntime:
    """Providers supply fresh execution inputs and a protocol-neutral adapter.

    Only explicit control actions execute. Observations update diagnostics,
    never desired values or dispatch. New edits fence queued work; late results
    cannot publish over newer intent. There is no automatic resume, retry or
    restoration dispatch.
    """

    def __init__(
        self,
        runtime,
        inputs,
        adapter,
        blocker=lambda target: None,
        *,
        electrical_capabilities=lambda target: (),
        authority_adapter=lambda station: None,
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
        self.authority_adapter = authority_adapter
        self._takeover_generations = {}
        self.takeover_results = {}
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
            k: v
            for k, v in changes.items()
            if k != "allowed" and k not in self._edited.get(target, set())
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
        if self.runtime.authority(target.station) != ControlAuthority.REMOTE:
            return None, None, "no_authority"
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
        if intent.request.target_w == 0 and caps.stop.state != EvidenceState.VERIFIED:
            from ..solver.operating_point import Reason, ResultStatus

            return (
                inputs,
                SolverResult(ResultStatus.UNREACHABLE, Reason.STOP_UNVERIFIED),
                "zero_current_unverified",
            )
        result = solve(
            PowerRequest(intent.request.target_w, intent.request.direction, True),
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
            and self.runtime.enabled(target) is True
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
            "actual_enabled": self.runtime.enabled(target),
            "control_authority": self.runtime.authority(target.station).value,
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
        # Compatibility for callers: permission is a transient command, not intent.
        enabled = changes.pop("allowed", None)
        if changes:
            intent = self._edit(target, changes)
            if intent.request.target_w == 0 or self.runtime.enabled(target) is not True:
                self._inactive.add(target)
            self._edited.setdefault(target, set()).update(changes)
        if enabled is not None:
            return await self.request_enabled(target, enabled)
        return await self.apply_stored(target)

    async def request_enabled(self, target, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        intent = self.intent(target)
        intent.generation += 1
        intent.command_result = intent.solver_result = None
        generation = intent.generation
        state = self.runtime.get(target.station)
        blocked = (
            "disconnected"
            if self._closed or state is None or not state.connected
            else "no_authority"
            if self.runtime.authority(target.station) != ControlAuthority.REMOTE
            else "enabled_unknown"
            if self.runtime.enabled(target) is None
            else "adapter_unavailable"
            if self.adapter(target) is None
            else None
        )
        if blocked:
            intent.status = blocked
            self.publish(target)
            return None
        if not permission_available(self.adapter(target)):
            result = CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.UNSUPPORTED_OPERATION,
            )
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
            return result
        prepared = {}
        if enabled:
            self._inactive.add(target)
            observation = self.runtime.enabled_observation(target)
            result = await self.apply_stored(target, prepare=True, prepared=prepared)
            if result is None or result.status != CommandStatus.APPLIED:
                return result
            if (
                intent.generation != generation
                or self.runtime.enabled(target) is not observation.enabled
                or self.runtime.enabled_observation(target).revision
                != observation.revision
            ):
                return stale_command_result()
        return await self._permission(
            target,
            intent,
            generation,
            enabled,
            fence=prepared.get("fence", lambda: True),
        )

    async def apply_stored(
        self, target, *, prepare=False, fence=lambda: True, prepared=None
    ):
        """Apply a target once; never change hardware permission."""
        intent = self.intent(target)
        generation = intent.generation
        state = self.runtime.get(target.station)
        actual = self.runtime.enabled(target)
        blocked = (
            "disconnected"
            if self._closed or state is None or not state.connected
            else "no_authority"
            if self.runtime.authority(target.station) != ControlAuthority.REMOTE
            else "enabled_unknown"
            if actual is None
            else "charging_disabled"
            if not actual and not prepare
            else "adapter_unavailable"
            if self.adapter(target) is None
            else None
        )
        if blocked:
            intent.status = blocked
            self.publish(target)
            return None
        enabled_revision = self.runtime.enabled_observation(target).revision
        inputs, resolved, blocked = self.resolve(target)
        intent.solver_result = resolved
        adapter = self.adapter(target) if blocked is None else None
        if blocked is not None or adapter is None:
            intent.status = blocked or "adapter_unavailable"
            self.publish(target)
            return None
        state = self.runtime.get(target.station)
        token = state.token
        authority_revision = state.authority_revision

        def current(*, after_dispatch=False, permission_confirmed=False):
            if (
                self._closed
                or not fence()
                or generation != intent.generation
                or (
                    not permission_confirmed
                    and (
                        self.runtime.enabled(target) is not actual
                        or self.runtime.enabled_observation(target).revision
                        != enabled_revision
                    )
                )
                or not self.runtime.current(token)
                or self.runtime.authority(target.station) != ControlAuthority.REMOTE
                or self.runtime.get(target.station).authority_revision
                != authority_revision
            ):
                return False
            fresh, result, reason = self.resolve(target)
            if fresh is None:
                return False
            if resolved.point.charging:
                if (
                    fresh.voltage.active_voltages(
                        resolved.point.mode, datetime.now(UTC)
                    )
                    != resolved.point.phase_voltages_v
                ):
                    return False
            # Compare required electrical values, not sample timestamps.
            fresh = replace(fresh, voltage=inputs.voltage)
            if after_dispatch or not resolved.point.charging:
                fresh = replace(
                    fresh,
                    current_mode=inputs.current_mode,
                    actively_charging=inputs.actively_charging,
                    eligible_modes=inputs.eligible_modes,
                )
                return fresh == inputs and self.blocker(target) is None
            return reason is None and fresh == inputs and result.point == resolved.point

        if prepared is not None:

            def prepared_fence():
                return current(after_dispatch=True)

            prepared_fence.after_dispatch = lambda: current(
                after_dispatch=True, permission_confirmed=True
            )
            prepared["fence"] = prepared_fence
        intent.status = "pending"
        self.publish(target)
        result = await apply_operating_point(
            adapter, resolved.point, is_current=current
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
        authority_revision = state.authority_revision

        observation = self.runtime.enabled_observation(target)

        def current(*, after_dispatch=False):
            return (
                not self._closed
                and intent.generation == generation
                and self.runtime.current(token)
                and self.runtime.authority(target.station) == ControlAuthority.REMOTE
                and self.runtime.get(target.station).authority_revision
                == authority_revision
                and (
                    after_dispatch
                    or (
                        self.runtime.enabled(target) is not None
                        and observation is not None
                        and self.runtime.enabled_observation(target).revision
                        == observation.revision
                    )
                )
                and (
                    getattr(fence, "after_dispatch", fence)()
                    if after_dispatch
                    else fence()
                )
            )

        current.after_dispatch = lambda: current(after_dispatch=True)
        intent.status = "pending"
        self.publish(target)
        result = await apply_charging_permission(adapter, enabled, is_current=current)
        if result.status != CommandStatus.APPLIED and self.runtime.current(token):
            await adapter.read_enabled()
        if not current(after_dispatch=True):
            result = stale_command_result()
        if publish and generation == intent.generation:
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
        return result

    async def take_control(self, station):
        """Explicit acquisition, followed by one fenced application of saved intent.

        Ordinary edits never call this. Future profile selection can intentionally
        reuse it. Concurrent edits/new presses invalidate the pending synchronization.
        """
        state = self.runtime.get(station)
        adapter = self.authority_adapter(station)
        if self._closed or state is None or not state.connected or adapter is None:
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.AUTHORITY,
                CommandReason.UNSUPPORTED_OPERATION,
            )
        generation = self._takeover_generations.get(station, 0) + 1
        self._takeover_generations[station] = generation
        token = state.token
        targets = state.connectors
        intents = {target: self.intent(target).generation for target in targets}

        def current():
            return (
                not self._closed
                and self.runtime.current(token)
                and self._takeover_generations.get(station) == generation
                and self.runtime.get(station).connectors == targets
                and all(self.intent(t).generation == g for t, g in intents.items())
            )

        result = await take_control(adapter, is_current=current)
        if not current():
            result = stale_command_result()
        if generation == self._takeover_generations.get(station):
            self.takeover_results[station] = result
        if (
            result.status == CommandStatus.APPLIED
            and self.runtime.authority(station) == ControlAuthority.REMOTE
        ):
            for target in targets:
                if not current():
                    break
                # Starting after acquisition cannot retain a relay parked by Local.
                self._inactive.add(target)
                bound = self.adapter(target)
                if bound is None:
                    continue
                await bound.read_enabled()
                if current():
                    await self.apply_stored(target, fence=current)
        return result
