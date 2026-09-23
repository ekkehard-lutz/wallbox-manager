"""Bounded read-only polling and explicit hardware permission readback."""

import asyncio
from datetime import UTC, datetime, timedelta

from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from websockets.exceptions import ConnectionClosed

from ....core.enabled import EnabledObservation

# Internal hardware-test tuning, not public configuration or protocol semantics.
POLL_INTERVAL = 5
STATE_LIFETIME = 15


async def read_enabled(bound):
    """Read one confirmed value. No authority acquisition or corrective writes."""
    from .adapter import _dispatch_guard, _DispatchRefused

    live = bound.adapter
    component = bound._permission_component(writable=False)
    if component is None and live.runtime.enabled_observation(bound.target) is None:
        return None
    at = datetime.now(UTC)
    value = None

    def guard():
        nonlocal at
        if live.token != bound.token or not live.runtime.current(bound.token):
            from ....control.commands import stale_command_result

            raise _DispatchRefused(stale_command_result())
        if bound._permission_component(writable=False) != component:
            raise ValueError("enabled endpoint changed")
        at = datetime.now(UTC)

    context = _dispatch_guard.set(guard)
    try:
        if component is not None:
            response = await live.call(
                call.GetVariables(
                    get_variable_data=[
                        {
                            "component": component,
                            "variable": {"name": "ChargingEnabled"},
                            "attribute_type": "Actual",
                        }
                    ]
                ),
                suppress=False,
            )
            rows = response.get_variable_result
            if len(rows) == 1:
                row = rows[0]
                if (
                    row.get("component") == component
                    and row.get("variable") == {"name": "ChargingEnabled"}
                    and row.get("attribute_type", "Actual") == "Actual"
                    and row.get("attribute_status") == "Accepted"
                ):
                    value = {"true": True, "false": False}.get(
                        row.get("attribute_value")
                    )
    except (
        TimeoutError,
        OSError,
        OCPPError,
        ConnectionClosed,
        ValueError,
        _DispatchRefused,
    ):
        pass
    finally:
        _dispatch_guard.reset(context)
    live.runtime.observe_enabled(
        bound.token,
        EnabledObservation(
            bound.target, value, at, at + timedelta(seconds=STATE_LIFETIME)
        ),
    )
    return (
        live.runtime.enabled(bound.target)
        if live.runtime.current(bound.token)
        else None
    )


async def poll_enabled(live, token):
    while live.runtime.current(token) and live.token == token:
        for target in live.runtime.get(token.station).connectors:
            bound = live.bind_control(
                target, lambda: None, phase_operation_evidence=lambda *_: None
            )
            await read_enabled(bound)
        await asyncio.sleep(POLL_INTERVAL)
