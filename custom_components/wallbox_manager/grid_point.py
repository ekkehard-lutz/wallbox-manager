"""NETZ electrical planning, independent of measured vehicle draw (GRID-03–10)."""

import math
from dataclasses import dataclass
from fractions import Fraction


def number(value):
    if isinstance(value, bool):
        raise ValueError("Numeric value required")
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Finite numeric value required") from exc


@dataclass(frozen=True)
class Point:
    amps: int
    phases: int | None
    watts: Fraction


class Envelope:
    def __init__(self, minimum, maximum, asymmetry, step, modes, caps, voltages):
        self.minimum, maximum, asymmetry, self.step = map(
            number, (minimum, maximum, asymmetry, step)
        )
        if (
            self.minimum < 1
            or self.minimum.denominator != 1
            or self.step < 1
            or self.step.denominator != 1
            or maximum < self.minimum
            or asymmetry <= 0
        ):
            raise ValueError("Unsupported current diagnostics")
        self.voltages = tuple(map(number, voltages))
        self.ranges = {}
        self.migration = {}
        for n in sorted(set(modes)):
            if type(n) is not int or n not in (1, 2, 3):
                raise ValueError("Unsupported phase count")
            ceiling = maximum if n == 3 else min(maximum, asymmetry)
            raw = caps.get(n)
            cap = ceiling if raw is None else number(raw)
            # Legacy fractions/out-of-range caps can only decrease. Keep the raw
            # store intact and expose the normalization; never silently raise it.
            normalized = max(0, min(math.floor(cap), math.floor(ceiling)))
            if normalized < self.minimum:
                normalized = 0
            if cap != normalized:
                self.migration[n] = {"stored": str(cap), "effective": normalized}
            if normalized == 0:
                continue
            cap = self.minimum + ((normalized - self.minimum) // self.step) * self.step
            voltage = sum(self.voltages[:n])
            if voltage <= 0:
                raise ValueError("Invalid voltage basis")
            self.ranges[n] = (voltage, self.minimum * voltage, cap * voltage, cap)

    def resolve(self, watts, direction="nearest", ongoing=None):
        watts = number(watts)
        if watts < 0 or direction not in ("down", "nearest", "up"):
            raise ValueError("Unsupported power request")
        if watts == 0:
            return Point(0, None, Fraction(0))
        modes = sorted(self.ranges)
        if not modes:
            raise ValueError("All supported phase modes are disabled")
        if (
            ongoing in modes
            and self.ranges[ongoing][1] <= watts <= self.ranges[ongoing][2]
        ):
            mode = ongoing
        else:
            # Ascending first-crossing also defines non-monotonic boundaries:
            # choose the first lower mode satisfying its boundary, else highest.
            mode = modes[-1]
            for low, high in zip(modes, modes[1:], strict=False):
                if watts <= (self.ranges[low][2] + self.ranges[high][1]) / 2:
                    mode = low
                    break
        voltage, _, _, cap = self.ranges[mode]
        index = (watts / voltage - self.minimum) / self.step
        index = (
            math.floor(index)
            if direction == "down"
            else math.ceil(index)
            if direction == "up"
            else math.floor(index + Fraction(1, 2))
        )
        amps = int(max(self.minimum, min(cap, self.minimum + index * self.step)))
        return Point(amps, mode, amps * voltage)


def capabilities(state):
    station, evse, connector = state.binding

    def get(role, *, scoped=False):
        return state.value(
            ("capability", (station, evse, connector if scoped else None), role)
        )

    modes = get("supported_phases")
    if not isinstance(modes, str):
        raise ValueError("Missing supported phases")
    return dict(
        minimum=get("minimum_current"),
        maximum=get("maximum_current", scoped=True),
        asymmetry=get("maximum_asymmetry_current", scoped=True),
        step=get("current_step"),
        modes=tuple(map(int, modes.split(","))),
    )


def envelope(state, settings):
    # One consistent basis: all valid observed phases, otherwise all nominal 230 V.
    station, evse, _ = state.binding
    voltages = [
        state.value(("observation", (station, evse, None), f"voltage_l{n}"))
        for n in (1, 2, 3)
    ]
    if any(v is None or not 100 <= number(v) <= 300 for v in voltages):
        voltages = [230, 230, 230]
    return Envelope(
        **capabilities(state),
        caps={n: settings.get(f"allowed_current_{n}p") for n in (1, 2, 3)},
        voltages=voltages,
    )
