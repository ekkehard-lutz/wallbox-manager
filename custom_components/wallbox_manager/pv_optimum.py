"""Independent Optimum SoC policy and PV-day observation, never device control."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from homeassistant.util import dt as dt_util

from .control.requests import Direction
from .pv_regulators import FastDischargeRegulator

OPTIMUM_DEFAULTS = {
    "optimum_lower_soc": 20,
    "optimum_upper_soc": 80,
    "optimum_max_discharge_w": 0,
    "estimated_daily_house_consumption_kwh": 0,
}
OPTIMUM_REFERENCES = (
    "soc_speicher_aktuell",
    "leistung_pv",
    "leistung_verbraucher",
    "storage_discharge_power",
    "storage_capacity",
    "remaining_pv_energy",
    "grid_import_power",
    "grid_export_power",
)


def remaining_house_energy(daily_kwh, seconds_until_sunset):
    """Replaceable first-order household forecast, excluding all EV energy."""
    return Fraction(str(daily_kwh)) * 1000 * max(0, seconds_until_sunset) / 86400


def target_soc(lower, upper, capacity_wh, remaining_pv_wh, house_wh):
    """Recoverable energy lowers only this profile's independently owned target."""
    if not 0 <= lower <= upper <= 100 or capacity_wh <= 0:
        raise ValueError("invalid Optimum limits or capacity")
    recoverable = (remaining_pv_wh - house_wh) * 100 / capacity_wh
    return min(upper, max(lower, upper - recoverable))


@dataclass
class PVDay:
    date: str | None = None
    state: str = "before"
    since: datetime | None = None
    valid_until: datetime | None = None

    def observe(self, now, local_date, watts, valid_until):
        """Accumulate only continuously valid evidence; midnight resets the latch."""
        if self.date != local_date:
            self.date, self.state, self.since = local_date, "before", None
            self.valid_until = None
        if self.valid_until is None or now > self.valid_until:
            self.since = None
        self.valid_until = valid_until
        if watts is None:
            self.since = None
            return
        eligible = watts >= 100 if self.state == "before" else watts < 100
        if self.state == "ended" or not eligible:
            self.since = None
            return
        if self.since is None:
            self.since = now
        delay = 300 if self.state == "before" else 900
        if (now - self.since).total_seconds() >= delay:
            self.state = "active" if self.state == "before" else "ended"
            self.since = None


class PVOptimum:
    """Policy adapter using the shared PV runtime and power regulators."""

    def optimum_observe_day(self, now=None, *, observation=...):
        from .pv_surplus import power_valid_for, reading

        if self.closed or not all(
            self.references.get(key) for key in OPTIMUM_REFERENCES
        ):
            return
        now = now or datetime.now(UTC)
        before = (self.optimum_day.date, self.optimum_day.state)
        state = (
            self.hass.states.get(self.references.get("leistung_pv", ""))
            if observation is ...
            else observation
        )
        try:
            watts = reading(state, now)
            expiry = now + timedelta(seconds=power_valid_for(state, now))
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            watts, expiry = None, now
        self.optimum_day.observe(
            now, dt_util.as_local(now).date().isoformat(), watts, expiry
        )
        if before != (self.optimum_day.date, self.optimum_day.state):
            self.optimum_day_store.async_delay_save(
                lambda: {
                    "date": self.optimum_day.date,
                    "state": self.optimum_day.state,
                },
                0,
            )

    def optimum_policy(self, target, now=None):
        from .pv_surplus import power_valid_for, reading

        now = now or datetime.now(UTC)
        self.optimum_observe_day(now)
        settings = self.setting(target)
        values = {}
        expiry = []
        for key in OPTIMUM_REFERENCES:
            state = self.hass.states.get(self.references.get(key, ""))
            values[key] = reading(
                state,
                now,
                soc=key == "soc_speicher_aktuell",
                energy=key in ("storage_capacity", "remaining_pv_energy"),
            )
            if values[key] < 0:
                raise ValueError("negative Optimum measurement")
            expiry.append(now + timedelta(seconds=power_valid_for(state, now)))
        soc = values["soc_speicher_aktuell"]
        if values["storage_capacity"] <= 0:
            raise ValueError("missing storage evidence")
        lower = Fraction(str(settings["optimum_lower_soc"]))
        upper = Fraction(str(settings["optimum_upper_soc"]))
        desired = upper
        if self.optimum_day.state == "active":
            sun = self.hass.states.get("sun.sun")
            if sun is None or sun.state not in ("above_horizon", "below_horizon"):
                raise ValueError("sun information unavailable")
            reported = getattr(sun, "last_reported", sun.last_updated)
            if (
                not 0 <= (now - reported).total_seconds() <= 90
                or power_valid_for(sun, now) <= 0
            ):
                raise ValueError("stale sun information")
            expiry.append(now + timedelta(seconds=power_valid_for(sun, now)))
            sunset = dt_util.parse_datetime(sun.attributes.get("next_setting", ""))
            if sunset is None or sunset.tzinfo is None:
                raise ValueError("sunset unavailable")
            # HA advances next_setting to tomorrow after today's sunset. Never
            # plan another 24 hours of household consumption in that case.
            local_sunset = dt_util.as_local(sunset).date()
            local_today = dt_util.as_local(now).date()
            if local_sunset not in (local_today, local_today + timedelta(days=1)) or (
                local_sunset != local_today and sun.state != "below_horizon"
            ):
                raise ValueError("sunset does not describe today")
            seconds = (
                max(0, (sunset - now).total_seconds())
                if local_sunset == local_today
                else 0
            )
            house = remaining_house_energy(
                settings["estimated_daily_house_consumption_kwh"],
                Fraction(str(seconds)),
            )
            desired = target_soc(
                lower,
                upper,
                values["storage_capacity"],
                values["remaining_pv_energy"],
                house,
            )
        mode = self.optimum_modes.get(target, "PV_BALANCE")
        if soc <= desired:
            mode = "PV_BALANCE"
        elif soc > desired + Fraction(str(settings["soc_hysterese"])):
            mode = "FAST_DISCHARGE"
        self.optimum_modes[target] = mode
        self.optimum_targets[target] = desired
        return values, mode, min(expiry)

    def optimum_refresh(self, now):
        """Track policy independently of EV connection, permission or authority."""
        if self.closed:
            return
        self.optimum_observe_day(now)
        for target in tuple(self.control.intents):
            if self.setting(target)["profile"] != "PV_OPTIMUM":
                continue
            try:
                self.optimum_policy(target, now)
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                self.optimum_targets.pop(target, None)
                self.optimum_modes.pop(target, None)
                self.optimum_regulators.pop(target, None)
            self.control.publish(target)

    def optimum_request(self, target):
        values, mode, expiry = self.optimum_policy(target)
        available, _, actual = self.pv_measurements(target, details=True)
        self.pv_expiry[target] = min(self.pv_expiry[target], expiry)
        settings = self.setting(target)
        from .pv_diagnostics import active, number

        if record := active(self, target):
            record.data.update(
                optimum_target_soc=number(self.optimum_targets[target]),
                optimum_mode=mode,
                pv_day_state=self.optimum_day.state,
            )
        if mode == "FAST_DISCHARGE":
            regulator = self.optimum_regulators.setdefault(
                target, FastDischargeRegulator()
            )
            power = regulator.request(
                actual,
                values["storage_discharge_power"],
                settings["optimum_max_discharge_w"],
                values["grid_import_power"],
                values["grid_export_power"],
                now=self.monotonic(),
                interval=settings["regulation_interval"],
            )
        else:
            self.optimum_regulators.pop(target, None)
            power = max(Fraction(0), available)
        return (
            power,
            Direction.DOWN,
            "actively_charging" if power > 0 else "paused_insufficient_pv",
        )
