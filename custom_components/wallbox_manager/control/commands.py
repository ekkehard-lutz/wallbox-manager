"""Protocol-neutral execution of already resolved operating points."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..core.values import nonempty
from ..solver.operating_point import OperatingPoint


class CommandStatus(StrEnum):
    APPLIED = "applied"
    TEMPORARILY_REJECTED = "temporarily_rejected"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ControlArea(StrEnum):
    OPERATING_POINT = "operating_point"
    CHARGING_PERMISSION = "charging_permission"
    CURRENT = "current"
    PHASE_MODE = "phase_mode"


class CommandReason(StrEnum):
    STALE = "stale"
    BUSY = "busy"
    TRANSACTION_UNAVAILABLE = "transaction_unavailable"
    PHASE_SWITCH_LOCKOUT = "phase_switch_lockout"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    TIMEOUT = "timeout"
    COMMUNICATION_ERROR = "communication_error"
    HARDWARE_ERROR = "hardware_error"


@dataclass(frozen=True)
class CommandResult:
    """Confirmed outcome, not measured consumption or an optimistic target.

    Temporary rejection is normal runtime behavior, not a technical failure.
    Runtime UNSUPPORTED indicates missing/inconsistent capability knowledge.
    Area identifies the affected control area, not a separate dispatch operation.
    Detail is optional protocol-neutral diagnostic text, never a wire payload.
    """

    status: CommandStatus
    area: ControlArea = ControlArea.OPERATING_POINT
    reason: CommandReason | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CommandStatus):
            raise ValueError("invalid command status")
        if not isinstance(self.area, ControlArea):
            raise ValueError("invalid control area")
        if self.reason is not None and not isinstance(self.reason, CommandReason):
            raise ValueError("invalid command reason")
        if self.detail is not None:
            nonempty(self.detail)


# Capture a desired-target generation in this synchronous predicate. Once false,
# it must remain false. It must be side-effect-free and return an actual bool.
CommandValidity = Callable[[], bool]


def command_is_current(is_current: CommandValidity) -> bool:
    """Validate a caller's fence; adapters also use this at dispatch boundaries."""
    current = is_current()
    if type(current) is not bool:
        raise ValueError("command validity must return boolean")
    return current


def stale_command_result() -> CommandResult:
    """A superseded target is a normal refusal, not a device failure."""
    return CommandResult(CommandStatus.TEMPORARILY_REJECTED, reason=CommandReason.STALE)


class ControlAdapter(Protocol):
    """One adapter instance is bound to one controllable device scope.

    Apply permission, physical phases and current as one coordinated operation.
    OFF means disable charging using the device's stop semantics, not generic 0 A.
    APPLIED requires confirmation of the whole requested operation.

    Check is_current after queue/lock waits and immediately before each device
    side effect, with no intervening await before dispatch. Keep device-specific
    atomicity and serialization inside the adapter. If stale, send no further
    target actions and return stale_command_result(). This fence cannot undo an
    already dispatched operation; safe completion/reconciliation of a partial
    operation is device-specific. The controller must fence late results before
    publishing active state. No cancellation or rollback guarantee is implied.
    """

    async def apply_operating_point(
        self, point: OperatingPoint, *, is_current: CommandValidity
    ) -> CommandResult: ...


async def apply_operating_point(
    adapter: ControlAdapter,
    point: OperatingPoint,
    *,
    is_current: CommandValidity,
) -> CommandResult:
    """Forward an actionable point unchanged and validate the adapter outcome.

    No solving, capability discovery or policy occurs here. Invalid local inputs
    and adapter contract violations raise; normal outcomes are returned as data.
    Unexpected exceptions/cancellation propagate without disguising their cause.
    """
    if not isinstance(point, OperatingPoint):
        raise ValueError("expected a resolved operating point")
    if not command_is_current(is_current):
        return stale_command_result()
    result = await adapter.apply_operating_point(point, is_current=is_current)
    if not isinstance(result, CommandResult):
        raise ValueError("adapter returned an invalid command result")
    return result
