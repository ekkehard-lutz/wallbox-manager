"""Explicit user commands; observations remain exclusively in State."""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from websockets.exceptions import ConnectionClosed

DISPATCH = ContextVar("wbm_control_dispatch", default=None)
ROLES = {
    "ControlAuthority": "control_authority",
    "ChargingEnabled": "charging_enabled",
}


def endpoint(name):
    return {"component": {"name": "WallboxController"}, "variable": {"name": name}}


class Refused(Exception):
    """An obsolete or unauthorized operation must never reach the wire."""


@dataclass
class Intent:
    name: str
    value: str
    adapter: object
    generation: tuple
    status: str = "pending"
    reason: str | None = None
    dispatched_at: datetime | None = None
    task: object = None


class Control:
    def __init__(self, listener):
        self.listener = listener
        self.intents = {}

    def key(self, name):
        state = self.listener.state
        return (
            ("station", (state.binding[0], None, None), "control_authority")
            if name == "ControlAuthority"
            else ("control", state.binding, "charging_enabled")
        )

    def blocked(self, name):
        adapter = self.listener.adapter
        if not adapter or not adapter.live(adapter.generation):
            return "disconnected"
        if not self.listener.state.current(self.listener.state.registered_generation):
            return "not_registered"
        if name not in adapter.writable:
            return "unsupported"
        return None

    def attributes(self, name):
        intent = self.intents.get(name)
        reason = self.blocked(name)
        return {
            "execution_ready": reason is None,
            "execution_blocked_reason": reason,
            "command_status": intent.status if intent else None,
            "command_reason": intent.reason if intent else None,
            "requested_value": intent.value if intent else None,
        }

    def publish(self, name):
        # Only control entities subscribe to these command diagnostics.
        state = self.listener.state
        intent = self.intents.get(name)
        if intent and (
            intent.status
            in ("rejected", "unsupported", "timeout", "communication_failure")
            or intent.reason in ("readback_timeout", "readback_communication_failure")
        ):
            state.messages.set(
                "control:" + name,
                source="control",
                severity="error",
                code="control_failed",
                detail=f"{name}: {intent.reason or intent.status}",
                generation=intent.generation,
                token=id(intent),
            )
        else:
            state.messages.clear("control:" + name)
        state.publish(("command", self.key(name)[1], ROLES[name]))

    def update(self, intent, status, reason=None):
        if self.intents.get(intent.name) is intent and (
            intent.status,
            intent.reason,
        ) != (status, reason):
            intent.status, intent.reason = status, reason
            self.publish(intent.name)
            self.listener.diagnostic(
                1, "command_" + intent.name, reason=reason or status, status=status
            )

    def invalidate(self):
        for intent in self.intents.values():
            if intent.status in ("pending", "accepted_unconfirmed"):
                self.update(intent, "superseded", "connection_or_boot_changed")

    def valid(self, intent):
        return (
            self.intents.get(intent.name) is intent
            and intent.status != "superseded"
            and intent.adapter.live(intent.generation)
            and self.blocked(intent.name) is None
        )

    def observed(self, name):
        intent = self.intents.get(name)
        if (
            not intent
            or intent.status != "accepted_unconfirmed"
            or not self.valid(intent)
        ):
            return
        observation = self.listener.state.observations.get(self.key(name))
        expected = "remote" if name == "ControlAuthority" else intent.value == "true"
        if (
            observation
            and observation.generation == intent.generation
            and intent.dispatched_at is not None
            and observation.observed_at >= intent.dispatched_at
            and self.listener.state.value(self.key(name)) == expected
        ):
            self.update(intent, "confirmed")

    async def request(self, name, value):
        allowed = {
            "ControlAuthority": ("OCPP",),
            "ChargingEnabled": ("true", "false"),
        }
        if value not in allowed.get(name, ()):
            raise ValueError("Unsupported control intent")
        old = self.intents.get(name)
        if (
            old
            and old.task
            and not old.task.done()
            and old.value == value
            and self.valid(old)
        ):
            return await asyncio.shield(old.task)
        if (
            old
            and old.value == value
            and old.status == "accepted_unconfirmed"
            and self.valid(old)
        ):
            if name == "ControlAuthority":
                old.task = old.adapter.spawn(self.confirm(old))
                return await asyncio.shield(old.task)
            return old.status
        unresolved_opposite = bool(
            old
            and old.value != value
            and old.dispatched_at
            and old.status not in ("confirmed", "rejected", "unsupported")
        )
        if old and old.status in ("pending", "accepted_unconfirmed"):
            self.update(old, "superseded", "newer_intent")
        adapter = self.listener.adapter
        intent = Intent(
            name,
            value,
            adapter,
            self.listener.state.generation,
        )
        self.intents[name] = intent
        self.publish(name)
        if reason := self.blocked(name):
            self.update(
                intent, "unsupported" if reason == "unsupported" else "rejected", reason
            )
            return intent.status
        if not self.valid(intent):
            self.update(intent, "superseded", "authority_or_intent_changed")
            return "superseded"
        expected = "remote" if name == "ControlAuthority" else value == "true"
        # An opposite in-flight write may still take effect: send corrective intent.
        if (
            not unresolved_opposite
            and (not old or not old.task or old.task.done())
            and self.listener.state.value(self.key(name)) == expected
        ):
            self.update(intent, "confirmed", "already_observed")
            if self.listener.grid:
                self.listener.grid.signature = None
                self.listener.grid.changed()
            return intent.status
        intent.task = adapter.spawn(self.execute(intent))
        return await asyncio.shield(intent.task)

    async def execute(self, intent):
        item = {
            **endpoint(intent.name),
            "attribute_type": "Actual",
            "attribute_value": intent.value,
        }

        def guard(action, wire):
            if (
                action != "SetVariables"
                or not self.valid(intent)
                or wire
                != {
                    "setVariableData": [
                        {
                            "component": item["component"],
                            "variable": item["variable"],
                            "attributeType": "Actual",
                            "attributeValue": intent.value,
                        }
                    ]
                }
            ):
                raise Refused("Unsupported or superseded control command")
            intent.dispatched_at = datetime.now(UTC)

        token = DISPATCH.set(guard)
        try:
            response = await intent.adapter.request(
                call.SetVariables(set_variable_data=[item])
            )
            if not self.valid(intent):
                self.update(intent, "superseded", "authority_or_intent_changed")
                return "superseded"
            rows = getattr(response, "set_variable_result", None)
            if (
                not isinstance(rows, list)
                or len(rows) != 1
                or not isinstance(rows[0], dict)
            ):
                self.update(intent, "communication_failure", "mismatched_response")
                return intent.status
            row = rows[0]
            if (
                any(row.get(k) != v for k, v in endpoint(intent.name).items())
                or row.get("attribute_type", "Actual") != "Actual"
            ):
                self.update(intent, "communication_failure", "mismatched_response")
                return intent.status
            status = row.get("attribute_status")
            if status != "Accepted":
                self.update(
                    intent,
                    "unsupported"
                    if status
                    in (
                        "UnknownComponent",
                        "UnknownVariable",
                        "NotSupportedAttributeType",
                    )
                    else "rejected",
                    row.get("status_info", {}).get("reason_code", status),
                )
                return intent.status
            self.update(intent, "accepted_unconfirmed")
            self.observed(intent.name)
            if intent.status == "confirmed":
                return intent.status
            return await self.confirm(intent)
        except Refused:
            self.update(intent, "superseded", "dispatch_refused")
        except TimeoutError:
            self.update(intent, "timeout", "response_timeout")
        except OSError, OCPPError, ConnectionClosed:
            self.update(intent, "communication_failure", "transport_failure")
        except asyncio.CancelledError:
            self.update(intent, "superseded", "cancelled")
            raise
        finally:
            DISPATCH.reset(token)
        return intent.status

    async def confirm(self, intent):
        """Read once for an acknowledged intent; only observations confirm it."""
        if not self.valid(intent):
            return "superseded"
        try:
            read = await intent.adapter.read(
                intent.generation,
                endpoint(intent.name),
                current=lambda: self.valid(intent),
            )
            if intent.status == "accepted_unconfirmed":
                self.update(intent, intent.status, None if read else "readback_unknown")
        except TimeoutError:
            self.update(intent, intent.status, "readback_timeout")
        except OSError, OCPPError, ConnectionClosed:
            self.update(intent, intent.status, "readback_communication_failure")
        if not self.valid(intent):
            self.update(intent, "superseded", "authority_or_intent_changed")
        return intent.status
