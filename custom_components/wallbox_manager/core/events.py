"""Immutable discovery/runtime boundary, independent of HA and wire protocols."""

from dataclasses import dataclass
from datetime import UTC, datetime

from .authority import AuthorityObservation
from .capabilities import CapabilityEvidence, CapabilitySnapshot
from .electrical import ElectricalCapability
from .enabled import EnabledObservation
from .models import ConnectorId, EvseId, PhysicalPhaseObservation, StationId
from .telemetry import STATE_OPTIONS, Channel, Observation, Quantity, State


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
    authority: AuthorityObservation | None = None
    authority_revision: int = 0
    enabled: tuple[EnabledObservation, ...] = ()

    def __post_init__(self):
        for name, expected in (
            ("supported_channels", Channel),
            ("observations", Observation),
            ("physical_phases", PhysicalPhaseObservation),
            ("electrical", ElectricalCapability),
            ("enabled", EnabledObservation),
        ):
            items = tuple(getattr(self, name))
            if any(not isinstance(item, expected) for item in items):
                raise ValueError("invalid telemetry snapshot")
            object.__setattr__(self, name, items)

    def observation(self, channel: Channel) -> Observation | None:
        return next((o for o in self.observations if o.channel == channel), None)


def control_view(snapshot):
    """Control values/validity, distinct from acquisition and deadline refreshes."""
    now = datetime.now(UTC)
    return (
        snapshot.token,
        snapshot.connected,
        snapshot.identity,
        snapshot.connectors,
        snapshot.capabilities,
        snapshot.charging_schedule,
        snapshot.discovery,
        snapshot.electrical,
        snapshot.authority,
        snapshot.authority_revision,
        tuple(
            (o.scope, o.enabled, o.revision, o.observed_at <= now < o.valid_until)
            for o in snapshot.enabled
        ),
        tuple((o.scope, o.mode, o.fresh(now)) for o in snapshot.physical_phases),
        tuple(t for t in snapshot.connectors if actively_charging(snapshot, t)),
        tuple(
            (o.channel, o.value, o.fresh(now))
            for o in snapshot.observations
            if o.channel.quantity in STATE_OPTIONS
            or o.channel.quantity.value.startswith("voltage_")
        ),
    )


def actively_charging(state, target):
    """Require actual positive flow; an idle relay or a desired target is no proof."""
    charging = state.observation(Channel(target, Quantity.CHARGING_STATE))
    if charging is None or charging.value != State.CHARGING:
        return False
    now = datetime.now(UTC)
    samples = [
        o
        for o in state.observations
        if o.channel.scope == target
        and o.channel.quantity
        in (
            Quantity.POWER,
            Quantity.CURRENT_L1,
            Quantity.CURRENT_L2,
            Quantity.CURRENT_L3,
        )
        and o.observed_at <= now
        and o.fresh(now)
    ]
    if not samples:
        return False
    # A newer zero reading supersedes older positive measurements.
    latest = max(o.observed_at for o in samples)
    samples = [o for o in samples if o.observed_at == latest]
    power = next((o for o in samples if o.channel.quantity == Quantity.POWER), None)
    return power.value > 0 if power else any(o.value > 0 for o in samples)
