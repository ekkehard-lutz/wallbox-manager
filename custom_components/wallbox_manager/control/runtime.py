"""Manual charging orchestration; no HA or protocol types and no automatic retry."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from fractions import Fraction

from ..core.authority import ControlAuthority
from ..core.capabilities import CapabilitySnapshot, CurrentLimit, EvidenceState
from ..core.models import ConnectorId, EvseId, PhaseMode, VoltageObservation
from ..core.values import scalar
from ..diagnostics import (
    command_event,
    diagnostic_recovery,
    profile_name,
    recovery_record,
    recovery_snapshot,
)
from ..solver.operating_point import SolverResult
from ..solver.power import maximum_power, solve
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
    """One coherent read of normalized contracts, not a capability registry.

    Voltage and activity are regulation samples. Capabilities, hard limits and
    transaction identity are live command validity. Current/eligible phase proof
    gates new dispatch; expected feedback changes do not redefine a sent command.
    """

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
    profile_modes: tuple[int, ...] | None = None
    phase_retry: bool = False
    # Latest policy observation, never a command retained for retry.
    energy_desired: SolverResult | None = None
    reachable_only: bool = False
    policy_pause: bool = False
    hard_max_w: Fraction | None = None
    status: str = "idle"
    fence_reason: str | None = None
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
        self._confirmed_points = {}
        self._confirmed_contexts = {}
        self.recovery_status = {}
        self.pending_points = {}
        self._point_locks = {}
        self._phase_restrictions = {}
        self._phase_probes = {}
        self._unconfirmed_targets = set()
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
        self._confirmed_points.clear()
        self._confirmed_contexts.clear()
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
        intent.policy_pause = False
        intent.phase_switch_deviation_pct = tolerance
        intent.generation += 1
        intent.solver_result = intent.command_result = None
        intent.status = "idle"
        return intent

    def phase_restricted(self, target):
        """Specific station rejection, scoped to live authority and physical mode.

        There is deliberately no expiry inferred from a generic retry interval.
        Changed physical feedback or a successful transition supplies new evidence.
        """
        restriction = self._phase_restrictions.get(target)
        if restriction is None:
            return False
        state, inputs = self.runtime.get(target.station), self.inputs(target)
        token, authority, mode = restriction
        if (
            state is None
            or state.token != token
            or state.authority_revision != authority
            or (
                inputs
                and inputs.current_mode is not None
                and inputs.current_mode != mode
            )
        ):
            self._phase_restrictions.pop(target, None)
            return False
        return True

    @contextmanager
    def phase_probe(self, target, *, enabled=False):
        """Permit one task's bounded retry, never declare the restriction expired.

        Ordinary planners/observers still see the restriction. Only an accepted
        different-mode command clears it; rejection retains it and uses fallback.
        """
        previous = self._phase_probes.get(target)
        if enabled:
            self._phase_probes[target] = asyncio.current_task()
        try:
            yield
        finally:
            if enabled:
                if previous is None:
                    self._phase_probes.pop(target, None)
                else:
                    self._phase_probes[target] = previous

    def _eligible_modes(
        self,
        target,
        inputs,
        *,
        reachable=False,
        dispatch_modes=None,
        energy_desired=False,
    ):
        modes = inputs.eligible_modes if dispatch_modes is None else dispatch_modes
        intent = self.intent(target)
        if reachable or intent.reachable_only:
            if inputs.current_mode is None and dispatch_modes is None:
                return ()
            if (
                not energy_desired
                and self.phase_restricted(target)
                and (
                    self._phase_probes.get(target) is None
                    or self._phase_probes[target] is not asyncio.current_task()
                )
            ):
                modes = tuple(m for m in modes if m == inputs.current_mode)
        return tuple(
            m
            for m in modes
            if energy_desired
            or intent.profile_modes is None
            or m.count in intent.profile_modes
        )

    def _current_limits(self, target, inputs):
        intent = self.intent(target)
        return inputs.limits + tuple(
            CurrentLimit(
                e.mode, 0, intent.current_limits[e.mode.count], "desired_control_limit"
            )
            for e in inputs.capabilities.envelopes
            if e.mode.count in intent.current_limits
        )

    def minimum_positive(self, target, *, dispatch_modes=None, energy_desired=False):
        """Resolve an actual reachable positive point through the common grid.

        Disabled permission may prepare a point for an explicitly authorized ON;
        the existing command lifecycle still owns permission and dispatch fences.
        Unknown phase/voltage/authorization never manufactures a positive floor.
        energy_desired excludes only learned temporary lockout from this query;
        the returned preference still needs executable resolution before dispatch.
        """
        if not self.profile_permitted(target) or self.runtime.enabled(target) is None:
            return None
        if self.blocker(target) is not None:
            return None
        _, result, _ = self.resolve(
            target,
            request=PowerSettings(0, Direction.DOWN),
            dispatch_modes=dispatch_modes,
            minimum_positive=True,
            energy_desired=energy_desired,
        )
        return result

    def resolve(
        self,
        target,
        *,
        substitute_mode=None,
        request=None,
        dispatch_modes=None,
        minimum_positive=False,
        reachable=False,
        energy_desired=False,
        hard_max_w=None,
    ):
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
        request = request or intent.request
        if hasattr(self, "profiles") and self.profiles.common_pv(target):
            if intent.hard_max_w is not None:
                hard_max_w = (
                    intent.hard_max_w
                    if hard_max_w is None
                    else min(hard_max_w, intent.hard_max_w)
                )
        if (
            request.target_w == 0
            and caps.stop.state != EvidenceState.VERIFIED
            and not minimum_positive
        ):
            from ..solver.operating_point import Reason, ResultStatus

            return (
                inputs,
                SolverResult(ResultStatus.UNREACHABLE, Reason.STOP_UNVERIFIED),
                "zero_current_unverified",
            )
        # Only post-dispatch policy validation supplies the already verified
        # dispatched modes; all new writes use fresh phase-operation evidence.
        eligible_modes = self._eligible_modes(
            target,
            inputs,
            reachable=minimum_positive or reachable,
            energy_desired=energy_desired,
            dispatch_modes=dispatch_modes,
        )
        if (
            (intent.reachable_only or minimum_positive or reachable)
            and not eligible_modes
            and (request.target_w > 0 or minimum_positive)
        ):
            return inputs, None, "phase_reachability_unavailable"
        result = solve(
            PowerRequest(request.target_w, request.direction, True),
            caps,
            inputs.voltage,
            now=datetime.now(UTC),
            eligible_modes=eligible_modes
            if substitute_mode is None
            else tuple(m for m in eligible_modes if m == substitute_mode),
            charging_only=substitute_mode is not None,
            minimum_positive=minimum_positive,
            limits=self._current_limits(target, inputs),
            current_mode=inputs.current_mode,
            actively_charging=inputs.actively_charging
            and self.runtime.enabled(target) is True
            and target not in self._inactive,
            phase_switch_deviation_pct=intent.phase_switch_deviation_pct,
            hard_max_w=hard_max_w,
        )
        return (
            inputs,
            result,
            self.blocker(target) if result.point is not None else result.reason.value,
        )

    def power_floor(self, target):
        return self.power_ceiling(target, minimum=True)

    def power_ceiling(self, target, *, minimum=False):
        """Known feasible ceiling, independent of the requested operating point.

        Unknown/stale evidence is not a station rating. Reuse the electrical
        solver's hard limits and current grid; never synthesize 230 V.
        """
        state = self.runtime.get(target.station)
        inputs = self.inputs(target) if state and state.connected else None
        if inputs is None:
            return None
        caps = inputs.capabilities
        now = datetime.now(UTC)
        if (
            caps.scope != target
            or caps.connection_generation != state.token.connection_generation
            or caps.boot_generation != state.token.boot_generation
            or caps.firmware != state.identity.firmware
        ):
            return None
        return maximum_power(
            caps,
            inputs.voltage,
            minimum=minimum,
            now=now,
            eligible_modes=inputs.eligible_modes,
            limits=inputs.limits
            + tuple(
                CurrentLimit(
                    e.mode,
                    0,
                    self.intent(target).current_limits[e.mode.count],
                    "desired_control_limit",
                )
                for e in caps.envelopes
                if e.mode.count in self.intent(target).current_limits
            ),
        )

    @staticmethod
    def setpoint_context(inputs):
        """Transaction and electrical contract under which a setpoint was confirmed."""
        return inputs.transaction_id, inputs.capabilities, inputs.limits

    def confirmed_point(self, target):
        """Read-only projection of an APPLIED result in its confirmed context.

        Intent edits do not change hardware. Pending/retryable commands retain
        the last confirmed point, separately from their unconfirmed target.
        Failed or superseded dispatch clears uncertain state.
        """
        record = self._confirmed_points.get(target)
        state = self.runtime.get(target.station)
        enabled = self.runtime.enabled_observation(target)
        if (
            not record
            or self._closed
            or not state
            or not state.connected
            or record[1] != state.token
            or record[2] != state.authority_revision
            or not enabled
            or record[3] != enabled.revision
            or self.runtime.enabled(target) is not True
            or self.runtime.authority(target.station) != ControlAuthority.REMOTE
            or not self.profile_permitted(target)
        ):
            return None
        return record[0]

    def attributes(self, target):
        intent = self.intent(target)
        if hasattr(self, "profiles") and target in self.profiles.debounce_tasks:
            inputs, blocked = self.inputs(target), "power_edit_pending"
        else:
            inputs, _, blocked = self.resolve(target)
        applied = self.confirmed_point(target)
        return {
            "electrical_recovery_status": self.recovery_status.get(target),
            "applied_current_a": float(applied.current_a or 0) if applied else None,
            "applied_phase_count": applied.mode.count
            if applied and applied.mode
            else None,
            "applied_offered_power_w": float(applied.offered_power_w)
            if applied
            else None,
            "actual_enabled": self.runtime.enabled(target),
            "control_authority": self.runtime.authority(target.station).value,
            "physical_phase_mode": [p.value for p in inputs.current_mode.phases]
            if inputs and inputs.current_mode
            else None,
            "physical_phase_valid_until": next(
                (
                    o.valid_until.isoformat()
                    for o in self.runtime.get(target.station).physical_phases
                    if o.scope == target
                ),
                None,
            )
            if self.runtime.get(target.station)
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
            self.intent(target).profile_modes = None
            intent = self._edit(target, changes)
            if intent.request.target_w == 0 or self.runtime.enabled(target) is not True:
                self._inactive.add(target)
            self._edited.setdefault(target, set()).update(changes)
        if enabled is not None:
            return await self.request_enabled(target, enabled)
        return await self.apply_stored(target)

    def profile_permitted(self, target):
        return not hasattr(self, "ownership") or self.ownership.permits(self, target)

    def preparation_wait(self, target, reason):
        """A missing preparation proof is a retryable outcome, never a lost ON."""
        result = CommandResult(
            CommandStatus.TEMPORARILY_REJECTED,
            ControlArea.OPERATING_POINT,
            CommandReason.TRANSACTION_UNAVAILABLE
            if reason == "transaction_unavailable"
            else CommandReason.BUSY,
            reason,
        )
        self.intent(target).command_result = result
        self.intent(target).status = reason
        self.publish(target)
        return result

    async def request_enabled(self, target, enabled, *, fence=lambda: True):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if enabled and not self.profile_permitted(target):
            self.intent(target).status = "inactive_wallbox"
            self.publish(target)
            return CommandResult(
                CommandStatus.TEMPORARILY_REJECTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.NO_AUTHORITY,
                "inactive_wallbox",
            )
        intent = self.intent(target)
        if not enabled:
            self._inactive.add(target)
            self._confirmed_points.pop(target, None)
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
            return self.preparation_wait(target, blocked)
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
            result = await self.apply_stored(
                target, prepare=True, prepared=prepared, fence=fence
            )
            if result is None or result.status != CommandStatus.APPLIED:
                return result
            if (
                intent.generation != generation
                or self.runtime.enabled(target) is not observation.enabled
                or self.runtime.enabled_observation(target).revision
                != observation.revision
            ):
                return stale_command_result()
        # Preparing the point and confirming ON are one regulation decision.
        # Keep its existing pending slot through permission I/O so sensor events
        # cannot cancel it between these two confirmed control operations.
        pending = self._confirmed_points.get(target, (None,))[0]
        if enabled:
            self.pending_points[target] = pending
        try:
            result = await self._permission(
                target,
                intent,
                generation,
                enabled,
                fence=prepared.get("fence", fence),
            )
        finally:
            if enabled and self.pending_points.get(target) is pending:
                self.pending_points.pop(target, None)
        if enabled and generation == intent.generation:
            record = self._confirmed_points.get(target)
            if result.status == CommandStatus.APPLIED and record:
                # The separately confirmed ON advances the permission revision.
                self._confirmed_points[target] = (
                    *record[:3],
                    self.runtime.enabled_observation(target).revision,
                )
            else:
                self._confirmed_points.pop(target, None)
            self.publish(target)
        return result

    @diagnostic_recovery
    async def reconcile_applied(self, target, *, fence):
        """Adopt an authoritative schedule readback in a freshly fenced context."""
        recovery_record("attempt", result="started")
        self.recovery_status[target] = "waiting_electrical_evidence"
        adapter = self.adapter(target)
        state = self.runtime.get(target.station)
        inputs = self.inputs(target)
        enabled = self.runtime.enabled_observation(target)
        generation = self.intent(target).generation
        if not state or not inputs or not enabled or not adapter or not fence():
            recovery_record(
                "rejected",
                reason=(
                    "runtime_unavailable"
                    if not state
                    else "capability_evidence_missing"
                    if not inputs
                    else "charging_enabled_unknown"
                    if not enabled
                    else "adapter_unavailable"
                    if not adapter
                    else "command_fence_stale"
                ),
            )
            return None

        recovery_snapshot(
            "phase_evidence",
            lambda: dict(
                observations=[
                    dict(
                        source=o.source,
                        phases=o.mode.count if o.mode else None,
                        state="expired"
                        if not o.fresh(datetime.now(UTC))
                        else "unknown"
                        if o.mode is None
                        else "valid",
                        valid_until=o.valid_until.isoformat(),
                    )
                    for o in state.physical_phases
                    if o.scope == target
                ],
                state="present"
                if any(o.scope == target for o in state.physical_phases)
                else "absent",
            ),
        )

        def current(*, refreshing_phase=False):
            fresh = self.runtime.get(target.station)
            observation = self.runtime.enabled_observation(target)
            fresh_inputs = self.inputs(target)
            if refreshing_phase and fresh_inputs:
                fresh_inputs = replace(
                    fresh_inputs,
                    current_mode=inputs.current_mode,
                    eligible_modes=inputs.eligible_modes,
                )
            reason = (
                "command_fence_stale"
                if not fence()
                else "runtime_closed"
                if self._closed
                else "profile_not_permitted"
                if not self.profile_permitted(target)
                else "connection_generation_changed"
                if not self.runtime.current(state.token)
                else "authority_changed"
                if fresh.authority_revision != state.authority_revision
                else "no_authority"
                if self.runtime.authority(target.station) != ControlAuthority.REMOTE
                else "charging_enabled_unknown"
                if not observation
                else "charging_enabled_changed"
                if observation.revision != enabled.revision
                else "generation_changed"
                if self.intent(target).generation != generation
                else "point_command_pending"
                if target in self.pending_points
                else "capability_evidence_missing"
                if fresh_inputs is None
                else "electrical_inputs_changed"
                if replace(fresh_inputs, voltage=inputs.voltage) != inputs
                else None
            )
            if reason:
                recovery_record("fence", reason=reason)
            return reason is None

        refresh = getattr(adapter, "read_physical_mode", None)
        if refresh:
            await refresh(is_current=lambda: current(refreshing_phase=True))
            if not current(refreshing_phase=True):
                recovery_record("rejected", reason="phase_readback_stale")
                self.recovery_status[target] = "phase_readback_stale"
                return None
            inputs = self.inputs(target)
        else:
            recovery_record(
                "phase_readback", attempted=False, reason="phase_readback_not_supported"
            )
        read = getattr(adapter, "read_operating_limit", None)
        if not read:
            recovery_record(
                "composite_schedule",
                attempted=False,
                reason="composite_schedule_not_supported",
            )
        result = await read(is_current=current) if read else None
        if result is None or not current():
            recovery_record("rejected", reason="readback_rejected_or_stale")
            self.recovery_status[target] = "readback_rejected_or_stale"
            return None
        self.recovery_status[target] = "readback_obtained"
        amps, phases = result
        inputs = self.inputs(target)
        mode = inputs.current_mode
        recovery_snapshot(
            "electrical_evidence",
            lambda: dict(
                physical_phases=mode.count if mode else None,
                schedule_phases=phases,
                current_a=float(amps),
                generation=generation,
                authority_revision=state.authority_revision,
                enabled_revision=enabled.revision,
                requested_power_w=float(self.intent(target).request.target_w),
                profile=profile_name(self.profiles, target)
                if hasattr(self, "profiles")
                else None,
                voltages=[
                    dict(
                        phase=v.phase.value,
                        voltage_v=float(v.voltage_v),
                        fresh=v.observed_at <= datetime.now(UTC) < v.valid_until,
                        valid_until=v.valid_until.isoformat(),
                    )
                    for v in inputs.voltage.phases
                ],
                phase_observations=[
                    dict(
                        source=o.source,
                        phases=o.mode.count if o.mode else None,
                        state="expired"
                        if not o.fresh(datetime.now(UTC))
                        else "unknown"
                        if o.mode is None
                        else "valid",
                    )
                    for o in self.runtime.get(target.station).physical_phases
                    if o.scope == target
                ],
                voltage_available=bool(inputs.voltage.phases),
                envelopes=[
                    dict(
                        phases=e.mode.count,
                        minimum_a=float(e.min_current_a),
                        maximum_a=float(e.max_current_a),
                        step_a=float(e.current_step_a),
                        evidence=e.evidence.state.value,
                    )
                    for e in inputs.capabilities.envelopes
                ],
                limits=[
                    dict(
                        phases=v.mode.count,
                        minimum_a=float(v.min_current_a),
                        maximum_a=float(v.max_current_a),
                    )
                    for v in inputs.limits
                ],
                desired_current_limits={
                    str(k): float(v)
                    for k, v in self.intent(target).current_limits.items()
                },
            ),
        )
        if amps:
            if mode is None or mode.count != phases:
                recovery_record(
                    "rejected",
                    reason="phase_evidence_unavailable"
                    if mode is None
                    else "schedule_phase_missing"
                    if phases is None
                    else "schedule_phase_mismatch",
                )
                self.recovery_status[target] = "physical_phase_unavailable_or_mismatch"
                return None
            volts = inputs.voltage.active_voltages(mode, datetime.now(UTC))
            if volts is None:
                recovery_snapshot(
                    "rejected",
                    lambda: dict(
                        reason="voltage_missing"
                        if any(
                            phase not in {v.phase for v in inputs.voltage.phases}
                            for phase in mode.phases
                        )
                        else "voltage_stale",
                    ),
                )
                self.recovery_status[target] = "voltage_unavailable"
                return None
            watts = amps * sum(volts)
        else:
            watts = Fraction(0)
        _, solved, blocked = self.resolve(
            target,
            request=PowerSettings(watts, Direction.NEAREST),
            substitute_mode=mode if amps else None,
        )
        if (
            blocked
            or not solved
            or not solved.point
            or (amps and solved.point.current_a != amps)
        ):
            recovery_record(
                "rejected",
                reason="electrical_resolution_rejected",
                blocked=blocked,
                solver_reason=solved.reason.value if solved else None,
            )
            self.recovery_status[target] = "electrical_resolution_rejected"
            return None
        self.recovery_status[target] = "point_validated"
        self._confirmed_points[target] = (
            solved.point,
            state.token,
            state.authority_revision,
            enabled.revision,
        )
        self._confirmed_contexts[target] = self.setpoint_context(inputs)
        self._unconfirmed_targets.discard(target)
        self.recovery_status[target] = "point_adopted"
        recovery_snapshot(
            "adoption",
            lambda: dict(
                result="point_adopted",
                phases=solved.point.mode.count if solved.point.mode else 0,
                current_a=float(solved.point.current_a or 0),
                voltages_v=[float(v) for v in solved.point.phase_voltages_v],
                power_w=float(solved.point.offered_power_w),
                charging_command_sent=False,
            ),
        )
        self.publish(target)
        return solved.point

    async def wait_for_pending_point(self, target):
        """Let a superseded operation drain before a regulator samples again."""
        lock = self._point_locks.get(target)
        if lock is not None:
            async with lock:
                pass

    async def apply_stored(self, target, **kwargs):
        """Serialize transitions through confirmation, including phase fallback."""
        generation = self.intent(target).generation
        lock = self._point_locks.setdefault(target, asyncio.Lock())
        async with lock:
            if generation != self.intent(target).generation:
                return stale_command_result()
            self.pending_points[target] = None
            try:
                return await self._apply_stored(target, **kwargs)
            except BaseException:
                self._confirmed_points.pop(target, None)
                raise
            finally:
                self.pending_points.pop(target, None)

    async def _apply_stored(
        self,
        target,
        *,
        prepare=False,
        fence=lambda: True,
        prepared=None,
        reuse_applied=False,
    ):
        """Apply a target once; never change hardware permission."""
        if not self.profile_permitted(target):
            self.intent(target).status = "inactive_wallbox"
            self.publish(target)
            return (
                self.preparation_wait(target, "inactive_wallbox") if prepare else None
            )
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
            return self.preparation_wait(target, intent.status) if prepare else None
        enabled_revision = self.runtime.enabled_observation(target).revision
        inputs, resolved, blocked = self.resolve(target)
        intent.solver_result = resolved
        adapter = self.adapter(target) if blocked is None else None
        if blocked is not None or adapter is None:
            intent.status = blocked or "adapter_unavailable"
            self.publish(target)
            return self.preparation_wait(target, intent.status) if prepare else None
        state = self.runtime.get(target.station)
        token = state.token
        authority_revision = state.authority_revision

        substitute_mode = None
        command_request = intent.request
        intent.phase_retry = intent.reachable_only and self.phase_restricted(target)
        intent.fence_reason = None

        def control_valid(*, after_dispatch=False, permission_confirmed=False):
            """Live lifecycle/safety proof, independent of the regulation sample."""

            def reject(reason):
                intent.fence_reason = reason
                return None

            if self._closed or generation != intent.generation:
                return reject("intent_changed")
            if not self.profile_permitted(target):
                return reject("profile_or_ownership_changed")
            if not self.runtime.current(token):
                return reject("connection_changed")
            if (
                self.runtime.authority(target.station) != ControlAuthority.REMOTE
                or self.runtime.get(target.station).authority_revision
                != authority_revision
            ):
                return reject("authority_changed")
            permission = self.runtime.enabled_observation(target)
            if not permission_confirmed and (
                self.runtime.enabled(target) is not actual
                or permission is None
                or permission.revision != enabled_revision
            ):
                return reject("permission_changed")
            caller = (
                getattr(fence, "after_dispatch", fence) if after_dispatch else fence
            )
            if not caller():
                return reject("caller_invalidated")
            fresh = self.inputs(target)
            if fresh is None or fresh.capabilities != inputs.capabilities:
                return reject("capabilities_changed")
            if fresh.limits != inputs.limits:
                return reject("electrical_limits_changed")
            if fresh.transaction_id != inputs.transaction_id:
                return reject("transaction_changed")
            if self.blocker(target) is not None:
                return reject("adapter_or_transaction_unavailable")
            return fresh

        probing_phase = self._phase_probes.get(target) is asyncio.current_task()

        def validate_current(*, after_dispatch=False, permission_confirmed=False):
            fresh = control_valid(
                after_dispatch=after_dispatch, permission_confirmed=permission_confirmed
            )
            if fresh is None:
                return False
            if after_dispatch:
                # The wire command is fixed. New PV/SoC/power/voltage samples,
                # expiry and ordinary activity/phase feedback belong to the next
                # cycle, never to retrospective solving of this command.
                return True
            # Queue/lock waits still require a fresh, representable snapshot and
            # current phase-operation proof immediately before a hardware write.
            _, result, reason = self.resolve(
                target, substitute_mode=substitute_mode, request=command_request
            )
            if (
                resolved.point.charging
                and fresh.voltage.active_voltages(
                    resolved.point.mode, datetime.now(UTC)
                )
                is None
            ):
                intent.fence_reason = "pre_dispatch_voltage_unavailable"
                return False
            if result is None or not resolved.point.same_setpoint(result.point):
                intent.fence_reason = "pre_dispatch_setpoint_changed"
                return False
            if hasattr(self, "profiles") and not self.profiles.permits_point(
                target, resolved.point, transition_mode=substitute_mode
            ):
                if intent.fence_reason != "pre_dispatch_desired_phase":
                    intent.fence_reason = "pre_dispatch_pv_policy"
                return False
            if resolved.point.charging and (
                reason is not None
                or fresh.current_mode != inputs.current_mode
                or fresh.eligible_modes != inputs.eligible_modes
            ):
                intent.fence_reason = "pre_dispatch_phase_changed"
                return False
            return True

        def current(*, after_dispatch=False, permission_confirmed=False):
            # Transport guards can run in the serialized sender task. Carry only
            # this command's probe permission across that boundary, not to normal
            # observation tasks that happen to run while its I/O is pending.
            with self.phase_probe(target, enabled=probing_phase):
                return validate_current(
                    after_dispatch=after_dispatch,
                    permission_confirmed=permission_confirmed,
                )

        current.after_dispatch = lambda: current(after_dispatch=True)

        if prepared is not None:

            def prepared_fence():
                return current(after_dispatch=True)

            prepared_fence.after_dispatch = lambda: current(
                after_dispatch=True, permission_confirmed=True
            )
            prepared["fence"] = prepared_fence
        prior_point = self.confirmed_point(target)
        retry_required = (
            intent.command_result is not None
            and intent.command_result.status != CommandStatus.APPLIED
        )
        if (
            reuse_applied
            and not retry_required
            and target not in self._unconfirmed_targets
            and self._confirmed_contexts.get(target) == self.setpoint_context(inputs)
            and resolved.point.same_setpoint(prior_point)
            and current()
        ):
            intent.command_result = CommandResult(CommandStatus.APPLIED)
            intent.status = "applied"
            command_event(
                self, target, "command_reused", operating_point=resolved.point
            )
            self.publish(target)
            return intent.command_result
        self.pending_points[target] = resolved.point
        intent.status = "pending"
        self.publish(target)
        command_event(self, target, "command_attempt", operating_point=resolved.point)
        result = await apply_operating_point(
            adapter, resolved.point, is_current=current
        )
        if (
            result.status == CommandStatus.TEMPORARILY_REJECTED
            and result.area == ControlArea.PHASE_MODE
            and result.reason == CommandReason.PHASE_SWITCH_LOCKOUT
            and resolved.point.charging
            and inputs.current_mode is not None
            and inputs.current_mode != resolved.point.mode
            and current()
        ):
            intent.phase_retry = True
            self._phase_restrictions[target] = (
                token,
                authority_revision,
                inputs.current_mode,
            )
            # One synchronous recalculation within this explicit command only.
            # Keep all original fences, including confirmed physical phase state.
            substitute_mode = inputs.current_mode
            if hasattr(self, "profiles") and self.profiles.shared_pv_execution(target):
                # The profile owns permission to exceed a raw energy budget at
                # its positive floor; the common runtime still chooses the point.
                intent.reachable_only = True
                power, direction, _, plan = self.profiles.pv_plan(
                    target,
                    advance=False,
                    transition_mode=inputs.current_mode,
                )
                if plan is not None and plan.point is not None and plan.point.charging:
                    command_request = PowerSettings(power, direction)
            _, substitute, blocked = self.resolve(
                target, substitute_mode=substitute_mode, request=command_request
            )
            if blocked is None and substitute.point is not None:
                resolved = substitute
                intent.solver_result = resolved
                if (
                    reuse_applied
                    and not retry_required
                    and target not in self._unconfirmed_targets
                    and self._confirmed_contexts.get(target)
                    == self.setpoint_context(inputs)
                    and resolved.point.same_setpoint(prior_point)
                    and current()
                ):
                    result = CommandResult(CommandStatus.APPLIED)
                else:
                    result = await apply_operating_point(
                        adapter, resolved.point, is_current=current
                    )
        # Record the actual invalidating context even when the adapter already
        # rejected an obsolete connection/authority before returning its result.
        still_current = current(after_dispatch=True)
        if generation != intent.generation or (
            result.status == CommandStatus.APPLIED and not still_current
        ):
            if generation != intent.generation:
                self._confirmed_points.pop(target, None)
            # The adapter accepted a write whose final fence no longer matches.
            # Retain historical confirmation, but require reconciliation even if
            # the next desired target equals that old point.
            self._unconfirmed_targets.add(target)
            result = stale_command_result()
        if result.status in (CommandStatus.FAILED, CommandStatus.UNSUPPORTED):
            self._confirmed_points.pop(target, None)
        command_event(
            self,
            target,
            "command_outcome",
            operating_point=resolved.point,
            result=result,
        )
        if generation == intent.generation:
            if result.status == CommandStatus.APPLIED:
                if (
                    resolved.point.charging
                    and resolved.point.mode != inputs.current_mode
                ):
                    self._phase_restrictions.pop(target, None)
                    intent.phase_retry = False
                self._unconfirmed_targets.discard(target)
                self._confirmed_contexts[target] = self.setpoint_context(inputs)
                self._confirmed_points[target] = (
                    resolved.point,
                    token,
                    authority_revision,
                    self.runtime.enabled_observation(target).revision,
                )
                if resolved.point.charging:
                    self._inactive.discard(target)
                else:
                    self._inactive.add(target)
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
                and (not enabled or self.profile_permitted(target))
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
        command_event(self, target, "command_attempt")
        result = await apply_charging_permission(adapter, enabled, is_current=current)
        if result.status != CommandStatus.APPLIED and self.runtime.current(token):
            await adapter.read_enabled()
        if not current(after_dispatch=True):
            result = stale_command_result()
        command_event(self, target, "command_outcome", result=result)
        if publish and generation == intent.generation:
            intent.command_result = result
            intent.status = result.status.value
            self.publish(target)
        return result

    async def take_control(self, station):
        """An explicit takeover participates in the installation ownership guard."""
        if hasattr(self, "ownership"):
            return await self.ownership.activate_station(self, station)
        return await self._take_control(station)

    async def _take_control(self, station):
        """Acquire authority, explicitly disable every connector and confirm OFF."""
        state = self.runtime.get(station)
        for target in tuple(self._confirmed_points):
            if target.station == station:
                self._confirmed_points.pop(target, None)
        adapter = self.authority_adapter(station)
        if (
            self._closed
            or state is None
            or not state.connected
            or adapter is None
            or not state.connectors
        ):
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
        if result.status == CommandStatus.APPLIED:
            for target in targets:
                bound = self.adapter(target)
                if bound is None:
                    result = CommandResult(
                        CommandStatus.UNSUPPORTED,
                        ControlArea.CHARGING_PERMISSION,
                        CommandReason.UNSUPPORTED_OPERATION,
                    )
                    break
                await bound.read_enabled()
                if not current():
                    result = stale_command_result()
                    break
                # Account for the generation advance owned by this OFF request.
                intents[target] += 1
                result = await self.request_enabled(target, False, fence=current)
                if (
                    not result
                    or result.status != CommandStatus.APPLIED
                    or self.runtime.enabled(target) is not False
                ):
                    result = result or CommandResult(
                        CommandStatus.FAILED,
                        ControlArea.CHARGING_PERMISSION,
                        CommandReason.HARDWARE_ERROR,
                    )
                    break
            if not current():
                result = stale_command_result()
        if generation == self._takeover_generations.get(station):
            self.takeover_results[station] = result
        return result
