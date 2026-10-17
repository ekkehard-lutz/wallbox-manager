"""Small OCPP2.1 meter normalizer extracted from beta.18 parsing rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

UNITS = {
    "Voltage": {"V": 1, "mV": 0.001, "kV": 1000},
    "Current.Import": {"A": 1, "mA": 0.001},
    "Power.Active.Import": {"W": 1, "kW": 1000, "MW": 1000000},
    "Energy.Active.Import.Register": {"Wh": 0.001, "kWh": 1, "MWh": 1000},
}
CONNECTOR = {
    "Available": "available",
    "Occupied": "occupied",
    "Reserved": "reserved",
    "Unavailable": "unavailable",
    "Faulted": "faulted",
}
CHARGING = {
    "Idle": "idle",
    "EVConnected": "connected",
    "Charging": "charging",
    "SuspendedEV": "suspended_vehicle",
    "SuspendedEVSE": "suspended_station",
}


def reported_time(value, received):
    try:
        at = datetime.fromisoformat(value)
        return (
            at.astimezone(UTC)
            if at.utcoffset() is not None and at <= received + timedelta(seconds=5)
            else None
        )
    except ValueError, TypeError, OverflowError:
        return None


def samples(groups, *, session=False):
    received = datetime.now(UTC)
    found = {}
    for group in groups or ():
        at = reported_time(group.get("timestamp"), received)
        if at is None:
            continue
        for sample in group.get("sampled_value", ()):
            measurand = sample.get("measurand", "Energy.Active.Import.Register")
            phase = sample.get("phase")
            prefix = {
                "Voltage": "voltage",
                "Current.Import": "current",
                "Power.Active.Import": "power",
            }.get(measurand)
            if phase in ("L1-N", "L2-N", "L3-N") and measurand == "Voltage":
                phase = phase[:2]
            role = (
                f"{prefix}_{phase.lower()}"
                if prefix and phase in ("L1", "L2", "L3")
                else "energy"
                if measurand == "Energy.Active.Import.Register" and phase is None
                else "power"
                if measurand == "Power.Active.Import" and phase is None
                else None
            )
            context = sample.get("context", "Sample.Periodic")
            if (
                role is None
                or sample.get("location", "Outlet") != "Outlet"
                or context
                not in (
                    "Sample.Periodic",
                    "Sample.Clock",
                    "Trigger",
                    "Transaction.Begin",
                    "Transaction.End",
                )
            ):
                continue
            if not session and role == "energy" and context.startswith("Transaction."):
                continue
            value = None
            info = sample.get("unit_of_measure", {})
            unit, multiplier = info.get("unit", "Wh"), info.get("multiplier", 0)
            raw = sample.get("value")
            factor = UNITS[measurand].get(unit)
            if (
                sample.get("format", "Raw") == "Raw"
                and factor is not None
                and type(multiplier) is int
                and -12 <= multiplier <= 12
                and type(raw) in (int, float)
            ):
                try:
                    decimal = Decimal(str(raw))
                    if (
                        decimal.is_finite()
                        and -30 <= decimal.adjusted() <= 30
                        and len(decimal.as_tuple().digits) <= 40
                    ):
                        number = float(
                            decimal * Decimal(str(factor)) * Decimal(10) ** multiplier
                        )
                        if 0 <= number <= 10**18:
                            value = number
                except ValueError, OverflowError, InvalidOperation:
                    pass
            key = (role, at)
            if key in found and found[key] != value:
                value = None
            found[key] = value
    for (role, at), value in found.items():
        yield (
            role,
            value,
            at,
            {"energy": "kWh", "power": "W", "voltage": "V", "current": "A"}[
                role.split("_")[0]
            ],
        )
