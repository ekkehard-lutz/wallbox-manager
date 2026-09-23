"""OCPP 2.1 discovery and reference-device transaction-scoped charging control."""

from contextvars import ContextVar

from ocpp.exceptions import OCPPError
from ocpp.v21 import ChargePoint, call
from websockets.exceptions import ConnectionClosed

from ....control.commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
    CommandValidity,
    ControlArea,
    command_is_current,
    stale_command_result,
)
from ....core.models import EvseId, Phase
from ....solver.operating_point import OperatingPoint
from ..common.inventory import InventoryAdapter

# Context follows DiscoveryAdapter's session-owned task, without contaminating
# concurrent discovery calls or inbound CALLRESULTs on the same adapter.
_dispatch_guard = ContextVar("ocpp21_control_dispatch_guard", default=None)


class _DispatchRefused(Exception):
    def __init__(self, result):
        self.result = result


class Adapter(InventoryAdapter, ChargePoint):
    """ControlAdapter for the verified reference mapping: station EVSE 1.

    One-phase means physical L1; other single-phase selections require explicit
    topology support and cannot be expressed merely by numberPhases.
    """

    def _active_transaction(self):
        target = EvseId(self.token.station, "1")
        sessions = [
            session
            for session in self.runtime.sessions.latest
            if session.active and session.evse_id == target
        ]
        return sessions[0].external_transaction_id if len(sessions) == 1 else None

    async def _send(self, message):
        # ocpp==2.1.0 invokes this after schema validation and _call_lock.
        guard = _dispatch_guard.get()
        if guard is not None:
            guard()
        await super()._send(message)

    async def apply_operating_point(
        self, point: OperatingPoint, *, is_current: CommandValidity
    ) -> CommandResult:
        if not isinstance(point, OperatingPoint):
            raise ValueError("expected a resolved operating point")
        if not command_is_current(is_current):
            return stale_command_result()
        if not point.charging:
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.UNSUPPORTED_OPERATION,
                "Stop/disable semantics are not implemented yet.",
            )
        if point.current_a.denominator != 1:
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.CURRENT,
                CommandReason.UNSUPPORTED_OPERATION,
                "Capability knowledge must resolve current to whole amperes.",
            )
        if point.mode.phases not in ((Phase.L1,), (Phase.L1, Phase.L2, Phase.L3)):
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.PHASE_MODE,
                CommandReason.UNSUPPORTED_OPERATION,
                "The requested physical phase mapping is not verified.",
            )
        token = self.token
        transaction = self._active_transaction()

        def guard():
            if not command_is_current(is_current):
                raise _DispatchRefused(stale_command_result())
            if self.token != token or not self.runtime.current(token):
                raise _DispatchRefused(
                    CommandResult(
                        CommandStatus.FAILED, reason=CommandReason.COMMUNICATION_ERROR
                    )
                )
            if transaction is None or self._active_transaction() != transaction:
                raise _DispatchRefused(
                    CommandResult(
                        CommandStatus.TEMPORARILY_REJECTED,
                        reason=CommandReason.TRANSACTION_UNAVAILABLE,
                        detail="An unambiguous active transaction is required.",
                    )
                )

        context = _dispatch_guard.set(guard)
        try:
            guard()
            response = await self.call(
                call.SetChargingProfile(
                    evse_id=1,
                    charging_profile={
                        "id": 1,
                        "stack_level": 0,
                        "charging_profile_purpose": "TxProfile",
                        "charging_profile_kind": "Absolute",
                        "transaction_id": transaction,
                        "charging_schedule": [
                            {
                                "id": 1,
                                "charging_rate_unit": "A",
                                "charging_schedule_period": [
                                    {
                                        "start_period": 0,
                                        "limit": int(point.current_a),
                                        "number_phases": point.mode.count,
                                    }
                                ],
                            }
                        ],
                    },
                ),
                suppress=False,
            )
        except _DispatchRefused as exc:
            return exc.result
        except TimeoutError:
            return CommandResult(CommandStatus.FAILED, reason=CommandReason.TIMEOUT)
        except ConnectionClosed, OSError, OCPPError:
            return CommandResult(
                CommandStatus.FAILED, reason=CommandReason.COMMUNICATION_ERROR
            )
        finally:
            _dispatch_guard.reset(context)
        if response.status == "Accepted":
            return CommandResult(CommandStatus.APPLIED)
        if response.status == "Rejected":
            return CommandResult(
                CommandStatus.TEMPORARILY_REJECTED, reason=CommandReason.BUSY
            )
        return CommandResult(
            CommandStatus.FAILED, reason=CommandReason.COMMUNICATION_ERROR
        )
