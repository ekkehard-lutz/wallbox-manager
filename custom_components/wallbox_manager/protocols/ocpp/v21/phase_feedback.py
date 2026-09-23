"""Strictly scoped standard physical conductor feedback, never capability proof."""

from datetime import datetime, timedelta

from ....core.models import Phase, PhaseMode, PhysicalPhaseObservation
from ..common.inventory import connector_identity

SOURCE = "ocpp2.1:Connector.PhaseRotation"
LIFETIME = timedelta(seconds=5)


def accept_phase_events(runtime, token, events):
    if not runtime.current(token):
        return
    for event in events:
        component = event.get("component", {})
        scope = component.get("evse", {})
        if (
            component.get("name") != "Connector"
            or set(component) != {"name", "evse"}
            or set(scope) != {"id", "connector_id"}
            or event.get("variable") != {"name": "PhaseRotation"}
            or event.get("event_notification_type") != "HardWiredNotification"
        ):
            continue
        try:
            target = connector_identity(
                token.station, scope["id"], scope["connector_id"]
            )
            observed_at = datetime.fromisoformat(
                event["timestamp"].replace("Z", "+00:00")
            )
            observation = PhysicalPhaseObservation(
                target,
                {"Rxx": PhaseMode((Phase.L1,)), "RST": PhaseMode(tuple(Phase))}.get(
                    event.get("actual_value")
                ),
                observed_at,
                observed_at + LIFETIME,
                SOURCE,
            )
        except ValueError, TypeError, KeyError:
            continue
        runtime.observe_physical_phase(token, observation)
