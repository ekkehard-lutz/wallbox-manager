"""Common PV target safety and SoC state policy, independent of power/control."""

from dataclasses import dataclass
from fractions import Fraction

PV_PROFILES = ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")
TARGET_FIELDS = ("soll_soc_speicher", "optimum_lower_soc", "optimum_upper_soc")


def target_bounds(reserve, hysteresis):
    reserve, hysteresis = Fraction(str(reserve)), Fraction(str(hysteresis))
    if not 0 <= reserve <= 100 or not 0 <= hysteresis <= 50:
        raise ValueError("invalid reserve or hysteresis")
    lower, upper = reserve + hysteresis, 100 - hysteresis
    if lower > upper:
        raise ValueError("no safe PV target interval")
    return lower, upper


def clamp_target(value, reserve, hysteresis):
    lower, upper = target_bounds(reserve, hysteresis)
    return min(upper, max(lower, Fraction(str(value))))


def clamp_settings(settings, reserve):
    """Project legacy/current targets into the safe interval; preserve H.

    Clamp each endpoint, then raise the upper endpoint to the lower if inverted.
    An impossible interval raises rather than inventing an unsafe target.
    """
    result = dict(settings)
    for key in TARGET_FIELDS:
        result[key] = float(clamp_target(result[key], reserve, result["soc_hysterese"]))
    result["optimum_upper_soc"] = max(
        result["optimum_lower_soc"], result["optimum_upper_soc"]
    )
    return result


@dataclass(frozen=True)
class SocDecision:
    mode: str
    reason: str
    lower_stop_threshold: Fraction
    pv_start_threshold: Fraction
    fast_start_threshold: Fraction


def soc_policy(
    target, hysteresis, soc, previous="STOP", *, stopped=False, surplus=False
):
    """Exact inclusive L/U and M start boundaries; surplus is start-only evidence."""
    target, hysteresis, soc = map(Fraction, map(str, (target, hysteresis, soc)))
    if (
        not 0 <= soc <= 100
        or hysteresis < 0
        or previous not in ("STOP", "PV_BALANCE", "FAST_DISCHARGE")
    ):
        raise ValueError("invalid SoC policy input")
    lower, middle, upper = (
        target - hysteresis / 2,
        target + hysteresis / 2,
        target + hysteresis,
    )
    prior = "STOP" if stopped else previous
    if soc <= lower:
        mode, reason = "STOP", "lower_soc_protection"
    elif soc >= upper:
        mode, reason = "FAST_DISCHARGE", "fast_start_threshold"
    elif soc < middle:
        mode = "STOP" if prior == "STOP" else "PV_BALANCE"
        reason = "below_pv_start" if mode == "STOP" else "balance_hysteresis"
    elif prior != "STOP":
        mode, reason = prior, "mode_hysteresis"
    elif surplus:
        mode, reason = "PV_BALANCE", "pv_surplus_start"
    else:
        mode, reason = "STOP", "waiting_pv_surplus"
    return SocDecision(mode, reason, lower, middle, upper)
