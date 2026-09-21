"""Immutable, scoped observations; no wire or Home Assistant types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fractions import Fraction

from .models import ConnectorId, EvseId, Scope, StationId
from .values import nonempty, scalar, timestamp


class Quantity(StrEnum):
    VOLTAGE_L1 = "voltage_l1"
    VOLTAGE_L2 = "voltage_l2"
    VOLTAGE_L3 = "voltage_l3"
    CURRENT_L1 = "current_l1"
    CURRENT_L2 = "current_l2"
    CURRENT_L3 = "current_l3"
    POWER_L1 = "power_l1"
    POWER_L2 = "power_l2"
    POWER_L3 = "power_l3"
    POWER = "power"
    ENERGY = "energy"
    CONNECTOR_STATE = "connector_state"
    CHARGING_STATE = "charging_state"


class State(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    RESERVED = "reserved"
    UNAVAILABLE = "unavailable"
    FAULTED = "faulted"
    IDLE = "idle"
    CONNECTED = "connected"
    PREPARING = "preparing"
    CHARGING = "charging"
    SUSPENDED_VEHICLE = "suspended_vehicle"
    SUSPENDED_STATION = "suspended_station"
    FINISHING = "finishing"


STATE_OPTIONS = {
    Quantity.CONNECTOR_STATE: (
        State.UNKNOWN,
        State.AVAILABLE,
        State.OCCUPIED,
        State.RESERVED,
        State.UNAVAILABLE,
        State.FAULTED,
    ),
    Quantity.CHARGING_STATE: (
        State.UNKNOWN,
        State.IDLE,
        State.CONNECTED,
        State.PREPARING,
        State.CHARGING,
        State.SUSPENDED_VEHICLE,
        State.SUSPENDED_STATION,
        State.FINISHING,
    ),
}


def station_of(scope: Scope) -> StationId:
    if isinstance(scope, ConnectorId):
        return scope.evse.station
    if isinstance(scope, EvseId):
        return scope.station
    if isinstance(scope, StationId):
        return scope
    raise ValueError("invalid observation scope")


@dataclass(frozen=True)
class Channel:
    scope: Scope
    quantity: Quantity

    def __post_init__(self):
        station_of(self.scope)
        if not isinstance(self.quantity, Quantity):
            raise ValueError("invalid quantity")


@dataclass(frozen=True)
class Observation:
    """Numbers are V, A, W or Wh. None is invalid, never a fabricated zero.

    A missing deadline denotes an event-driven state, valid only in its runtime
    generation. Meter observations always have a deadline for time-bounded accounting.
    Consumers requiring a recent sample must call fresh(); HA live measurement
    availability instead follows the current connected runtime generation.
    Report/receipt times remain independently visible.
    """

    channel: Channel
    value: Fraction | State | None
    observed_at: datetime
    received_at: datetime
    valid_until: datetime | None
    source: str

    def __post_init__(self):
        if not isinstance(self.channel, Channel):
            raise ValueError("invalid channel")
        timestamp(self.observed_at)
        timestamp(self.received_at)
        nonempty(self.source)
        options = STATE_OPTIONS.get(self.channel.quantity)
        if options is not None:
            if self.value is not None and (
                not isinstance(self.value, State) or self.value not in options
            ):
                raise ValueError("invalid state for channel")
        else:
            if self.valid_until is None:
                raise ValueError("meter observation requires a deadline")
            if self.value is not None:
                object.__setattr__(self, "value", scalar(self.value))
        if self.valid_until is not None:
            timestamp(self.valid_until)
            if self.valid_until < self.observed_at:
                raise ValueError("deadline precedes observation")

    def fresh(self, now: datetime) -> bool:
        timestamp(now)
        return self.value is not None and (
            self.valid_until is None or now < self.valid_until
        )


def state_flag(value: State | None, flag: str) -> bool | None:
    """Do not turn absent or indeterminate protocol state into false."""
    if value is None or value == State.UNKNOWN:
        return None
    if flag == "available":
        return value == State.AVAILABLE
    if flag == "occupied":
        return {State.AVAILABLE: False, State.OCCUPIED: True}.get(value)
    if flag == "vehicle_connected":
        if value in (State.PREPARING, State.FINISHING):
            return None  # Connector occupancy does not establish physical EV presence.
        return value != State.IDLE
    if flag == "charging_active":
        return value == State.CHARGING
    raise ValueError("unknown state flag")
