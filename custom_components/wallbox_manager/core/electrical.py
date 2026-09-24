"""Individually sourced electrical capabilities; operator intent is separate."""

from dataclasses import dataclass
from fractions import Fraction

from .capabilities import CapabilityEvidence, EvidenceState
from .models import ConnectorId, EvseId, Phase, PhaseMode
from .values import scalar

EVSE_FIELDS = frozenset(
    ("supported_phases", "minimum_current", "current_step", "phase_switching")
)
CONNECTOR_FIELDS = frozenset(
    (
        "maximum_current",  # Generic current fact; never implies a phase mode.
        "maximum_current_1",
        "maximum_current_2",
        "maximum_current_3",
        "enable_disable",
        "zero_current",
    )
)


@dataclass(frozen=True)
class ElectricalCapability:
    scope: EvseId | ConnectorId
    key: str
    value: Fraction | tuple[int, ...] | bool | None
    evidence: CapabilityEvidence

    def __post_init__(self):
        fields = (
            CONNECTOR_FIELDS if isinstance(self.scope, ConnectorId) else EVSE_FIELDS
        )
        if self.key not in fields or not isinstance(self.scope, (EvseId, ConnectorId)):
            raise ValueError("invalid capability scope")
        if not isinstance(self.evidence, CapabilityEvidence):
            raise ValueError("invalid capability evidence")
        if self.value is not None:
            object.__setattr__(self, "value", validate_value(self.key, self.value))


def validate_value(key, value):
    if key == "supported_phases":
        values = tuple(value)
        if (
            not values
            or len(set(values)) != len(values)
            or any(type(n) is not int or n not in (1, 2, 3) for n in values)
        ):
            raise ValueError("invalid supported phase counts")
        return tuple(sorted(values))
    if key in ("phase_switching", "enable_disable", "zero_current"):
        if type(value) is not bool:
            raise ValueError("boolean capability required")
        return value
    return scalar(Fraction(value) if isinstance(value, str) else value, positive=True)


def resolve_capabilities(observed, references):
    """Resolve each field; explicit denial and malformed data block fallback."""
    values = {(c.scope, c.key): c for c in references}
    for capability in observed:
        if capability.evidence.state in (
            EvidenceState.VERIFIED,
            EvidenceState.UNSUPPORTED,
            EvidenceState.DEGRADED,
        ):
            values[capability.scope, capability.key] = capability
    # Counts are authoritative: a reference maximum never creates mode support.
    for (scope, key), capability in tuple(values.items()):
        if isinstance(scope, ConnectorId) and key.startswith("maximum_current_"):
            counts = values.get((scope.evse, "supported_phases"))
            minimum = values.get((scope.evse, "minimum_current"))
            if (
                counts is not None
                and counts.value is not None
                and int(key[-1]) not in counts.value
            ):
                del values[scope, key]
            elif (
                minimum
                and minimum.value is not None
                and capability.value is not None
                and capability.value < minimum.value
            ):
                values[scope, key] = ElectricalCapability(
                    scope,
                    key,
                    None,
                    CapabilityEvidence(
                        EvidenceState.DEGRADED,
                        capability.evidence.source,
                        capability.evidence.observed_at,
                        "maximum_below_minimum",
                    ),
                )
    return tuple(values.values())


def phase_modes(text):
    """Explicit conductor mappings, e.g. 'l1;l1,l2;l1,l2,l3'."""
    modes = tuple(
        PhaseMode(tuple(Phase(p.strip().lower()) for p in mode.split(",")))
        for mode in text.split(";")
    )
    if len(set(modes)) != len(modes):
        raise ValueError("duplicate phase mode")
    return modes
