"""Pure, exact operating-point selection; no transport, policy or I/O."""

from collections.abc import Iterable
from datetime import datetime
from fractions import Fraction
from math import ceil, floor

from ..control.requests import Direction, PowerRequest
from ..core.capabilities import CapabilitySnapshot, CurrentLimit, EvidenceState
from ..core.models import PhaseMode, VoltageObservation
from ..core.values import scalar, timestamp
from .operating_point import OperatingPoint, Reason, ResultStatus, SolverResult


def solve(
    request: PowerRequest,
    capabilities: CapabilitySnapshot,
    voltage: VoltageObservation,
    *,
    now: datetime,
    eligible_modes: Iterable[PhaseMode],
    current_mode: PhaseMode | None = None,
    actively_charging: bool = False,
    limits: Iterable[CurrentLimit] = (),
    phase_switch_deviation_pct: Fraction = Fraction(0),
    charging_only: bool = False,
) -> SolverResult:
    """Select among verified modes within all supplied hard current intervals.

    Eligibility is supplied by the caller; it is not evidence of physical support.
    Voltage must match capability scope/generation exactly. Adapters explicitly
    normalize broader/narrower readings to that scope after checking applicability.
    Caller supplies fresh installation/station/session limits on every solve.
    No measured EV consumption is inspected and no acceptance limit is inferred.
    Optional current-mode retention applies only after direction/hard constraints.
    Existing callers default to 0%; the manual runtime explicitly supplies 5%.

    Each mode's linear power grid needs only its endpoints and the two indices
    bracketing the target. This is equivalent to full enumeration without memory
    or runtime proportional to the number of current steps.
    """
    timestamp(now)
    tolerance = scalar(phase_switch_deviation_pct)
    if tolerance > 25:
        raise ValueError("phase retention tolerance must be between 0 and 25 percent")
    if type(actively_charging) is not bool:
        raise ValueError("active charging state must be boolean")
    eligible = frozenset(eligible_modes)
    if any(not isinstance(mode, PhaseMode) for mode in eligible):
        raise ValueError("invalid eligible phase mode")
    if current_mode is not None and not isinstance(current_mode, PhaseMode):
        raise ValueError("invalid current mode")
    if not actively_charging or not request.allowed:
        current_mode = None
    limits = tuple(limits)
    if any(not isinstance(limit, CurrentLimit) for limit in limits):
        raise ValueError("invalid current limit")
    can_stop = capabilities.stop.state == EvidenceState.VERIFIED
    off = OperatingPoint.off()
    if not request.allowed:
        if can_stop:
            return SolverResult(ResultStatus.OFF, Reason.CHARGING_PROHIBITED, off)
        return SolverResult(ResultStatus.UNREACHABLE, Reason.STOP_UNVERIFIED)
    if request.target_w == 0 and can_stop:
        return SolverResult(ResultStatus.OFF, Reason.OFF_SELECTED, off)
    if (
        voltage.scope != capabilities.scope
        or voltage.connection_generation != capabilities.connection_generation
    ):
        return SolverResult(ResultStatus.UNREACHABLE, Reason.OBSERVATION_MISMATCH)

    # A blocked phase transition may require a positive-current substitute.
    # Explicit zero requests above still retain immediate stop semantics.
    candidates = [off] if can_stop and not charging_only else []
    missing_voltage = False
    has_mode = False
    for envelope in capabilities.envelopes:
        if (
            envelope.mode not in eligible
            or envelope.evidence.state != EvidenceState.VERIFIED
        ):
            continue
        has_mode = True
        volts = voltage.active_voltages(envelope.mode, now)
        if volts is None:
            missing_voltage = True
            continue
        lower, upper = envelope.min_current_a, envelope.max_current_a
        for limit in limits:
            if limit.mode == envelope.mode:
                lower = max(lower, limit.min_current_a)
                upper = min(upper, limit.max_current_a)
        origin, step = envelope.min_current_a, envelope.current_step_a
        first = max(0, ceil((lower - origin) / step))
        last = floor((upper - origin) / step)
        if first > last:
            continue
        voltage_sum = sum(volts, Fraction(0))
        target_index = (request.target_w / voltage_sum - origin) / step
        for index in {first, last, floor(target_index), ceil(target_index)}:
            if first <= index <= last:
                current = origin + index * step
                candidates.append(
                    OperatingPoint(
                        True, envelope.mode, current, current * voltage_sum, volts
                    )
                )

    # Missing voltage excludes only modes requiring that phase. Never infer a
    # voltage or let an unavailable multi-phase mode suppress a valid 1p point.
    if missing_voltage and not any(point.charging for point in candidates):
        return SolverResult(ResultStatus.UNREACHABLE, Reason.VOLTAGE_UNAVAILABLE)
    if not candidates:
        return SolverResult(
            ResultStatus.UNREACHABLE,
            Reason.ELECTRICAL_LIMIT if has_mode else Reason.NO_ELIGIBLE_MODE,
        )

    def tie_key(point: OperatingPoint) -> tuple:
        prefer_current = (
            current_mode is not None
            and current_mode in eligible
            and point.mode == current_mode
        )
        return (
            not prefer_current,
            point.offered_power_w,
            point.mode.count if point.mode else 0,
            point.mode.phases if point.mode else (),
            point.current_a or Fraction(0),
        )

    def nearest_key(point: OperatingPoint) -> tuple:
        return (abs(point.offered_power_w - request.target_w), *tie_key(point))

    minimum = min(p.offered_power_w for p in candidates)
    maximum = max(p.offered_power_w for p in candidates)
    directional = [
        point
        for point in candidates
        if request.direction == Direction.NEAREST
        or (
            request.direction == Direction.DOWN
            and point.offered_power_w <= request.target_w
        )
        or (
            request.direction == Direction.UP
            and point.offered_power_w >= request.target_w
        )
    ]
    if not directional:
        return SolverResult(
            ResultStatus.UNREACHABLE,
            Reason.DIRECTION_UNREACHABLE,
            min_power_w=minimum,
            max_power_w=maximum,
            suggested=min(candidates, key=nearest_key),
        )
    chosen = min(directional, key=nearest_key)
    if request.target_w > 0 and current_mode is not None:
        retained = [p for p in directional if p.charging and p.mode == current_mode]
        if retained:
            candidate = min(retained, key=nearest_key)
            if abs(candidate.offered_power_w - request.target_w) * 100 <= (
                request.target_w * tolerance
            ):
                chosen = candidate
    return SolverResult(
        ResultStatus.FEASIBLE if chosen.charging else ResultStatus.OFF,
        Reason.SELECTED if chosen.charging else Reason.OFF_SELECTED,
        chosen,
        minimum,
        maximum,
    )
