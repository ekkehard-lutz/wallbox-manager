"""Station-scoped, implementation-defined WallboxController authority transport.

The inspected interface uses Actual Local/OCPP, permits only takeover, and emits
hard-wired local-loss events. It is not a standardized OCPP ownership variable.
"""

from datetime import UTC, datetime

from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from websockets.exceptions import ConnectionClosed

from ....control.commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
    ControlArea,
    command_is_current,
    stale_command_result,
)
from ....core.authority import AuthorityObservation, ControlAuthority

COMPONENT = {"name": "WallboxController"}
VARIABLE = {"name": "ControlAuthority"}
SOURCE = "ocpp2.1:WallboxController.ControlAuthority"


def normalized(value):
    return {"Local": ControlAuthority.LOCAL, "OCPP": ControlAuthority.REMOTE}.get(
        value, ControlAuthority.UNKNOWN
    )


def authority_rows(rows):
    return [
        row
        for row in rows
        if row.get("component") == COMPONENT and row.get("variable") == VARIABLE
    ]


def inventory_observation(runtime, token, rows, at):
    matches = authority_rows(rows)
    if not matches:
        return
    attrs = [
        a
        for row in matches
        for a in row.get("variable_attribute", [])
        if a.get("type", "Actual") == "Actual"
    ]
    value = (
        normalized(attrs[0].get("value"))
        if len(matches) == len(attrs) == 1
        else ControlAuthority.UNKNOWN
    )
    runtime.observe_authority(
        token, AuthorityObservation(token.station, value, at, SOURCE + ":FullInventory")
    )


def accept_authority_events(runtime, token, events):
    for event in events:
        if (
            event.get("component") != COMPONENT
            or event.get("variable") != VARIABLE
            or event.get("event_notification_type") != "HardWiredNotification"
        ):
            continue
        try:
            at = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            observation = AuthorityObservation(
                token.station,
                normalized(event.get("actual_value")),
                at,
                SOURCE + ":NotifyEvent",
            )
        except ValueError, TypeError, KeyError:
            continue
        runtime.observe_authority(token, observation)


class StationAuthorityAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.token = adapter.token

    def endpoint(self):
        inventory = getattr(self.adapter, "permission_inventory", None)
        if inventory is None or inventory[0] != self.token:
            return None
        rows = authority_rows(inventory[1])
        if len(rows) != 1:
            return None
        attrs = [
            a
            for a in rows[0].get("variable_attribute", [])
            if a.get("type", "Actual") == "Actual"
        ]
        if len(attrs) != 1 or attrs[0].get("mutability") != "ReadWrite":
            return None
        if normalized(attrs[0].get("value")) == ControlAuthority.UNKNOWN:
            return None
        return rows[0]

    def can_take_control(self):
        return self.adapter.runtime.current(self.token) and self.endpoint() is not None

    async def take_control(self, *, is_current):
        from .adapter import _dispatch_guard, _DispatchRefused

        runtime = self.adapter.runtime
        endpoint = self.endpoint()
        if not command_is_current(is_current) or not runtime.current(self.token):
            return stale_command_result()
        if endpoint is None:
            return CommandResult(
                CommandStatus.UNSUPPORTED,
                ControlArea.AUTHORITY,
                CommandReason.UNSUPPORTED_OPERATION,
            )
        # An unconfirmed write/read sequence cannot retain a previous Remote claim.
        runtime.observe_authority(
            self.token,
            AuthorityObservation(
                self.token.station,
                ControlAuthority.UNKNOWN,
                datetime.now(UTC),
                SOURCE + ":takeover_pending",
            ),
        )
        revision = runtime.get(self.token.station).authority_revision

        def guard():
            if (
                not command_is_current(is_current)
                or self.adapter.token != self.token
                or not runtime.current(self.token)
                or runtime.get(self.token.station).authority_revision != revision
            ):
                raise _DispatchRefused(stale_command_result())
            if self.endpoint() != endpoint:
                raise _DispatchRefused(
                    CommandResult(
                        CommandStatus.UNSUPPORTED,
                        ControlArea.AUTHORITY,
                        CommandReason.UNSUPPORTED_OPERATION,
                    )
                )

        context = _dispatch_guard.set(guard)
        try:
            guard()
            result = await self.adapter.call(
                call.SetVariables(
                    set_variable_data=[
                        {
                            "component": COMPONENT,
                            "variable": VARIABLE,
                            "attribute_type": "Actual",
                            "attribute_value": "OCPP",
                        }
                    ]
                ),
                suppress=False,
            )
            guard()
            outcome = matching_result(result.set_variable_result)
            if outcome is None:
                return CommandResult(
                    CommandStatus.FAILED,
                    ControlArea.AUTHORITY,
                    CommandReason.COMMUNICATION_ERROR,
                )
            if outcome.get("attribute_status") != "Accepted":
                return CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED,
                    ControlArea.AUTHORITY,
                    CommandReason.NO_AUTHORITY,
                )
            result = await self.adapter.call(
                call.GetVariables(
                    get_variable_data=[
                        {
                            "component": COMPONENT,
                            "variable": VARIABLE,
                            "attribute_type": "Actual",
                        }
                    ]
                ),
                suppress=False,
            )
            guard()
            outcome = matching_result(result.get_variable_result)
            if outcome is None or outcome.get("attribute_status") != "Accepted":
                return CommandResult(
                    CommandStatus.FAILED,
                    ControlArea.AUTHORITY,
                    CommandReason.COMMUNICATION_ERROR,
                )
            value = normalized(outcome.get("attribute_value"))
            runtime.observe_authority(
                self.token,
                AuthorityObservation(
                    self.token.station,
                    value,
                    datetime.now(UTC),
                    SOURCE + ":GetVariables",
                ),
            )
            if value != ControlAuthority.REMOTE:
                return CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED,
                    ControlArea.AUTHORITY,
                    CommandReason.NO_AUTHORITY,
                )
            return CommandResult(CommandStatus.APPLIED, ControlArea.AUTHORITY)
        except _DispatchRefused as exc:
            return exc.result
        except TimeoutError:
            return CommandResult(
                CommandStatus.FAILED, ControlArea.AUTHORITY, CommandReason.TIMEOUT
            )
        except ConnectionClosed, OSError, OCPPError:
            return CommandResult(
                CommandStatus.FAILED,
                ControlArea.AUTHORITY,
                CommandReason.COMMUNICATION_ERROR,
            )
        finally:
            _dispatch_guard.reset(context)


def matching_result(results):
    if len(results) != 1:
        return None
    result = results[0]
    if (
        result.get("component") != COMPONENT
        or result.get("variable") != VARIABLE
        or result.get("attribute_type", "Actual") != "Actual"
    ):
        return None
    return result
