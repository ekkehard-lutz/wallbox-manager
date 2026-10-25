"""Pure remaining-local-day planner, preserving beta.18's energy calculation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .grid_point import number
from .pv_soc import clamp_target, target_bounds


def strict_surplus(production, total_consumption, ev_power=None):
    """Prove surplus from total load, or subtract a genuinely observed EV load."""
    if production is None or total_consumption is None:
        return False
    if production > total_consumption:
        return True  # Household excluding a nonnegative EV cannot exceed total load.
    return ev_power is not None and production > max(0, total_consumption - ev_power)


def validate_target_inputs(profile, settings, reserve, *, forecast=None, capacity=None):
    """Validate calculation dependencies without running the daily planner."""
    lower, upper = target_bounds(reserve, settings["soc_hysterese"])
    if profile == "PV_SURPLUS":
        if not lower <= number(settings["soll_soc_speicher"]) <= upper:
            raise ValueError("surplus_target_outside_interval")
    elif profile == "PV_OPTIMUM":
        if forecast is None or capacity is None or capacity <= 0:
            raise ValueError("remaining_pv_energy/storage_capacity")
    return lower, upper


def remaining_day_seconds(now):
    midnight = datetime.combine(
        now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo
    )
    return number((midnight.astimezone(UTC) - now.astimezone(UTC)).total_seconds())


def remaining_house_energy(daily_kwh, remaining_seconds):
    return number(daily_kwh) * 1000 * max(0, number(remaining_seconds)) / 86400


def target_soc(lower, upper, capacity_wh, remaining_pv_wh, house_wh):
    lower, upper, capacity_wh, remaining_pv_wh, house_wh = map(
        number, (lower, upper, capacity_wh, remaining_pv_wh, house_wh)
    )
    if not 0 <= lower <= upper <= 100 or capacity_wh <= 0:
        raise ValueError("invalid Optimum limits or capacity")
    return min(
        upper, max(lower, upper - 100 * (remaining_pv_wh - house_wh) / capacity_wh)
    )


@dataclass
class Planner:
    date: str | None = None
    phase: str = "BEFORE_SURPLUS"
    planned_at: float | None = None
    signature: tuple | None = None
    target: object = None

    def saved(self):
        return {"version": 1, "date": self.date, "phase": self.phase}

    @classmethod
    def restore(cls, saved):
        if (
            isinstance(saved, dict)
            and saved.get("version") == 1
            and saved.get("phase") in ("BEFORE_SURPLUS", "DYNAMIC", "FINISHED")
        ):
            return cls(saved.get("date"), saved["phase"])
        return cls()

    def calculate(
        self,
        profile,
        settings,
        reserve,
        now,
        elapsed,
        *,
        surplus=False,
        forecast=None,
        capacity=None,
    ):
        if profile == "PV_OPTIMUM":
            # The local day and actual first-surplus observation are independent
            # of release and of whether a forecast currently permits calculating T.
            date = now.date().isoformat()
            if self.date != date:
                self.date, self.phase, self.signature = date, "BEFORE_SURPLUS", None
            if self.phase == "BEFORE_SURPLUS" and surplus:
                self.phase = "DYNAMIC"
        h = settings["soc_hysterese"]
        lower_bound, _ = validate_target_inputs(
            profile, settings, reserve, forecast=forecast, capacity=capacity
        )
        if profile == "PV_MAXIMUM":
            return lower_bound
        if profile == "PV_SURPLUS":
            return number(settings["soll_soc_speicher"])
        lower = clamp_target(settings["optimum_lower_soc"], reserve, h)
        upper = max(lower, clamp_target(settings["optimum_upper_soc"], reserve, h))
        house = remaining_house_energy(
            settings["estimated_daily_house_consumption_kwh"],
            remaining_day_seconds(now),
        )
        if self.phase == "DYNAMIC" and forecast <= house:
            self.phase = "FINISHED"
        if self.phase != "DYNAMIC":
            self.target = upper
        else:
            signature = (
                date,
                lower,
                upper,
                forecast,
                capacity,
                settings["estimated_daily_house_consumption_kwh"],
            )
            if (
                signature != self.signature
                or self.planned_at is None
                or elapsed - self.planned_at >= 300
            ):
                self.target = target_soc(lower, upper, capacity, forecast, house)
                self.planned_at, self.signature = elapsed, signature
        return self.target
