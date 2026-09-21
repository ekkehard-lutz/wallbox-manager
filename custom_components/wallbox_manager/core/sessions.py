"""Protocol-independent immutable transaction lifecycle and meter accounting."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from .models import ConnectorId, EvseId
from .telemetry import STATE_OPTIONS, Observation, Quantity, State
from .values import nonempty, scalar, timestamp


class SessionEventKind(StrEnum):
    STARTED = "started"
    UPDATED = "updated"
    ENDED = "ended"


@dataclass(frozen=True)
class SessionEvent:
    scope: EvseId | ConnectorId
    external_transaction_id: str
    kind: SessionEventKind
    at: datetime
    charging_state: State | None = None
    end_reason: str | None = None
    sequence: int | None = None
    meter_wh: Fraction | None = None

    def __post_init__(self):
        if not isinstance(self.scope, (EvseId, ConnectorId)):
            raise ValueError("session requires an EVSE or connector")
        nonempty(self.external_transaction_id)
        timestamp(self.at)
        if not isinstance(self.kind, SessionEventKind):
            raise ValueError("invalid lifecycle kind")
        if self.charging_state is not None and (
            not isinstance(self.charging_state, State)
            or self.charging_state not in STATE_OPTIONS[Quantity.CHARGING_STATE]
        ):
            raise ValueError("invalid charging state")
        if self.sequence is not None and (
            type(self.sequence) is not int or self.sequence < 0
        ):
            raise ValueError("invalid sequence")
        if self.meter_wh is not None:
            object.__setattr__(self, "meter_wh", scalar(self.meter_wh))


@dataclass(frozen=True)
class ChargingSession:
    session_id: str
    external_transaction_id: str
    scope: EvseId | ConnectorId
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    energy_start_wh: Fraction | None = None
    energy_end_wh: Fraction | None = None
    energy_charged_wh: Fraction | None = None
    current_power_w: Fraction | None = None
    max_power_w: Fraction | None = None
    charging_state: State = State.UNKNOWN
    end_reason: str | None = None
    sequence: int | None = None
    energy_at: datetime | None = None
    power_at: datetime | None = None
    state_at: datetime | None = None
    power_valid_until: datetime | None = None
    energy_invalid: bool = False
    start_known: bool = True

    def __post_init__(self):
        nonempty(self.session_id)
        nonempty(self.external_transaction_id)
        if not isinstance(self.scope, (EvseId, ConnectorId)):
            raise ValueError("session requires a scoped identity")
        for at in (
            self.started_at,
            self.updated_at,
            self.ended_at,
            self.energy_at,
            self.power_at,
            self.state_at,
            self.power_valid_until,
        ):
            if at is not None:
                timestamp(at)
        if self.updated_at < self.started_at or (
            self.ended_at is not None and self.ended_at != self.updated_at
        ):
            raise ValueError("invalid lifecycle interval")
        if self.ended_at is not None and self.current_power_w != 0:
            raise ValueError("completed session power must be zero")
        if (
            not isinstance(self.charging_state, State)
            or self.charging_state not in STATE_OPTIONS[Quantity.CHARGING_STATE]
        ):
            raise ValueError("invalid charging state")
        for key in (
            "energy_start_wh",
            "energy_end_wh",
            "energy_charged_wh",
            "current_power_w",
            "max_power_w",
        ):
            value = getattr(self, key)
            if value is not None:
                object.__setattr__(self, key, scalar(value))
        if self.energy_charged_wh is not None and (
            self.energy_invalid
            or self.energy_start_wh is None
            or self.energy_end_wh is None
            or self.energy_charged_wh != self.energy_end_wh - self.energy_start_wh
        ):
            raise ValueError("invalid energy delta")

    @property
    def station_id(self):
        return self.evse_id.station

    @property
    def evse_id(self):
        return self.scope.evse if isinstance(self.scope, ConnectorId) else self.scope

    @property
    def connector_id(self):
        return self.scope if isinstance(self.scope, ConnectorId) else None

    @property
    def active(self):
        return self.ended_at is None

    def duration(self, now: datetime) -> float:
        timestamp(now)
        return max(0, ((self.ended_at or now) - self.started_at).total_seconds())

    def meter(self, value, at):
        """A reset or conflicting reading makes accounting unknown permanently."""
        if at < self.started_at or (self.energy_at is not None and at < self.energy_at):
            return self
        if self.energy_at == at and self.energy_end_wh == value:
            return self
        invalid = self.energy_invalid or value is None
        if self.energy_at == at and self.energy_end_wh != value:
            invalid = True
        if (
            value is not None
            and self.energy_end_wh is not None
            and value < self.energy_end_wh
        ):
            invalid = True
        start = self.energy_start_wh
        if self.start_known and self.energy_at is None and at == self.started_at:
            start = value
        delta = None if invalid or start is None or value is None else value - start
        return replace(
            self,
            energy_start_wh=start,
            energy_end_wh=value,
            energy_charged_wh=delta,
            energy_at=at,
            energy_invalid=invalid,
        )

    def observe(self, observation: Observation):
        if (
            not self.active
            or observation.channel.scope != self.scope
            or observation.observed_at < self.started_at
        ):
            return self
        at, value = observation.observed_at, observation.value
        match observation.channel.quantity:
            case Quantity.ENERGY:
                return self.meter(value, at)
            case Quantity.POWER:
                if self.power_at is not None and at < self.power_at:
                    return self
                if self.power_at == at and value != self.current_power_w:
                    value = None
                maximum = self.max_power_w
                if value is not None:
                    maximum = max(maximum or Fraction(0), value)
                return replace(
                    self,
                    current_power_w=value,
                    max_power_w=maximum,
                    power_at=at,
                    power_valid_until=observation.valid_until,
                )
            case Quantity.CHARGING_STATE:
                if value is not None and (self.state_at is None or at > self.state_at):
                    return replace(self, charging_state=value, state_at=at)
        return self
