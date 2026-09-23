"""Descriptive Device Model extensions, not universal OCA variable claims."""

from datetime import UTC, datetime
from fractions import Fraction

from ....core.capabilities import CapabilityEvidence, EvidenceState
from ....core.electrical import ElectricalCapability, validate_value
from ..common.inventory import connector_identity, evse_identity

VARIABLES = {
    "SupportedPhaseModes": ("EVSE", "supported_phases"),
    "MinimumCurrent": ("EVSE", "minimum_current"),
    "CurrentStep": ("EVSE", "current_step"),
    "PhaseSwitchingSupported": ("EVSE", "phase_switching"),
    "MaximumCurrent1Phase": ("Connector", "maximum_current_1"),
    "MaximumCurrent2Phase": ("Connector", "maximum_current_2"),
    "MaximumCurrent3Phase": ("Connector", "maximum_current_3"),
    "ChargingEnableDisableSupported": ("Connector", "enable_disable"),
}


def parse_capabilities(station, rows):
    observations = {}
    at = datetime.now(UTC)
    for row in rows:
        component, variable = row.get("component", {}), row.get("variable", {})
        spec = VARIABLES.get(variable.get("name"))
        if spec is None:
            continue
        component_name, key = spec
        scope = component.get("evse", {})
        if (
            component.get("name") != component_name
            or set(component) != {"name", "evse"}
            or set(variable) != {"name"}
        ):
            continue
        try:
            if component_name == "EVSE" and set(scope) == {"id"}:
                target = evse_identity(station, scope["id"])
            elif component_name == "Connector" and set(scope) == {"id", "connector_id"}:
                target = connector_identity(station, scope["id"], scope["connector_id"])
            else:
                continue
        except ValueError, KeyError, TypeError:
            continue
        values = observations.setdefault((target, key), [])
        attrs = [
            a
            for a in row.get("variable_attribute", [])
            if a.get("type", "Actual") == "Actual"
        ]
        if not attrs:
            values.append(None)
        for attr in attrs:
            try:
                text = attr["value"].strip()
                if key == "supported_phases":
                    value = tuple(int(n.strip()) for n in text.split(","))
                elif key in ("enable_disable", "phase_switching"):
                    value = {"true": True, "false": False}[text.lower()]
                else:
                    value = Fraction(text)
                values.append(validate_value(key, value))
            except ValueError, TypeError, KeyError, ZeroDivisionError, AttributeError:
                values.append(None)
    result = []
    for (scope, key), values in observations.items():
        valid = None not in values and all(v == values[0] for v in values)
        result.append(
            ElectricalCapability(
                scope,
                key,
                values[0] if valid else None,
                CapabilityEvidence(
                    EvidenceState.VERIFIED if valid else EvidenceState.DEGRADED,
                    "ocpp2.1:FullInventory:" + key,
                    at,
                    None if valid else "invalid_or_conflicting_values",
                ),
            )
        )
    return tuple(result)
