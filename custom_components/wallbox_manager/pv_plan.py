"""Pure remaining-local-day planner, preserving beta.18's energy calculation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .grid_point import number
from .pv_soc import clamp_target, target_bounds


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
        h = settings["soc_hysterese"]
        lower_bound, _ = target_bounds(reserve, h)
        if profile == "PV_MAXIMUM":
            return lower_bound
        if profile == "PV_SURPLUS":
            return clamp_target(settings["soll_soc_speicher"], reserve, h)
        if forecast is None or capacity is None or capacity <= 0:
            raise ValueError("remaining_pv_energy/storage_capacity")
        lower = clamp_target(settings["optimum_lower_soc"], reserve, h)
        upper = max(lower, clamp_target(settings["optimum_upper_soc"], reserve, h))
        date = now.date().isoformat()
        if self.date != date:
            self.date, self.phase, self.signature = date, "BEFORE_SURPLUS", None
        if self.phase == "BEFORE_SURPLUS" and surplus:
            self.phase = "DYNAMIC"
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
