"""Optional operator fallback fields associated with explicit topology identity."""

from ..core.capabilities import CapabilityEvidence, EvidenceState
from ..core.electrical import ElectricalCapability, phase_modes
from ..core.models import ConnectorId, EvseId, StationId

FIELDS = {
    "reference_phases": "supported_phases",
    "reference_min_a": "minimum_current",
    "reference_step_a": "current_step",
    "reference_max_1a": "maximum_current_1",
    "reference_max_2a": "maximum_current_2",
    "reference_max_3a": "maximum_current_3",
    "reference_phase_switching": "phase_switching",
    "reference_enable_disable": "enable_disable",
}


class ConfiguredReference:
    def __init__(self, options):
        self.children = tuple(
            ConfiguredReference(value)
            for station in options.get("station_references", {}).values()
            for value in station.values()
        )
        self.options = options
        self.target = None
        if options.get("reference_station_id"):
            self.target = ConnectorId(
                EvseId(
                    StationId(options["reference_station_id"]),
                    str(options["reference_evse_id"]),
                ),
                str(options["reference_connector_id"]),
            )
        self.modes = (
            phase_modes(options["reference_modes"])
            if options.get("reference_modes")
            else ()
        )

    def capabilities(self, target, at):
        if self.children:
            return tuple(
                c for child in self.children for c in child.capabilities(target, at)
            )
        if (
            self.target is None
            or not isinstance(target, ConnectorId)
            or target.evse != self.target.evse
        ):
            return ()
        proof = CapabilityEvidence(
            EvidenceState.VERIFIED, "configured_operator_fallback", at
        )
        result = []
        for option, key in FIELDS.items():
            if option not in self.options:
                continue
            if (
                key.startswith("maximum_") or key == "enable_disable"
            ) and target != self.target:
                continue
            value = self.options[option]
            if key == "supported_phases":
                value = tuple(int(n.strip()) for n in value.split(","))
            scope = (
                target
                if key.startswith("maximum_") or key == "enable_disable"
                else target.evse
            )
            result.append(ElectricalCapability(scope, key, value, proof))
        return tuple(result)
