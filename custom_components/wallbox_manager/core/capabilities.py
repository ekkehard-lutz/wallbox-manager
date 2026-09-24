"""Versioned discovery evidence and independent physical charging envelopes."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from .models import ConnectorId, EvseId, PhaseMode, StationId
from .values import generation, nonempty, scalar, timestamp


class EvidenceState(StrEnum):
    UNKNOWN = "unknown"
    ADVERTISED = "advertised"
    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class CapabilityEvidence:
    state: EvidenceState
    source: str
    observed_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, EvidenceState):
            raise ValueError("invalid evidence state")
        nonempty(self.source)
        timestamp(self.observed_at)
        if self.reason is not None:
            nonempty(self.reason)


@dataclass(frozen=True)
class CapabilityOverride:
    """Configuration intent is separate from evidence and never grants support."""

    capability: str
    enabled: bool
    source: str
    reason: str

    def __post_init__(self) -> None:
        for value in (self.capability, self.source, self.reason):
            nonempty(value)
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be boolean")


@dataclass(frozen=True)
class ChargingEnvelope:
    """Reachable currents are min_current_a + n * current_step_a, n >= 0.

    This describes what the wallbox can safely offer, not EV consumption.
    max_current_a is a ceiling; it need not itself lie on the current grid.
    """

    mode: PhaseMode
    min_current_a: Fraction
    max_current_a: Fraction
    current_step_a: Fraction
    evidence: CapabilityEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PhaseMode):
            raise ValueError("invalid phase mode")
        if not isinstance(self.evidence, CapabilityEvidence):
            raise ValueError("invalid envelope evidence")
        for name in ("min_current_a", "max_current_a", "current_step_a"):
            object.__setattr__(self, name, scalar(getattr(self, name), positive=True))
        if self.min_current_a > self.max_current_a:
            raise ValueError("minimum exceeds maximum")


@dataclass(frozen=True)
class CurrentLimit:
    """Additional mode-specific site/station/session interval on the device grid.

    A zero maximum inhibits charging in this mode. Limits do not grant new modes.
    The coordinator supplies the remaining shared budget, not aggregate capacity.
    Temporary EV-acceptance constraints may be supplied explicitly by a future
    controller; they never modify wallbox capability evidence. The caller owns
    their session lifetime, freshness and removal, separately for each mode.
    """

    mode: PhaseMode
    min_current_a: Fraction
    max_current_a: Fraction
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PhaseMode):
            raise ValueError("invalid limit mode")
        for name in ("min_current_a", "max_current_a"):
            object.__setattr__(self, name, scalar(getattr(self, name)))
        if self.min_current_a > self.max_current_a:
            raise ValueError("minimum exceeds maximum")
        nonempty(self.source)


@dataclass(frozen=True)
class CapabilitySnapshot:
    scope: StationId | EvseId | ConnectorId
    firmware: str | None
    connection_generation: int
    boot_generation: int
    revision: int
    observed_at: datetime
    source: str
    envelopes: tuple[ChargingEnvelope, ...]
    stop: CapabilityEvidence
    overrides: tuple[CapabilityOverride, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope, (StationId, EvseId, ConnectorId)):
            raise ValueError("capabilities require station, EVSE or connector scope")
        if self.firmware is not None:
            nonempty(self.firmware)
        for value in (self.connection_generation, self.boot_generation, self.revision):
            generation(value)
        timestamp(self.observed_at)
        nonempty(self.source)
        if not isinstance(self.stop, CapabilityEvidence):
            raise ValueError("invalid stop evidence")
        envelopes = tuple(self.envelopes)
        overrides = tuple(self.overrides)
        if any(not isinstance(e, ChargingEnvelope) for e in envelopes):
            raise ValueError("invalid charging envelope")
        if len({e.mode for e in envelopes}) != len(envelopes):
            raise ValueError("duplicate envelope mode")
        if any(not isinstance(o, CapabilityOverride) for o in overrides):
            raise ValueError("invalid override")
        object.__setattr__(self, "envelopes", envelopes)
        object.__setattr__(self, "overrides", overrides)
