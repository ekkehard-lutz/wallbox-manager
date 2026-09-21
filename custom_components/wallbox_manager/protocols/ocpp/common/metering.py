"""OCPP normalization into immutable observations, never HA metrics.

Sample buckets, unit/multiplier normalization and phase selection adapted from
lbbrhzn/ocpp chargepoint.py process_measurands/process_phases and ocppv201.py
_set_meter_values at 848407c11ff659ce59779a99ce69984bbb0e3ce1.
Copyright (c) 2021 lbbrhzn, MIT; see ../../../THIRD_PARTY_NOTICES.md.
No averaging, phase summation, connector flattening or inferred values is adopted.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from ....core.telemetry import Channel, Observation, Quantity, State

METER_MAX_AGE = timedelta(seconds=120)
CLOCK_TOLERANCE = timedelta(seconds=5)
UNITS = {
    "Voltage": {"V": 1, "mV": Fraction(1, 1000), "kV": 1000},
    "Current.Import": {"A": 1, "mA": Fraction(1, 1000)},
    "Power.Active.Import": {"W": 1, "kW": 1000, "MW": 1000000},
    "Energy.Active.Import.Register": {"Wh": 1, "kWh": 1000, "MWh": 1000000},
}
PREFIXES = {
    "Voltage": "voltage",
    "Current.Import": "current",
    "Power.Active.Import": "power",
}
CONNECTOR_STATES = {
    "Available": State.AVAILABLE,
    "Occupied": State.OCCUPIED,
    "Reserved": State.RESERVED,
    "Unavailable": State.UNAVAILABLE,
    "Faulted": State.FAULTED,
}
CHARGING_STATES = {
    "Idle": State.IDLE,
    "EVConnected": State.CONNECTED,
    "Charging": State.CHARGING,
    "SuspendedEV": State.SUSPENDED_VEHICLE,
    "SuspendedEVSE": State.SUSPENDED_STATION,
}
STATUS16_CHARGING = {
    **CHARGING_STATES,
    "Available": State.IDLE,
    "Preparing": State.PREPARING,
    "Finishing": State.FINISHING,
}


def reported_time(value, received_at):
    """Reject absent, naive or implausibly future source timestamps."""
    try:
        observed = datetime.fromisoformat(value)
        if observed.utcoffset() is None or observed > received_at + CLOCK_TOLERANCE:
            return None
        return observed.astimezone(UTC)
    except TypeError, ValueError, OverflowError:
        return None


def quantity_for(sample):
    measurand = sample.get("measurand", "Energy.Active.Import.Register")
    phase = sample.get("phase")
    if measurand == "Energy.Active.Import.Register" and phase is None:
        return Quantity.ENERGY
    if measurand == "Power.Active.Import" and phase is None:
        return Quantity.POWER
    if measurand == "Voltage" and phase in ("L1-N", "L2-N", "L3-N"):
        phase = phase[:2]
    if measurand in PREFIXES and phase in ("L1", "L2", "L3"):
        return Quantity(f"{PREFIXES[measurand]}_{phase.lower()}")
    return None


def normalized_value(sample, legacy):
    measurand = sample.get("measurand", "Energy.Active.Import.Register")
    if sample.get("location", "Outlet") != "Outlet":
        return None
    context = sample.get("context", "Sample.Periodic")
    if context not in (
        "Sample.Periodic",
        "Sample.Clock",
        "Trigger",
        "Transaction.Begin",
        "Transaction.End",
    ):
        return None
    # A transaction-boundary register may be session-relative on real devices.
    if measurand == "Energy.Active.Import.Register" and context.startswith(
        "Transaction."
    ):
        return None
    if sample.get("format", "Raw") != "Raw":
        return None
    unit_info = (
        {"unit": sample.get("unit", "Wh")}
        if legacy
        else sample.get("unit_of_measure", {})
    )
    if not isinstance(unit_info, dict):
        return None
    multiplier = unit_info.get("multiplier", 0)
    unit = unit_info.get("unit", "Wh")
    if type(multiplier) is not int or not -12 <= multiplier <= 12:
        return None
    factor = UNITS.get(measurand, {}).get(unit)
    if factor is None:
        return None
    value = sample.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not legacy:
        return None
    try:
        numeric = Decimal(str(value))
        if (
            not numeric.is_finite()
            or not -30 <= numeric.adjusted() <= 30
            or len(numeric.as_tuple().digits) > 40
        ):
            return None
        value = Fraction(numeric) * factor * Fraction(10) ** multiplier
        if value < 0 or value > 10**18:
            return None
        return value
    except ValueError, OverflowError, ZeroDivisionError, InvalidOperation:
        return None


def meter_observations(scope, groups, source, *, legacy=False, received_at=None):
    received = received_at or datetime.now(UTC)
    observations = {}
    for group in groups or ():
        if not isinstance(group, dict):
            continue
        observed = reported_time(group.get("timestamp"), received)
        if observed is None:
            continue
        for sample in group.get("sampled_value", ()):
            if not isinstance(sample, dict):
                continue
            quantity = quantity_for(sample)
            if quantity is None:
                continue
            channel = Channel(scope, quantity)
            value = normalized_value(sample, legacy)
            key = (channel, observed)
            if key in observations and observations[key].value != value:
                value = None  # Conflicting samples never depend on wire ordering.
            observations[key] = Observation(
                channel,
                value,
                observed,
                received,
                min(observed + METER_MAX_AGE, received + METER_MAX_AGE),
                source,
            )
    return tuple(observations.values())


def state_observation(scope, quantity, state, timestamp, source, *, received_at=None):
    received = received_at or datetime.now(UTC)
    observed = reported_time(timestamp, received)
    if observed is None:
        return ()
    return (
        Observation(Channel(scope, quantity), state, observed, received, None, source),
    )
