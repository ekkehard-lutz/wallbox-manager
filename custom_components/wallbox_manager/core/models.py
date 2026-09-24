"""Immutable protocol-independent physical identities and voltage observations."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from .values import generation, nonempty, scalar, timestamp


@dataclass(frozen=True)
class StationId:
    value: str

    def __post_init__(self) -> None:
        nonempty(self.value)


@dataclass(frozen=True)
class EvseId:
    station: StationId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.station, StationId):
            raise ValueError("EVSE requires a station")
        nonempty(self.value)


@dataclass(frozen=True)
class ConnectorId:
    evse: EvseId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.evse, EvseId):
            raise ValueError("connector requires an EVSE")
        nonempty(self.value)

    @property
    def station(self):
        return self.evse.station


type Scope = StationId | EvseId | ConnectorId


class Phase(StrEnum):
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"


@dataclass(frozen=True)
class PhaseMode:
    """Any nonempty subset of L1/L2/L3, including all two-phase mappings."""

    phases: tuple[Phase, ...]

    def __post_init__(self) -> None:
        phases = tuple(self.phases)
        if not phases or any(not isinstance(p, Phase) for p in phases):
            raise ValueError("explicit active phases required")
        if len(set(phases)) != len(phases):
            raise ValueError("duplicate phase")
        object.__setattr__(self, "phases", tuple(sorted(phases)))

    @classmethod
    def canonical(cls, count: int):
        """1p=L1, 2p=L1+L2, 3p=L1+L2+L3 at the EVSE interface.

        These are EVSE-local labels, not installation/grid conductor identities.
        Explicit subsets remain representable for other normalized adapters.
        """
        if type(count) is not int or count not in (1, 2, 3):
            raise ValueError("canonical phase count must be 1, 2 or 3")
        return cls(tuple(Phase)[:count])

    @property
    def count(self) -> int:
        return len(self.phases)


@dataclass(frozen=True)
class PhaseVoltage:
    """Measured RMS phase-to-neutral volts, valid through an explicit deadline.

    Adapters establish topology, freshness and coherence before constructing these
    observations. Line-to-line readings and nominal substitutions are not accepted.
    """

    phase: Phase
    voltage_v: Fraction
    observed_at: datetime
    valid_until: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.phase, Phase):
            raise ValueError("invalid phase")
        object.__setattr__(self, "voltage_v", scalar(self.voltage_v, positive=True))
        timestamp(self.observed_at)
        timestamp(self.valid_until)
        if self.valid_until <= self.observed_at:
            raise ValueError("voltage validity interval must be positive")
        nonempty(self.source)


@dataclass(frozen=True)
class VoltageObservation:
    scope: Scope
    connection_generation: int
    phases: tuple[PhaseVoltage, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, (StationId, EvseId, ConnectorId)):
            raise ValueError("invalid observation scope")
        generation(self.connection_generation)
        phases = tuple(self.phases)
        if any(not isinstance(p, PhaseVoltage) for p in phases):
            raise ValueError("invalid voltage sample")
        if len({p.phase for p in phases}) != len(phases):
            raise ValueError("duplicate voltage phase")
        object.__setattr__(self, "phases", phases)

    def active_voltages(
        self, mode: PhaseMode, now: datetime
    ) -> tuple[Fraction, ...] | None:
        """Return only a complete fresh basis; never fill absent phases."""
        samples = {
            p.phase: p.voltage_v
            for p in self.phases
            if p.observed_at <= now < p.valid_until
        }
        if any(p not in samples for p in mode.phases):
            return None
        return tuple(samples[p] for p in mode.phases)


@dataclass(frozen=True)
class PhysicalPhaseObservation:
    """Hardware-backed conductor state; unknown is explicit, never desired state."""

    scope: ConnectorId
    mode: PhaseMode | None
    observed_at: datetime
    valid_until: datetime
    source: str

    def __post_init__(self):
        if not isinstance(self.scope, ConnectorId):
            raise ValueError("physical phase state requires connector scope")
        if self.mode is not None and not isinstance(self.mode, PhaseMode):
            raise ValueError("invalid physical phase state")
        timestamp(self.observed_at)
        timestamp(self.valid_until)
        nonempty(self.source)
        if self.valid_until <= self.observed_at:
            raise ValueError("physical phase evidence requires a positive lifetime")

    def fresh(self, now):
        return self.observed_at <= now < self.valid_until
