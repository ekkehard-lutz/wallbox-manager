"""NETZ nominal 230 V technical steps and rounded planning (GRID-31–39)."""

import math
from dataclasses import dataclass
from fractions import Fraction

NOMINAL_VOLTAGE = 230


def number(value):
    if isinstance(value, bool):
        raise ValueError("Numeric value required")
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Finite numeric value required") from exc


def rounded_watts(watts):
    """Exact decimal half-up at 100 W, without a binary-float tolerance."""
    return math.floor(number(watts) / 100 + Fraction(1, 2)) * 100


def rounded_kw(kw):
    return rounded_watts(number(kw) * 1000) / 1000


@dataclass(frozen=True)
class Point:
    amps: int
    phases: int | None
    watts: Fraction

    @property
    def display_watts(self):
        return rounded_watts(self.watts)

    def selection(self):
        return {"power_kw": self.display_watts / 1000, "power_phases": self.phases}


class Envelope:
    def __init__(self, minimum, maximum, asymmetry, step, modes, caps):
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
        self.ranges = {}
        self.migration = {}
        for n in sorted(set(modes)):
            if type(n) is not int or n not in (1, 2, 3):
                raise ValueError("Unsupported phase count")
            ceiling = maximum if n == 3 else min(maximum, asymmetry)
            raw = caps.get(n)
            cap = ceiling if raw is None else number(raw)
            normalized = max(0, min(math.floor(cap), math.floor(ceiling)))
            if normalized < self.minimum:
                normalized = 0
            if cap != normalized:
                self.migration[n] = {"stored": str(cap), "effective": normalized}
            if normalized == 0:
                continue
            cap = self.minimum + ((normalized - self.minimum) // self.step) * self.step
            voltage = n * NOMINAL_VOLTAGE
            self.ranges[n] = (
                voltage,
                rounded_watts(self.minimum * voltage),
                rounded_watts(cap * voltage),
                cap,
            )

    def points(self, mode):
        voltage, _, _, cap = self.ranges[mode]
        return [
            Point(amps, mode, amps * voltage)
            for amps in range(int(self.minimum), int(cap) + 1, int(self.step))
        ]

    def power_bounds(self):
        if not self.ranges:
            return None
        return (
            min(r[1] for r in self.ranges.values()) / 1000,
            max(r[2] for r in self.ranges.values()) / 1000,
        )

    def resolve(self, watts, direction="nearest", ongoing=None, selected=None):
        watts = number(watts)
        if watts < 0 or direction not in ("down", "nearest", "up"):
            raise ValueError("Unsupported power request")
        if watts == 0:
            return Point(0, None, Fraction(0))
        modes = sorted(self.ranges)
        if not modes:
            raise ValueError("All supported phase modes are disabled")
        # A technical selection explicitly identifies one of the overlapping
        # phase points. A stale/invalid hint never invents a now-illegal point.
        if type(selected) is int and selected in modes:
            for point in self.points(selected):
                if point.display_watts == watts:
                    return point
        if (
            ongoing in modes
            and self.ranges[ongoing][1] <= watts <= self.ranges[ongoing][2]
        ):
            mode = ongoing
        else:
            mode = modes[-1]
            for low, high in zip(modes, modes[1:], strict=False):
                if watts <= (self.ranges[low][2] + self.ranges[high][1]) / 2:
                    mode = low
                    break
        return self.on_phase(watts, mode, direction)

    def on_phase(self, watts, mode, direction="nearest"):
        """Approximate explicitly enumerated rounded offers, including zero."""
        watts = number(watts)
        if watts < 0 or direction not in ("down", "nearest", "up"):
            raise ValueError("Unsupported power request")
        if mode not in self.ranges:
            raise ValueError("Phase mode disabled or unsupported")
        points = [Point(0, None, Fraction(0)), *self.points(mode)]
        if watts == 0:
            return points[0]
        if direction == "down":
            return max(
                (p for p in points if p.display_watts <= watts),
                key=lambda p: p.display_watts,
            )
        if direction == "up":
            return next((p for p in points[1:] if p.display_watts >= watts), points[-1])
        return min(
            points, key=lambda p: (abs(p.display_watts - watts), -p.display_watts)
        )

    def interim(self, watts, mode, direction):
        """An interim positive clamp cannot violate the requested direction."""
        if mode not in self.ranges:
            return Point(0, None, 0)
        point = self.on_phase(watts, mode, direction)
        if (
            direction == "up"
            and point.display_watts < watts
            or direction == "down"
            and point.display_watts > watts
        ):
            return Point(0, None, 0)
        return point

    def navigate(self, watts, mode, direction):
        """One strict, monotonic technical step; keep the selected mode first."""
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("Direction must be -1 or 1")
        watts = number(watts)
        same = self.points(mode) if mode in self.ranges else []
        candidates = [p for p in same if (p.display_watts - watts) * direction > 0]
        if not candidates:
            candidates = [
                p
                for n in self.ranges
                if n != mode
                for p in self.points(n)
                if (p.display_watts - watts) * direction > 0
            ]
        return (
            min(candidates, key=lambda p: (abs(p.display_watts - watts), p.phases))
            if candidates
            else None
        )


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
    return Envelope(
        **capabilities(state),
        caps={n: settings.get(f"allowed_current_{n}p") for n in (1, 2, 3)},
    )
