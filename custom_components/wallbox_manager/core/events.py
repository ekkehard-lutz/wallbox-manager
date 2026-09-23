"""Immutable discovery/runtime boundary, independent of HA and wire protocols."""

from dataclasses import dataclass

from .capabilities import CapabilityEvidence, CapabilitySnapshot
from .electrical import ElectricalCapability
from .models import ConnectorId, EvseId, PhysicalPhaseObservation, StationId
from .telemetry import Channel, Observation


@dataclass(frozen=True)
class SessionToken:
    """A runtime incarnation fences counters reset by an integration restart."""

    runtime_id: str
    station: StationId
    connection_generation: int
    boot_generation: int


@dataclass(frozen=True)
class StationIdentity:
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None

    def __post_init__(self) -> None:
        for name in ("vendor", "model", "serial", "firmware"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("identity metadata must be text")
                object.__setattr__(self, name, value.strip() or None)


@dataclass(frozen=True)
class StationSnapshot:
    """Known identities survive disconnect; capability evidence does not.

    charging_schedule is advertisement of schedule support, not verified dynamic
    current control, physical envelopes, stop or phase-switching support.
    """

    token: SessionToken
    connected: bool
    identity: StationIdentity
    evses: tuple[EvseId, ...]
    connectors: tuple[ConnectorId, ...]
    capabilities: CapabilitySnapshot
    charging_schedule: CapabilityEvidence
    discovery: CapabilityEvidence
    protocol: str | None = None
    protocol_version: str | None = None
    supported_channels: tuple[Channel, ...] = ()
    observations: tuple[Observation, ...] = ()
    physical_phases: tuple[PhysicalPhaseObservation, ...] = ()
    electrical: tuple[ElectricalCapability, ...] = ()

    def __post_init__(self):
        for name, expected in (
            ("supported_channels", Channel),
            ("observations", Observation),
            ("physical_phases", PhysicalPhaseObservation),
            ("electrical", ElectricalCapability),
        ):
            items = tuple(getattr(self, name))
            if any(not isinstance(item, expected) for item in items):
                raise ValueError("invalid telemetry snapshot")
            object.__setattr__(self, name, items)

    def observation(self, channel: Channel) -> Observation | None:
        return next((o for o in self.observations if o.channel == channel), None)
