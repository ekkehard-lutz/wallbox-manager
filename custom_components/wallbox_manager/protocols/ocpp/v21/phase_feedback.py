"""Reference wiring interpretation of standard Connector.PhaseRotation events.

No advertisement, meter current or requested phase count establishes this proof.
Other devices need their own verified mapping; this is not generic OCPP discovery.
"""

from datetime import datetime, timedelta

from ....core.models import EvseId, Phase, PhaseMode, PhysicalPhaseObservation

SOURCE = "ocpp2.1:wallbox-stationary:Connector.PhaseRotation"
LIFETIME = timedelta(seconds=5)


def accept_phase_events(runtime, token, events):
    state = runtime.get(token.station)
    if (
        not runtime.current(token)
        or state.identity.vendor != "wallbox-stationary"
        or state.identity.model != "wallbox-stationary"
    ):
        return
    for event in events:
        if (
            event.get("component")
            != {"name": "Connector", "evse": {"id": 1, "connector_id": 1}}
            or event.get("variable") != {"name": "PhaseRotation"}
            or event.get("event_notification_type") != "HardWiredNotification"
        ):
            continue
        try:
            observed_at = datetime.fromisoformat(
                event["timestamp"].replace("Z", "+00:00")
            )
            observation = PhysicalPhaseObservation(
                EvseId(token.station, "1"),
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
