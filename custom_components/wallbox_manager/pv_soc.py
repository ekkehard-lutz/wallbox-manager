"""Shared exact SoC policy; beta.18 target bounds with the revised S/L/M/U matrix."""

from dataclasses import dataclass
from fractions import Fraction

from .grid_point import number

PV_PROFILES = ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")


def target_bounds(reserve, hysteresis):
    reserve, hysteresis = map(number, (reserve, hysteresis))
    if not 0 <= reserve <= 100 or not 0 <= hysteresis <= 50:
        raise ValueError("invalid reserve or hysteresis")
    lower, upper = reserve + hysteresis, 100 - hysteresis
    if lower > upper:
        raise ValueError("no permitted PV target interval")
    return lower, upper


def clamp_target(value, reserve, hysteresis):
    lower, upper = target_bounds(reserve, hysteresis)
    return min(upper, max(lower, number(value)))


@dataclass(frozen=True)
class SocDecision:
    mode: str
    reason: str
    stop: Fraction
    low: Fraction
    middle: Fraction
    upper: Fraction


def soc_policy(target, hysteresis, soc, previous="STOP", *, surplus=False):
    target, hysteresis, soc = map(number, (target, hysteresis, soc))
    if (
        not 0 <= soc <= 100
        or hysteresis < 0
        or previous not in ("STOP", "BALANCE", "FAST")
    ):
        raise ValueError("invalid SoC policy input")
    s, low, m, u = (
        target - hysteresis,
        target - hysteresis / 2,
        target + hysteresis / 2,
        target + hysteresis,
    )
    if soc < s:
        mode, reason = "STOP", "soc_lower_bound"
    elif soc >= u:
        mode, reason = "FAST", "soc_upper_threshold"
    elif previous == "STOP":
        mode, reason = (
            ("BALANCE", "pv_surplus_start")
            if soc > m and surplus
            else ("STOP", "soc_restart_inhibited")
        )
    elif previous == "FAST" and soc < m:
        mode, reason = "BALANCE", "soc_balance_threshold"
    else:
        mode, reason = previous, "soc_hysteresis"
    return SocDecision(mode, reason, s, low, m, u)
