"""EVSE-bound execution of verified operating points using OCPP 2.1."""

import json
import math
from collections.abc import Callable
from contextvars import ContextVar
from fractions import Fraction

from ocpp.exceptions import OCPPError
from ocpp.routing import on
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
from ....core.capabilities import CapabilityEvidence, CapabilitySnapshot, EvidenceState
from ....core.models import ConnectorId, EvseId, PhaseMode
from ....solver.operating_point import OperatingPoint
from ..common.inventory import InventoryAdapter
from .phase_feedback import accept_phase_events

# Context follows DiscoveryAdapter's session-owned task, without contaminating
# concurrent discovery calls or inbound CALLRESULTs on the same adapter.
_dispatch_guard = ContextVar("ocpp21_control_dispatch_guard", default=None)


class _DispatchRefused(Exception):
    def __init__(self, result):
        self.result = result


class Adapter(InventoryAdapter, ChargePoint):
    """Station transport; control bindings share its serialized outbound queue."""

    def electrical_inventory(self, token, rows):
        from .capabilities import parse_capabilities

        self.permission_inventory = (token, tuple(rows))
        return parse_capabilities(token.station, rows)

    @on("NotifyEvent")
    def on_notify_event(self, event_data, **kwargs):
        accept_phase_events(self.runtime, self.token, event_data)
        return self._call_result.NotifyEvent()

    def bind_control(
        self,
        target: EvseId | ConnectorId,
        capabilities: Callable[[], CapabilitySnapshot | None],
        *,
        phase_operation_evidence: Callable[
            [CapabilitySnapshot, PhaseMode], CapabilityEvidence | None
        ],
    ) -> EvseControlAdapter:
        """Bind an EVSE explicitly; no inferred device or topology defaults."""
        return EvseControlAdapter(self, target, capabilities, phase_operation_evidence)

    async def _send(self, message):
        # ocpp==2.1.0 invokes this after schema validation and _call_lock.
        guard = _dispatch_guard.get()
        if guard is not None:
            guard()
        await super()._send(message)


class EvseControlAdapter:
    """ControlAdapter scoped to one EVSE and one connection/boot generation.

    capabilities supplies the canonical normalized snapshot, not a second registry.
    phase_operation_evidence is a synchronous, side-effect-free verifier for the
    *current* operation: does sending numberPhases for this mode preserve the
    verified physical mapping and safely perform any needed transition? A fixed
    device can verify its fixed mode without claiming switching support. An
    envelope alone is not evidence of this protocol-to-physical mapping.

    Both providers are read again at dispatch. Evidence must apply to the supplied
    snapshot and current physical state; unknown/unavailable proof fails closed.
    No conductor selection is inferred from the phase count, and no phase_to_use
    is emitted. More complex mappings need a separately verified execution path.
    """

    def __init__(self, adapter, target, capabilities, phase_operation_evidence):
        evse = target.evse if isinstance(target, ConnectorId) else target
        if (
            not isinstance(evse, EvseId)
            or target.station != adapter.token.station
            or not evse.value.isascii()
            or not evse.value.isdecimal()
            or int(evse.value) <= 0
            or str(int(evse.value)) != evse.value
        ):
            raise ValueError("target must be a canonical positive EVSE of this station")
        if isinstance(target, ConnectorId) and (
            not target.value.isascii()
            or not target.value.isdecimal()
            or int(target.value) <= 0
            or str(int(target.value)) != target.value
        ):
            raise ValueError("connector ID must be canonical and positive")
        if not callable(capabilities) or not callable(phase_operation_evidence):
            raise ValueError(
                "explicit capability and phase operation providers required"
            )
        self.adapter = adapter
        self.target = target
        self.evse = evse
        self.permission_evidence = lambda: None
        self.token = adapter.token
        self._capabilities = capabilities
        self._phase_operation_evidence = phase_operation_evidence

    def _active_transaction(self):
        sessions = [
            session
            for session in self.adapter.runtime.sessions.latest
            if session.active and session.evse_id == self.evse
        ]
        if len(sessions) != 1 or (
            isinstance(self.target, ConnectorId) and sessions[0].scope != self.target
        ):
            return None
        return sessions[0].external_transaction_id

    @staticmethod
    def _unsupported(area, detail):
        return CommandResult(
            CommandStatus.UNSUPPORTED, area, CommandReason.UNSUPPORTED_OPERATION, detail
        )

    def _validate_capabilities(self, point):
        snapshot = self._capabilities()
        state = self.adapter.runtime.get(self.target.station)
        if snapshot is not None and not isinstance(snapshot, CapabilitySnapshot):
            raise ValueError("capability provider must return a CapabilitySnapshot")
        if (
            snapshot is None
            or snapshot.scope != self.target
            or snapshot.connection_generation != self.token.connection_generation
            or snapshot.boot_generation != self.token.boot_generation
            or state is None
            or snapshot.firmware != state.identity.firmware
        ):
            return self._unsupported(
                ControlArea.OPERATING_POINT,
                "Matching current EVSE capabilities required.",
            )
        envelope = next((e for e in snapshot.envelopes if e.mode == point.mode), None)
        if envelope is None or envelope.evidence.state != EvidenceState.VERIFIED:
            return self._unsupported(
                ControlArea.PHASE_MODE,
                "The requested operating envelope is not verified.",
            )
        if (
            not envelope.min_current_a <= point.current_a <= envelope.max_current_a
            or (
                (point.current_a - envelope.min_current_a) / envelope.current_step_a
            ).denominator
            != 1
        ):
            return self._unsupported(
                ControlArea.CURRENT, "Current is outside the verified device grid."
            )
        evidence = self._phase_operation_evidence(snapshot, point.mode)
        if evidence is not None and not isinstance(evidence, CapabilityEvidence):
            raise ValueError("phase operation provider must return CapabilityEvidence")
        if evidence is None or evidence.state != EvidenceState.VERIFIED:
            return self._unsupported(
                ControlArea.PHASE_MODE,
                "Physical phase mapping and any required transition are not verified.",
            )
        return None

    @staticmethod
    def _wire_current(current):
        # The pinned library uses JSON numbers (int/float), not Fraction/Decimal.
        # Compare the actual JSON decimal to the exact setpoint, including values
        # like 6.1 which have an inexact binary float but an exact JSON spelling.
        if current.denominator == 1:
            return int(current)
        try:
            value = float(current)
        except OverflowError:
            return None
        if not math.isfinite(value) or Fraction(json.dumps(value)) != current:
            return None
        return value

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
        refusal = self._validate_capabilities(point)
        if refusal is not None:
            return refusal
        current = self._wire_current(point.current_a)
        if current is None:
            return self._unsupported(
                ControlArea.CURRENT,
                "Current cannot be represented exactly on this transport.",
            )
        token = self.token
        transaction = self._active_transaction()

        def guard():
            if not command_is_current(is_current):
                raise _DispatchRefused(stale_command_result())
            if self.adapter.token != token or not self.adapter.runtime.current(token):
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

            refusal = self._validate_capabilities(point)
            if refusal is not None:
                raise _DispatchRefused(refusal)

        context = _dispatch_guard.set(guard)
        try:
            guard()
            response = await self.adapter.call(
                call.SetChargingProfile(
                    evse_id=int(self.evse.value),
                    charging_profile={
                        "id": int(self.evse.value),
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
                                        "limit": current,
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

    def _permission_component(self):
        inventory = getattr(self.adapter, "permission_inventory", None)
        if (
            inventory is None
            or inventory[0] != self.token
            or not isinstance(self.target, ConnectorId)
        ):
            return None
        state = self.adapter.runtime.get(self.target.station)
        components = []
        for row in inventory[1]:
            component = row.get("component", {})
            if component.get("name") != "WallboxController" or row.get("variable") != {
                "name": "ChargingEnabled"
            }:
                continue
            exact = {
                "name": "WallboxController",
                "evse": {
                    "id": int(self.evse.value),
                    "connector_id": int(self.target.value),
                },
            }
            if component != exact and not (
                component == {"name": "WallboxController"}
                and state.connectors == (self.target,)
            ):
                continue
            attrs = [
                a
                for a in row.get("variable_attribute", [])
                if a.get("type", "Actual") == "Actual"
            ]
            if len(attrs) != 1 or attrs[0].get("mutability") not in (
                "ReadWrite",
                "WriteOnly",
            ):
                return None
            components.append(component)
        return components[0] if len(components) == 1 else None

    def can_set_charging_permission(self):
        proof = self.permission_evidence()
        return bool(
            self.adapter.runtime.current(self.token)
            and self._permission_component() is not None
            and proof is not None
            and proof.state == EvidenceState.VERIFIED
        )

    async def apply_charging_permission(self, enabled, *, is_current):
        component = self._permission_component()
        proof = self.permission_evidence()

        def guard():
            if (
                not command_is_current(is_current)
                or self.adapter.token != self.token
                or not self.adapter.runtime.current(self.token)
            ):
                raise _DispatchRefused(stale_command_result())
            if (
                component is None
                or self._permission_component() != component
                or proof is None
                or proof.state != EvidenceState.VERIFIED
                or self.permission_evidence() != proof
            ):
                raise _DispatchRefused(
                    self._unsupported(
                        ControlArea.CHARGING_PERMISSION,
                        "Verified permission and writable scoped endpoint required.",
                    )
                )

        context = _dispatch_guard.set(guard)
        try:
            guard()
            response = await self.adapter.call(
                call.SetVariables(
                    set_variable_data=[
                        {
                            "component": component,
                            "variable": {"name": "ChargingEnabled"},
                            "attribute_type": "Actual",
                            "attribute_value": "true" if enabled else "false",
                        }
                    ]
                ),
                suppress=False,
            )
            guard()
            results = response.set_variable_result
            if (
                len(results) != 1
                or results[0].get("component") != component
                or results[0].get("variable") != {"name": "ChargingEnabled"}
                or results[0].get("attribute_type", "Actual") != "Actual"
            ):
                return CommandResult(
                    CommandStatus.FAILED,
                    ControlArea.CHARGING_PERMISSION,
                    CommandReason.COMMUNICATION_ERROR,
                )
            if results[0].get("attribute_status") == "Accepted":
                return CommandResult(
                    CommandStatus.APPLIED, ControlArea.CHARGING_PERMISSION
                )
            return CommandResult(
                CommandStatus.TEMPORARILY_REJECTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.BUSY,
            )
        except _DispatchRefused as exc:
            return exc.result
        except TimeoutError:
            return CommandResult(
                CommandStatus.FAILED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.TIMEOUT,
            )
        except ConnectionClosed, OSError, OCPPError:
            return CommandResult(
                CommandStatus.FAILED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.COMMUNICATION_ERROR,
            )
        finally:
            _dispatch_guard.reset(context)
