"""Offered physical points and machine-readable solver outcomes."""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from ..core.models import PhaseMode
from ..core.values import scalar


@dataclass(frozen=True)
class OperatingPoint:
    """Balanced-current offer estimate with measured L-N volts and assumed PF=1.

    No measured EV consumption or protocol acknowledgement is implied. OFF has
    no current setpoint or active phases; its offered power alone is zero.
    """

    charging: bool
    mode: PhaseMode | None
    current_a: Fraction | None
    offered_power_w: Fraction
    phase_voltages_v: tuple[Fraction, ...] = ()

    def __post_init__(self) -> None:
        if type(self.charging) is not bool:
            raise ValueError("charging must be boolean")
        object.__setattr__(self, "offered_power_w", scalar(self.offered_power_w))
        volts = tuple(scalar(v, positive=True) for v in self.phase_voltages_v)
        object.__setattr__(self, "phase_voltages_v", volts)
        if self.charging:
            if not isinstance(self.mode, PhaseMode) or self.current_a is None:
                raise ValueError("charging requires a mode and current")
            object.__setattr__(self, "current_a", scalar(self.current_a, positive=True))
            if len(volts) != self.mode.count:
                raise ValueError("complete per-phase voltage basis required")
            if self.offered_power_w != self.current_a * sum(volts):
                raise ValueError("offer disagrees with voltage/current basis")
        elif (
            self.mode is not None
            or self.current_a is not None
            or self.offered_power_w != 0
            or volts
        ):
            raise ValueError("OFF has no physical current or phase setpoint")

    @classmethod
    def off(cls) -> OperatingPoint:
        return cls(False, None, None, Fraction(0))


class ResultStatus(StrEnum):
    FEASIBLE = "feasible"
    OFF = "off"
    UNREACHABLE = "unreachable"
    DEFERRED = "deferred"


class Reason(StrEnum):
    SELECTED = "selected"
    OFF_SELECTED = "off_selected"
    CHARGING_PROHIBITED = "charging_prohibited"
    STOP_UNVERIFIED = "stop_unverified"
    NO_ELIGIBLE_MODE = "no_eligible_mode"
    VOLTAGE_UNAVAILABLE = "voltage_unavailable"
    OBSERVATION_MISMATCH = "observation_mismatch"
    ELECTRICAL_LIMIT = "electrical_limit"
    DIRECTION_UNREACHABLE = "direction_unreachable"
    TRANSITION_RESTRICTED = "transition_restricted"


@dataclass(frozen=True)
class SolverResult:
    """Only point is actionable; suggested is diagnostic, never implicit relaxation.

    The future transition planner may return DEFERRED with a suggested point and
    monotonic retry deadline. The pure power solver does not schedule transitions.
    Bounds describe the known eligible set (including OFF when supported).
    """

    status: ResultStatus
    reason: Reason
    point: OperatingPoint | None = None
    min_power_w: Fraction | None = None
    max_power_w: Fraction | None = None
    suggested: OperatingPoint | None = None
    retry_at_monotonic: Fraction | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ResultStatus) or not isinstance(
            self.reason, Reason
        ):
            raise ValueError("invalid solver status/reason")
        for point in (self.point, self.suggested):
            if point is not None and not isinstance(point, OperatingPoint):
                raise ValueError("invalid operating point")
        success = self.status in (ResultStatus.FEASIBLE, ResultStatus.OFF)
        if success != (self.point is not None):
            raise ValueError("only success carries an actionable point")
        if self.point is not None:
            if self.point.charging != (self.status == ResultStatus.FEASIBLE):
                raise ValueError("point and status disagree")
        if (self.min_power_w is None) != (self.max_power_w is None):
            raise ValueError("bounds must be supplied together")
        for name in ("min_power_w", "max_power_w", "retry_at_monotonic"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, scalar(getattr(self, name)))
        if self.min_power_w is not None and self.min_power_w > self.max_power_w:
            raise ValueError("invalid result bounds")
        deferred = self.status == ResultStatus.DEFERRED
        if deferred != (self.retry_at_monotonic is not None):
            raise ValueError("only deferred results require a retry deadline")
        if deferred and (
            self.suggested is None or self.reason != Reason.TRANSITION_RESTRICTED
        ):
            raise ValueError("deferred result requires a transition suggestion")
