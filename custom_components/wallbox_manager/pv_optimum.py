"""Independent Optimum SoC policy and PV-day observation, never device control."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from homeassistant.util import dt as dt_util

from .control.requests import Direction
from .diagnostics import profile_event
from .freshness import freshness_for
from .pv_regulators import FastDischargeRegulator
from .pv_soc import clamp_target, soc_policy, target_bounds

FAST_OBSERVATION_SECONDS = 1
TARGET_PLANNING_SECONDS = 300

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


def remaining_house_energy(daily_kwh, remaining_seconds):
    """A 24-hour average load integrated to the actual next local midnight."""
    return Fraction(str(daily_kwh)) * 1000 * max(0, remaining_seconds) / 86400


def remaining_day_seconds(now):
    """UTC subtraction preserves 23/25-hour local days across DST changes."""
    local = dt_util.as_local(now)
    midnight = datetime.combine(
        local.date() + timedelta(days=1), datetime.min.time(), local.tzinfo
    )
    return Fraction(
        str((midnight.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
    )


def target_soc(lower, upper, capacity_wh, remaining_pv_wh, house_wh):
    """Recoverable energy lowers only this profile's independently owned target."""
    if not 0 <= lower <= upper <= 100 or capacity_wh <= 0:
        raise ValueError("invalid Optimum limits or capacity")
    recoverable = (remaining_pv_wh - house_wh) * 100 / capacity_wh
    return min(upper, max(lower, upper - recoverable))


@dataclass
class PVDay:
    date: str | None = None
    state: str = "BEFORE_SURPLUS"

    def observe(self, local_date, surplus=None):
        if self.date != local_date:
            self.date, self.state = local_date, "BEFORE_SURPLUS"
        if self.state == "BEFORE_SURPLUS" and surplus is not None and surplus > 0:
            self.state = "DYNAMIC"

    def saved(self):
        return {"date": self.date, "state": self.state}


class PVOptimum:
    """Daily target planner and shared SoC/regulator adapter for all PV profiles."""

    def shared_pv_execution(self, target):
        """Preserve Optimum's reachable-phase execution semantics for Maximum."""
        return self.setting(target)["profile"] in ("PV_OPTIMUM", "PV_MAXIMUM")

    def common_pv(self, target):
        return self.setting(target)["profile"] in ("PV_OPTIMUM", "PV_MAXIMUM") or (
            self.setting(target)["profile"] == "PV_SURPLUS"
            and bool(self.references.get("soc_speicher_aktuell"))
        )

    def pv_reserve(self):
        return Fraction(
            str(self.battery.read(self.references.get("min_soc_speicher", "")))
        )

    def pv_target(self, target, now):
        settings = self.setting(target)
        reserve = self.pv_reserve()
        lower, _ = target_bounds(reserve, settings["soc_hysterese"])
        profile = settings["profile"]
        desired = (
            self.optimum_target(target, now)
            if profile == "PV_OPTIMUM"
            else lower
            if profile == "PV_MAXIMUM"
            else settings["soll_soc_speicher"]
        )
        desired = clamp_target(desired, reserve, settings["soc_hysterese"])
        self.optimum_targets[target] = desired
        return desired

    def common_soc_policy(self, target, soc, desired, now):
        settings = self.setting(target)
        previous = self.optimum_modes.get(target, "STOP")
        initializing = target in self.optimum_initializations
        stopped = initializing or not self.pv_ongoing.get(target, False)
        evidence = False
        middle = desired + Fraction(str(settings["soc_hysterese"])) / 2
        upper = desired + Fraction(str(settings["soc_hysterese"]))
        if (stopped or previous == "STOP") and middle <= soc < upper:
            pv, household = self.household_measurements(target, now)
            evidence = pv > household
        decision = soc_policy(
            desired,
            settings["soc_hysterese"],
            soc,
            previous,
            stopped=stopped,
            surplus=evidence,
        )
        self.optimum_initializations.discard(target)
        context = dict(
            target_soc=float(desired),
            lower_stop_threshold=float(decision.lower_stop_threshold),
            pv_start_threshold=float(decision.pv_start_threshold),
            fast_start_threshold=float(decision.fast_start_threshold),
            mode=decision.mode,
            previous_mode=previous,
            reason=decision.reason,
            pv_start_evidence=evidence,
            target_profile=settings["profile"],
            soc=float(soc),
        )
        from .pv_diagnostics import active

        if record := active(self, target):
            record.data.update(
                {k: v for k, v in context.items() if k != "previous_mode"}
            )
        if decision.mode != previous:
            profile_event(self, target, "soc_mode", **context)
            self.pv_stop_since.pop(target, None)
            if asyncio.current_task() is not self.tasks.get(target):
                self.optimum_wakes.setdefault(target, asyncio.Event()).set()
        self.optimum_modes[target] = decision.mode
        return decision.mode

    def optimum_initialize(self, target):
        """Mark an activation once; delayed evidence must not lose initialization."""
        self.optimum_plans.pop(target, None)
        self.optimum_initializations.add(target)
        self.optimum_regulators.pop(target, None)
        self.pv_stop_since.pop(target, None)
        session = self.control.runtime.sessions.get(target)
        self.optimum_connections[target] = (
            (session.session_id, session.active) if session else None
        )

    def optimum_connection(self, target):
        session = self.control.runtime.sessions.get(target)
        if session is None:
            return
        current = (session.session_id, session.active)
        previous = self.optimum_connections.get(target)
        if current == previous:
            return
        self.optimum_connections[target] = current
        handover = bool(
            previous
            and any(
                old.session_id == previous[0] and old.end_reason == "superseded"
                for old in self.control.runtime.sessions.history(target)
            )
        )
        if session.active and not handover:
            self.optimum_initialize(target)
            profile_event(self, target, "vehicle_connected")
        elif not session.active and session.end_reason != "superseded":
            profile_event(self, target, "vehicle_disconnected")

    def optimum_day_for(self, target):
        from .profiles import target_key

        if target not in self.optimum_days:
            saved = self.optimum_saved_days.get(target_key(target), {})
            self.optimum_days[target] = (
                PVDay(saved.get("date"), saved["state"])
                if isinstance(saved, dict)
                and saved.get("state") in ("BEFORE_SURPLUS", "DYNAMIC", "FINISHED")
                else PVDay()
            )
        return self.optimum_days[target]

    def optimum_save_day(self):
        from .profiles import target_key

        self.optimum_day_store.async_delay_save(
            lambda: {
                "version": 2,
                "days": {
                    **self.optimum_saved_days,
                    **{
                        target_key(t): day.saved()
                        for t, day in self.optimum_days.items()
                    },
                },
            },
            0,
        )

    def optimum_observe_day(self, now=None, *, target=None, observation=...):
        """Daily surplus proof uses raw, fresh load excluding this selected EV."""
        if self.closed:
            return False
        now = now or datetime.now(UTC)
        if target is None:
            changed = False
            for connector in tuple(self.control.intents):
                if self.setting(connector)["profile"] == "PV_OPTIMUM":
                    changed |= self.optimum_observe_day(now, target=connector)
            return changed
        day = self.optimum_day_for(target)
        before = day.saved()
        local_date = dt_util.as_local(now).date().isoformat()
        # Reset at midnight independently of measurement availability.
        day.observe(local_date)
        if before["date"] != day.date:
            self.optimum_plans.pop(target, None)
            self.optimum_forecasts.pop(target, None)
            upper = Fraction(str(self.setting(target)["optimum_upper_soc"]))
            if self.optimum_targets.get(target) != upper:
                profile_event(
                    self,
                    target,
                    "target_soc",
                    target_soc=float(upper),
                    phase="BEFORE_SURPLUS",
                    reason="local_day_reset",
                )
            self.optimum_targets[target] = upper
        if day.state == "BEFORE_SURPLUS":
            try:
                pv, household = self.household_measurements(target, now)
                day.observe(local_date, pv - household)
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                pass
        if before != day.saved():
            self.optimum_save_day()
            profile_event(self, target, "planner_phase", phase=day.state, date=day.date)
            return True
        return False

    def optimum_target(self, target, now):
        """Fixed targets need no forecast; dynamic plans use remaining-day energy."""
        from .pv_surplus import reading

        self.optimum_observe_day(now, target=target)
        day = self.optimum_day_for(target)
        settings = self.setting(target)
        lower = Fraction(str(settings["optimum_lower_soc"]))
        upper = Fraction(str(settings["optimum_upper_soc"]))
        desired = upper
        if day.state == "DYNAMIC":
            pv = reading(
                self.hass.states.get(self.references.get("remaining_pv_energy", "")),
                now,
                energy=True,
                max_age=freshness_for("remaining_pv_energy"),
                diagnostic_key="remaining_pv_energy",
            )
            if pv < 0:
                raise ValueError("invalid planning energy")
            house = remaining_house_energy(
                settings["estimated_daily_house_consumption_kwh"],
                remaining_day_seconds(now),
            )
            self.optimum_forecasts[target] = {
                "remaining_pv_wh": float(pv),
                "remaining_house_wh": float(house),
                "remaining_surplus_wh": float(pv - house),
            }
            if pv <= house:
                day.state = "FINISHED"
                self.optimum_save_day()
                profile_event(
                    self,
                    target,
                    "planner_phase",
                    phase=day.state,
                    date=day.date,
                    **self.optimum_forecasts[target],
                )
            else:
                capacity = reading(
                    self.hass.states.get(self.references.get("storage_capacity", "")),
                    now,
                    energy=True,
                    max_age=freshness_for("storage_capacity"),
                    diagnostic_key="storage_capacity",
                )
                if capacity <= 0:
                    raise ValueError("invalid planning energy")
                key = (
                    day.date,
                    day.state,
                    lower,
                    upper,
                    settings["estimated_daily_house_consumption_kwh"],
                )
                planned = self.optimum_plans.get(target)
                tick = self.monotonic()
                if planned is None or planned[0] != key or tick >= planned[1]:
                    planned = (
                        key,
                        tick + TARGET_PLANNING_SECONDS,
                        target_soc(lower, upper, capacity, pv, house),
                    )
                    self.optimum_plans[target] = planned
                desired = planned[2]
        if day.state != "DYNAMIC":
            self.optimum_plans.pop(target, None)
        if self.optimum_targets.get(target) != desired:
            profile_event(
                self,
                target,
                "target_soc",
                target_soc=float(desired),
                phase=day.state,
                lower=float(lower),
                upper=float(upper),
                **self.optimum_forecasts.get(target, {}),
            )
        self.optimum_targets[target] = desired
        return desired

    def optimum_policy(self, target, now=None):
        from .pv_surplus import power_valid_for, reading

        now = now or datetime.now(UTC)
        # Establish the independent target before checking execution evidence.
        desired = self.pv_target(target, now)
        soc = reading(
            self.hass.states.get(self.references.get("soc_speicher_aktuell", "")),
            now,
            soc=True,
            max_age=freshness_for("soc_speicher_aktuell"),
            diagnostic_key="soc_speicher_aktuell",
        )
        mode = self.common_soc_policy(target, soc, desired, now)
        if mode == "STOP":
            # A proven protection stop needs no positive power budget. Missing
            # power inputs must not defeat the lower SoC boundary.
            state = self.hass.states.get(self.references["soc_speicher_aktuell"])
            return {}, mode, now + timedelta(seconds=power_valid_for(state, now))
        # Mode depends only on valid SoC and target. Command budgets additionally
        # require every original execution input, even in a fixed-upper phase.
        values, expiry = {}, []
        references = (
            OPTIMUM_REFERENCES
            if self.setting(target)["profile"] == "PV_OPTIMUM"
            else (
                "soc_speicher_aktuell",
                "leistung_pv",
                "leistung_verbraucher",
                *(
                    (
                        "storage_discharge_power",
                        "grid_import_power",
                        "grid_export_power",
                    )
                    if mode == "FAST_DISCHARGE"
                    else ()
                ),
            )
        )
        for key in references:
            state = self.hass.states.get(self.references.get(key, ""))
            values[key] = reading(
                state,
                now,
                soc=key == "soc_speicher_aktuell",
                energy=key in ("storage_capacity", "remaining_pv_energy"),
                max_age=freshness_for(key),
                diagnostic_key=key,
            )
            if values[key] < 0:
                raise ValueError("negative Optimum measurement")
            expiry.append(
                now
                + timedelta(
                    seconds=power_valid_for(state, now, max_age=freshness_for(key))
                )
            )
        if "storage_capacity" in values and values["storage_capacity"] <= 0:
            raise ValueError("missing storage evidence")
        return values, mode, min(expiry)

    def optimum_fast(self, target):
        return (
            self.common_pv(target)
            and self.optimum_modes.get(target) == "FAST_DISCHARGE"
        )

    async def optimum_wait(self, target, seconds):
        """Wake an existing balance loop on a responsive SoC mode transition."""
        event = self.optimum_wakes.setdefault(target, asyncio.Event())
        tasks = [
            asyncio.create_task(self.wait(seconds)),
            asyncio.create_task(event.wait()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def optimum_refresh(self, now):
        """Track policy independently of EV connection, permission or authority."""
        if self.closed:
            return
        self.optimum_observe_day(now)
        for target in tuple(self.control.intents):
            if not self.common_pv(target):
                continue
            try:
                policy = self.optimum_policy(target, now)
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                self.pv_input_gap(target)
            else:
                if policy[1] == "FAST_DISCHARGE":
                    try:
                        # Observe even while serialized OCPP work is pending.
                        # Only the existing PV loop may dispatch an operating point.
                        self.optimum_request(target, policy=policy)
                    except ValueError, TypeError, ZeroDivisionError, OverflowError:
                        self.pv_input_gap(target)
            self.control.publish(target)

    def optimum_request(self, target, *, policy=None):
        values, mode, expiry = policy or self.optimum_policy(target)
        if mode == "STOP":
            self.pv_expiry[target] = expiry
            self.optimum_regulators.pop(target, None)
            return Fraction(0), Direction.DOWN, "stopped_battery_soc"
        available, _, actual = self.pv_measurements(target, details=True)
        self.pv_expiry[target] = min(self.pv_expiry[target], expiry)
        settings = self.setting(target)
        from .pv_diagnostics import active, number

        if record := active(self, target):
            record.data.update(
                optimum_target_soc=number(self.optimum_targets[target]),
                optimum_mode=mode,
                pv_day_state=self.optimum_day_for(target).state,
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
        if record := active(self, target):
            record.data.update(
                raw_regulator_target_w=number(power),
                observed_net_grid_import_w=number(
                    max(
                        0,
                        values.get("grid_import_power", 0)
                        - values.get("grid_export_power", 0),
                    )
                ),
            )
        return (
            power,
            Direction.DOWN,
            "actively_charging" if power > 0 else "paused_insufficient_pv",
        )

    def optimum_pause_policy(
        self,
        target,
        power,
        result,
        *,
        advance,
        transition_mode=None,
        energy_desired=False,
    ):
        """Separate an energy deficit from permission to enter expensive OFF.

        FAST keeps a reachable positive floor indefinitely. BALANCE reuses the
        configured stop delay, holding that floor (not an obsolete high offer).
        At least one regulation interval is required even with stop delay zero:
        one transient sample must never pause an established Optimum charge.
        Unknown inputs are handled by pv_plan before entering this policy.
        """
        from .pv_diagnostics import active, number
        from .solver.operating_point import Reason

        minimum = self.control.minimum_positive(
            target,
            dispatch_modes=(transition_mode,) if transition_mode else None,
            energy_desired=energy_desired,
        )

        if record := active(self, target):
            record.data.update(
                minimum_reachable_power_w=number(minimum.point.offered_power_w)
                if minimum and minimum.point
                else None,
            )
        if result.point.charging:
            if advance:
                self.pv_stop_since.pop(target, None)
            return None
        if minimum is None or minimum.point is None:
            # Known electrical infeasibility keeps existing safe OFF behavior;
            # lack of reachability evidence is a no-decision, never a new pause.
            if minimum is None or minimum.reason != Reason.ELECTRICAL_LIMIT:
                if advance:
                    self.pv_stop_since.pop(target, None)
                return power, Direction.DOWN, "telemetry_unavailable", None
            return 0, Direction.DOWN, "optimum_no_positive_point", result
        confirmed = self.control.confirmed_point(target)
        continuing = bool(
            confirmed.charging
            if confirmed is not None
            else self.control.runtime.enabled(target) is True
        )
        if self.optimum_fast(target):
            if advance:
                self.pv_stop_since.pop(target, None)
            status = "optimum_minimum_hold"
        elif continuing:
            now = self.monotonic()
            if advance:
                self.pv_stop_since.setdefault(target, now)
            since = self.pv_stop_since.get(target)
            settings = self.setting(target)
            delay = max(settings["pv_stop_delay"], settings["regulation_interval"])
            if since is not None and now >= since + delay:
                return 0, Direction.DOWN, "optimum_deliberate_pause", result
            status = "optimum_pause_pending"
        else:
            # Already OFF (or first activation) needs no positive-to-zero debounce.
            return 0, Direction.DOWN, "optimum_deliberate_pause", result
        if record := active(self, target):
            record.data["policy_reason"] = status
        return minimum.point.offered_power_w, Direction.DOWN, status, minimum
