"""PV policy and measurement adapter; execution belongs to ControlRuntime."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from .control.commands import CommandReason, CommandStatus
from .control.requests import Direction
from .control.runtime import PowerSettings
from .core.capabilities import EvidenceState
from .freshness import LIVE_FRESHNESS
from .pv_diagnostics import active, cycle, diagnostic_plan, entity_sample, number
from .pv_optimum import FAST_OBSERVATION_SECONDS
from .pv_regulators import pv_balance

PV_DEFAULTS = {
    "approximation": "down",
    "soll_soc_speicher": 95,
    "soc_hysterese": 5,
    "regulation_interval": 5,
    "pv_start_delay": 0,
    "pv_stop_delay": 90,
}
MAX_AGE_SECONDS = LIVE_FRESHNESS
_LOGGER = logging.getLogger(__name__)


def reading(state, now, *, soc=False, energy=False, max_age=LIVE_FRESHNESS):
    """Reject missing units, non-finite values, old and future observations."""
    if state is None:
        raise ValueError("missing measurement")
    value = Fraction(state.state)
    unit = state.attributes.get("unit_of_measurement")
    units = {"%": 1} if soc else {"W": 1, "kW": 1000, "MW": 1000000}
    if energy:
        units = {"Wh": 1, "kWh": 1000, "MWh": 1000000}
    if unit not in units:
        raise ValueError("invalid unit")
    timestamp = getattr(state, "last_reported", state.last_updated)
    if not 0 <= (now - timestamp).total_seconds() <= max_age:
        raise ValueError("stale measurement")
    expiry = state.attributes.get("valid_until")
    if expiry and datetime.fromisoformat(expiry) <= now:
        raise ValueError("expired measurement")
    value *= units[unit]
    if soc and not 0 <= value <= 100:
        raise ValueError("invalid SOC")
    return value


def power_valid_for(state, now, *, max_age=LIVE_FRESHNESS):
    """Known validity bounds history segments as well as current snapshots."""
    if state is None:
        return 0
    expiry = getattr(state, "last_reported", state.last_updated) + timedelta(
        seconds=max_age
    )
    if explicit := state.attributes.get("valid_until"):
        expiry = min(expiry, datetime.fromisoformat(explicit))
    return max(0, (expiry - now).total_seconds())


def decision(available, soc, settings, ongoing):
    """A pause clears ongoing; every subsequent start crosses the strict threshold."""
    direction = Direction(settings["approximation"])
    if soc is not None:
        target = min(Fraction(str(settings["soll_soc_speicher"])), 99)
        stop = target - Fraction(str(settings["soc_hysterese"]))
        if soc < stop:
            return Fraction(0), Direction.DOWN, "stopped_battery_soc"
        if not ongoing and soc <= target:
            return Fraction(0), Direction.DOWN, "waiting_battery_soc"
        direction = Direction.UP if soc > target else Direction.DOWN
    if available <= 0:
        return Fraction(0), direction, "paused_insufficient_pv"
    return available, direction, "actively_charging"


class PVSurplus:
    """Mixin sharing profile persistence, epochs, ownership and primitive controls."""

    def pv_battery_state(self, target, soc):
        """Latch battery eligibility independently of a delayed electrical stop."""
        ongoing = self.pv_ongoing.get(target, False)
        if soc is None:
            return ongoing
        settings = self.setting(target)
        upper = Fraction(str(settings["soll_soc_speicher"]))
        lower = upper - Fraction(str(settings["soc_hysterese"]))
        allowed = self.pv_battery.get(target, ongoing)
        if soc > upper:
            allowed = True
        elif soc < lower or not ongoing:
            allowed = False
        self.pv_battery[target] = allowed
        if record := active(self, target):
            record.data.update(
                battery_policy_allowed=allowed,
                battery_hysteresis_holding=ongoing and lower <= soc <= upper,
                battery_start_threshold=number(upper),
                battery_stop_threshold=number(lower),
            )
        return allowed

    def permits_point(
        self, target, point, *, after_dispatch=False, transition_mode=None
    ):
        """Apply the existing PV policy at the shared command dispatch fence."""
        if self.setting(target)["profile"] not in ("PV_SURPLUS", "PV_OPTIMUM"):
            return not point.charging or self.grid_phase(target)[0] == "active"
        if not point.charging:
            if (
                self.setting(target)["profile"] == "PV_OPTIMUM"
                and self.control.intent(target).policy_pause
            ):
                # An economic OFF queued before a recovery must still be a
                # deliberate pause at dispatch. Explicit control stops bypass it.
                _, _, _, plan = self.pv_plan(target, advance=False)
                if plan is None or plan.point is None or plan.point.charging:
                    return False
            # OFF ends continuation even when another primitive control requested it.
            self.pv_ongoing[target] = False
            return True
        if self.closed or self.setting(target)[
            "profile"
        ] not in self.available_profiles(target):
            return False
        power, direction, status, plan = self.pv_plan(
            target,
            advance=False,
            transition_mode=point.mode if after_dispatch else transition_mode,
        )
        if plan is None:
            return False  # Holding hardware is not permission for a new write.
        if self.setting(target)["profile"] == "PV_OPTIMUM" and not after_dispatch:
            inputs = self.control.inputs(target)
            desired = self.control.intent(target).energy_desired
            if inputs is None or (
                point.mode != inputs.current_mode
                and (
                    desired is None
                    or desired.point is None
                    or not desired.point.charging
                    or desired.point.mode != point.mode
                )
            ):
                # A lower-power old phase is not authorized just because an
                # increased fresh budget could still afford it. Current within
                # the still-desired phase retains normal DOWN coalescing.
                self.control.intent(target).fence_reason = "pre_dispatch_desired_phase"
                return False
        if status == "pv_stop_delay":
            return point.same_setpoint(self.control.confirmed_point(target))
        return (
            power > 0
            and self.control.intent(target).request.direction == direction
            and (direction != Direction.DOWN or point.offered_power_w <= power)
        )

    def pv_soc_changed(self, event):
        """Fence unsafe queued work and wake the same regulator for immediate OFF.

        Use the event's value: a later recovery must not hide a threshold crossing
        that occurred while a command or regulation timer was waiting.
        """
        if self.closed or event.data.get("entity_id") != self.references.get(
            "soc_speicher_aktuell"
        ):
            return
        for target in tuple(self.control.intents):
            settings = self.setting(target)
            if settings["profile"] != "PV_SURPLUS":
                continue
            if target in self.control.pending_points:
                continue  # Normal SoC policy belongs to the next regulation tick.
            with cycle(self, target, "soc_event"):
                try:
                    soc = reading(
                        event.data.get("new_state"), datetime.now(UTC), soc=True
                    )
                    power, _, status = decision(
                        Fraction(1), soc, settings, self.pv_battery_state(target, soc)
                    )
                except ValueError, TypeError, ZeroDivisionError, OverflowError:
                    power, status = Fraction(0), "measurements_unavailable"
                if record := active(self, target):
                    record.data.update(
                        policy_reason=status, soc_allows_charging=power > 0
                    )
                    record.data["soc_event"] = entity_sample(
                        event.data.get("new_state"),
                        event.data.get("entity_id"),
                        datetime.now(UTC),
                    )
                if status == "measurements_unavailable":
                    self.pv_input_gap(target)
                    continue
                if power > 0:
                    if (
                        self.pv_ongoing.get(target, False)
                        and target in self.pv_stop_since
                    ):
                        self.pv_plan(target)
                    continue
                if (
                    status in ("stopped_battery_soc", "waiting_battery_soc")
                    and self.pv_ongoing.get(target, False)
                    and settings["pv_stop_delay"] > 0
                ):
                    _, _, planned, result = self.pv_plan(target)
                    if planned == "pv_stop_delay" or result is None:
                        continue
                point = self.control.confirmed_point(target)
                needs_stop = (
                    self.pv_ongoing.get(target, False)
                    or self.control.intent(target).request.target_w > 0
                    or (point and point.charging)
                )
                self.pv_ongoing[target] = False
                if not needs_stop:
                    continue
                self.invalidate(target)
                self.control._edit(
                    target, {"target_w": Fraction(0), "direction": Direction.DOWN}
                )
                self.status[target] = status
                if self.valid(target, self.epochs[target]):
                    self.launch(target, stop_first=True)

    def pv_measurements(self, target, *, details=False):
        now = datetime.now(UTC)
        pv_state = self.hass.states.get(self.references.get("leistung_pv", ""))
        load_state = self.hass.states.get(
            self.references.get("leistung_verbraucher", "")
        )
        candidates = [
            s
            for s in self.hass.states.async_all("sensor")
            if s.attributes.get("wallbox_manager_role") == "session_power"
            and s.attributes.get("wallbox_manager_entry") == self.entry_id
            and s.attributes.get("station_id") == target.station.value
            and s.attributes.get("evse_id") == target.evse.value
            and s.attributes.get("connector_id") == target.value
            and s.attributes.get("runtime_incarnation")
            == self.control.runtime.runtime_id
        ]
        if record := active(self, target):
            record.capture_inputs(candidates)
        pv, load = reading(pv_state, now), reading(load_state, now)
        raw_pv, raw_load = pv, load
        timestamp = self.monotonic()
        pv, pv_history = self.power_history["leistung_pv"].average(timestamp, pv)
        load, load_history = self.power_history["leistung_verbraucher"].average(
            timestamp, load
        )
        if record := active(self, target):
            record.data.update(
                raw_pv_power_w=number(raw_pv),
                smoothed_pv_power_w=number(pv),
                raw_consumption_power_w=number(raw_load),
                smoothed_consumption_power_w=number(load),
                smoothing_window_s=self.references.get("power_smoothing_window", 5),
                smoothing_enabled=bool(
                    self.references.get("power_smoothing_window", 5)
                ),
                pv_history_s=pv_history,
                consumption_history_s=load_history,
            )
        soc_entity = self.references.get("soc_speicher_aktuell")
        soc = (
            reading(self.hass.states.get(soc_entity), now, soc=True)
            if soc_entity
            else None
        )
        if len(candidates) != 1:
            raise ValueError("selected connector power missing or ambiguous")
        actual = reading(candidates[0], now)
        if actual < 0:
            raise ValueError("negative charging power")
        states = [pv_state, load_state, candidates[0]]
        if soc_entity:
            states.append(self.hass.states.get(soc_entity))
        expiry = []
        for state in states:
            expiry.append(
                getattr(state, "last_reported", state.last_updated)
                + timedelta(seconds=MAX_AGE_SECONDS)
            )
            if value := state.attributes.get("valid_until"):
                expiry.append(datetime.fromisoformat(value))
        self.pv_expiry[target] = min(expiry)
        available = pv_balance(pv, load, actual)
        if record := active(self, target):
            record.data.update(
                site_load_w=number(load - actual),
                surplus_w=number(available),
                measured_power_w=number(actual),
            )
        return (available, soc, actual) if details else (available, soc)

    def pv_sync_session(self, target):
        """An identity handover is not an electrical stop; a real Ended is."""
        session = self.control.runtime.sessions.get(target)
        previous = self.pv_sessions.get(target)
        identity = session.session_id if session and session.active else None
        ended = bool(session and not session.active) or (
            previous is not None
            and identity != previous
            and any(
                item.session_id == previous and item.end_reason != "superseded"
                for item in self.control.runtime.sessions.history(target)
            )
        )
        confirmed = self.control.confirmed_point(target)
        if ended or (identity != previous and not (confirmed and confirmed.charging)):
            self.pv_ongoing[target] = False
            self.pv_battery.pop(target, None)
            self.pv_start_since.pop(target, None)
            self.pv_stop_since.pop(target, None)
        self.pv_sessions[target] = identity

    def pv_input_gap(self, target):
        """Break continuous policy timers without editing desired/applied state."""
        self.optimum_regulators.pop(target, None)
        self.pv_start_since.pop(target, None)
        self.pv_stop_since.pop(target, None)
        self.pv_expiry.pop(target, None)
        self.status[target] = "measurements_unavailable"

    def pv_request(self, target):
        self.pv_sync_session(target)
        try:
            if self.setting(target)["profile"] == "PV_OPTIMUM":
                return self.optimum_request(target)
            available, soc = self.pv_measurements(target)
            return decision(
                available, soc, self.setting(target), self.pv_battery_state(target, soc)
            )
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            self.optimum_regulators.pop(target, None)
            return Fraction(0), Direction.DOWN, "measurements_unavailable"

    @diagnostic_plan
    def pv_plan(self, target, *, advance=True, transition_mode=None):
        """Plan fresh energy preference, then its temporarily executable realization.

        Both solves share one regulator sample and the existing policy/solver.
        The desired result is observational state, never a queued retry command.
        Only executable planning advances pause/start persistence.
        """
        request = self.pv_request(target)
        intent = self.control.intent(target)
        if self.setting(target)["profile"] == "PV_OPTIMUM":
            desired = self._pv_plan(target, request, advance=False, energy_desired=True)
            intent.energy_desired = desired[3]
        else:
            intent.energy_desired = None
        executable = self._pv_plan(
            target, request, advance=advance, transition_mode=transition_mode
        )
        if self.setting(target)["profile"] == "PV_OPTIMUM" and (
            record := active(self, target)
        ):
            from .pv_diagnostics import point

            record.data.update(
                desired=point(intent.energy_desired.point)
                if intent.energy_desired
                else None,
                executable=point(executable[3].point) if executable[3] else None,
                phase_transition_blocked=bool(
                    intent.energy_desired
                    and intent.energy_desired.point
                    and intent.energy_desired.point.charging
                    and self.control.phase_restricted(target)
                    and (inputs := self.control.inputs(target))
                    and intent.energy_desired.point.mode != inputs.current_mode
                ),
                executable_minimum_hold=executable[2]
                in ("optimum_minimum_hold", "optimum_pause_pending"),
            )
        return executable

    def _pv_plan(
        self, target, request, *, advance, transition_mode=None, energy_desired=False
    ):
        """Apply existing profile policy with explicit electrical selection scope."""
        power, direction, status = request
        settings = self.setting(target)
        now = self.monotonic()
        inputs = self.control.inputs(target)
        if self.setting(target)["profile"] not in self.available_profiles(target):
            power, status = Fraction(0), "profile_unavailable"
        elif inputs is None or inputs.capabilities.stop.state != EvidenceState.VERIFIED:
            power, status = Fraction(0), "safe_stop_unavailable"
        maximum = self.control.power_ceiling(target)
        if maximum is not None:
            power = min(power, maximum)

        def resolve(watts, policy):
            return self.control.resolve(
                target,
                request=PowerSettings(watts, policy),
                reachable=settings["profile"] == "PV_OPTIMUM",
                energy_desired=energy_desired,
                dispatch_modes=(transition_mode,)
                if transition_mode is not None
                else None,
            )[1]

        result = resolve(power, direction)
        # No decision is distinct from the solver's confirmed feasible OFF point.
        # Do not turn missing policy/electrical inputs into a desired zero or a
        # retryable command. Post-dispatch validation may use its dispatched mode.
        if status != "profile_unavailable" and (
            status in ("measurements_unavailable", "safe_stop_unavailable")
            or inputs is None
            or (transition_mode is None and not inputs.eligible_modes)
            or result is None
            or result.point is None
        ):
            if advance:
                self.pv_start_since.pop(target, None)
                self.pv_stop_since.pop(target, None)
            return (
                power,
                direction,
                (
                    status
                    if status in ("measurements_unavailable", "safe_stop_unavailable")
                    else "telemetry_unavailable"
                ),
                None,
            )
        if settings["profile"] == "PV_OPTIMUM" and status in (
            "actively_charging",
            "paused_insufficient_pv",
        ):
            policy = self.optimum_pause_policy(
                target,
                power,
                result,
                advance=advance,
                transition_mode=transition_mode,
                energy_desired=energy_desired,
            )
            if policy is not None:
                return policy
        if (
            status == "actively_charging"
            and result
            and result.point
            and not result.point.charging
        ):
            status = "paused_insufficient_pv"
        ongoing = self.pv_ongoing.get(target, False)
        if (
            status
            in ("paused_insufficient_pv", "stopped_battery_soc", "waiting_battery_soc")
            and ongoing
            and settings["profile"] != "PV_OPTIMUM"
        ):
            confirmed = self.control.confirmed_point(target)
            volts = (
                inputs.voltage.active_voltages(confirmed.mode, datetime.now(UTC))
                if confirmed and confirmed.charging and inputs
                else None
            )
            held = (
                self.control.resolve(
                    target,
                    substitute_mode=confirmed.mode,
                    request=PowerSettings(
                        confirmed.current_a * sum(volts), Direction.DOWN
                    ),
                )[1]
                if volts
                else None
            )
            if record := active(self, target):
                record.data["stop_policy_reason"] = status
            if advance:
                self.pv_start_since.pop(target, None)
                self.pv_stop_since.setdefault(target, now)
            since = self.pv_stop_since.get(target)
            if (
                since is not None
                and now < since + settings["pv_stop_delay"]
                and confirmed
                and confirmed.charging
            ):
                if held and confirmed.same_setpoint(held.point):
                    return (
                        held.point.offered_power_w,
                        Direction.DOWN,
                        "pv_stop_delay",
                        held,
                    )
                if volts is None:
                    return (
                        confirmed.offered_power_w,
                        Direction.DOWN,
                        "pv_stop_delay",
                        None,
                    )
                # Proven electrical infeasibility retains the existing safety OFF.
        elif advance:
            self.pv_stop_since.pop(target, None)
        if (
            status == "actively_charging"
            and result
            and result.point
            and result.point.charging
        ):
            if (
                not ongoing
                and settings["pv_start_delay"] > 0
                and not self.optimum_fast(target)
                and not (
                    settings["profile"] == "PV_OPTIMUM"
                    and (confirmed := self.control.confirmed_point(target))
                    and confirmed.charging
                )
            ):
                if advance:
                    self.pv_start_since.setdefault(target, now)
                since = self.pv_start_since.get(target)
                if since is None or now < since + settings["pv_start_delay"]:
                    return (
                        Fraction(0),
                        Direction.DOWN,
                        "pv_start_delay",
                        resolve(Fraction(0), Direction.DOWN),
                    )
            return power, direction, status, result
        if advance:
            self.pv_start_since.pop(target, None)
        return Fraction(0), Direction.DOWN, status, resolve(Fraction(0), Direction.DOWN)

    def pv_edit(self, target):
        pending = target in self.control.pending_points
        if not pending:
            self.control.intent(target).profile_modes = None
            self.control.intent(target).reachable_only = (
                self.setting(target)["profile"] == "PV_OPTIMUM"
            )
        power, direction, status, result = self.pv_plan(target)
        if pending:
            # Coalesce all policy targets without superseding the command.
            return result
        intent = self.control.intent(target)
        if result is None:
            self.status[target] = status
            if (
                self.setting(target)["profile"] == "PV_OPTIMUM"
                or self.control.runtime.enabled(target) is not False
            ):
                return None  # Retain desired, in-flight and confirmed points.
            # Explicit ON/recovery may prepare a freshly confirmed disabled
            # station at zero before enabling CP. This cannot stop ongoing
            # charging; the normal sender must still confirm this preparation.
            power, direction = Fraction(0), Direction.DOWN
            result = self.control.resolve(
                target, request=PowerSettings(power, direction)
            )[1]
            if result is None or result.point is None:
                return None
        intent.profile_modes = (
            (result.point.mode.count,) if status == "pv_stop_delay" else None
        )
        if intent.request.target_w != power or intent.request.direction != direction:
            self.control._edit(target, {"target_w": power, "direction": direction})
        intent.policy_pause = status == "optimum_deliberate_pause"
        self.status[target] = status
        if status not in (
            "actively_charging",
            "pv_stop_delay",
            "optimum_minimum_hold",
            "optimum_pause_pending",
        ):
            self.pv_ongoing[target] = False
        return result

    def pv_wait_seconds(self, target):
        now = self.monotonic()
        settings = self.setting(target)
        deadlines = [
            1 if self.optimum_fast(target) else settings["regulation_interval"]
        ]
        for timers, field in (
            (self.pv_start_since, "pv_start_delay"),
            (self.pv_stop_since, "pv_stop_delay"),
        ):
            if target in timers and timers[target] + settings[field] > now:
                deadlines.append(timers[target] + settings[field] - now)
        if target in self.pv_expiry and self.status.get(target) in (
            "actively_charging",
            "pv_start_delay",
            "pv_stop_delay",
            "optimum_minimum_hold",
            "optimum_pause_pending",
        ):
            deadlines.append(
                max(
                    0.001,
                    (self.pv_expiry[target] - datetime.now(UTC)).total_seconds()
                    + 0.001,
                )
            )
        return min(deadlines)

    def pv_measurement_changed(self, event):
        """Input gaps pause regulation; valid observations can resume a start."""
        entity = event.data.get("entity_id")
        if self.closed:
            return
        state = event.data.get("new_state")
        if entity == self.references.get("leistung_pv") and self.optimum_observe_day(
            observation=state
        ):
            self.optimum_refresh(datetime.now(UTC))
        for key in ("leistung_pv", "leistung_verbraucher"):
            if entity == self.references.get(key):
                try:
                    now = datetime.now(UTC)
                    value = reading(state, now)
                    valid_for = power_valid_for(state, now)
                except ValueError, TypeError, ZeroDivisionError, OverflowError:
                    value, valid_for = None, 0
                self.power_history[key].add(
                    self.monotonic(), value, valid_for=valid_for
                )
        for target in tuple(self.tasks):
            if self.setting(target)["profile"] not in ("PV_SURPLUS", "PV_OPTIMUM"):
                continue
            if target in self.control.pending_points:
                continue  # Keep the dispatched decision; the next tick samples anew.
            attrs = (
                (state or event.data.get("old_state")).attributes
                if state or event.data.get("old_state")
                else {}
            )
            power = entity in (
                self.references.get("leistung_pv"),
                self.references.get("leistung_verbraucher"),
            ) or (
                attrs.get("wallbox_manager_role") == "session_power"
                and attrs.get("wallbox_manager_entry") == self.entry_id
                and attrs.get("station_id") == target.station.value
                and attrs.get("evse_id") == target.evse.value
                and attrs.get("connector_id") == target.value
            )
            soc = entity == self.references.get("soc_speicher_aktuell")
            if not power and not soc:
                continue
            try:
                reading(state, datetime.now(UTC), soc=soc)
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                self.pv_input_gap(target)
                continue
            if self.pv_ongoing.get(target, False) and target in self.pv_stop_since:
                self.pv_plan(target)
            if not self.pv_ongoing.get(target, False) and self.status.get(target) in (
                "paused_insufficient_pv",
                "waiting_battery_soc",
                "stopped_battery_soc",
                "measurements_unavailable",
                "pv_start_delay",
            ):
                _, _, planned, _ = self.pv_plan(target)
                if planned in (
                    "actively_charging",
                    "pv_start_delay",
                ) and planned != self.status.get(target):
                    start = self.pv_start_since.get(target)
                    self.invalidate(target)
                    if start is not None:
                        self.pv_start_since[target] = start
                    if self.valid(target, self.epochs[target]):
                        self.launch(target)

    def pv_confirm(self, target):
        point = self.control.confirmed_point(target)
        session = self.control.runtime.sessions.get(target)
        self.pv_ongoing[target] = bool(
            (
                self.status.get(target)
                in (
                    "actively_charging",
                    "pv_stop_delay",
                    "optimum_minimum_hold",
                    "optimum_pause_pending",
                )
                or (
                    self.pv_ongoing.get(target, False)
                    and self.status.get(target)
                    in (
                        "measurements_unavailable",
                        "telemetry_unavailable",
                        "safe_stop_unavailable",
                    )
                )
            )
            and point
            and point.charging
            and (session is None or session.active)
        )
        self.pv_sync_session(target)
        if self.pv_ongoing[target]:
            self.pv_start_since.pop(target, None)
        if (
            self.status.get(target) == "actively_charging"
            and not self.pv_ongoing[target]
        ):
            self.status[target] = "awaiting_applied"

    async def pv_enable_attempt(self, target, epoch):
        """Continue one explicitly authorized enable, never acquire authority."""
        # The prepared request is this attempt's regulation snapshot. Normal
        # measurement updates must not act as caller cancellation while queued.
        # apply_stored retains live policy/electrical checks before dispatch and
        # lifecycle/safety checks through point and permission confirmation.
        return await self.control.request_enabled(
            target, True, fence=lambda: self.epochs.get(target, 0) == epoch
        )

    def pv_startup_valid(self, target, epoch):
        context = self.pv_startups.get(target)
        runtime = self.control.runtime
        state = runtime.get(target.station)
        enabled = runtime.enabled_observation(target)
        return bool(
            context
            and not self.closed
            and self.epochs.get(target, 0) == epoch
            and self.setting(target)["profile"] in ("PV_SURPLUS", "PV_OPTIMUM")
            and self.setting(target)["profile"] in self.available_profiles(target)
            and self.can_control(target)
            and state
            and state.connected
            and state.token == context[0]
            and enabled
            and enabled.revision == context[1]
            and self.control.intent(target).generation == context[2]
        )

    def pv_schedule_startup(self, target, epoch):
        runtime = self.control.runtime
        self.pv_startups[target] = (
            runtime.get(target.station).token,
            runtime.enabled_observation(target).revision,
            self.control.intent(target).generation,
        )
        self.enable_requests.setdefault(target, asyncio.current_task())
        self.pv_retry_until[target] = self.monotonic() + 60
        self.status[target] = "awaiting_applied"
        self.tasks[target] = self.hass.async_create_background_task(
            self.pv_startup_sequence(target, epoch), "PV startup reconciliation"
        )

    async def pv_startup_sequence(self, target, epoch):
        try:
            while self.pv_startup_valid(target, epoch):
                await self.wait(max(0, self.pv_retry_until[target] - self.monotonic()))
                if not self.pv_startup_valid(target, epoch):
                    return
                if self.monotonic() < self.pv_retry_until[target]:
                    continue
                with cycle(self, target, "startup_retry"):
                    if (
                        self.pv_edit(target) is None
                        and self.setting(target)["profile"] == "PV_OPTIMUM"
                    ):
                        self.pv_retry_until[target] = self.monotonic() + 60
                        continue
                    # The edits below belong to this authorized retry. Other
                    # edits between attempts still revoke its captured generation.
                    context = self.pv_startups[target]
                    self.pv_startups[target] = (
                        *context[:2],
                        self.control.intent(target).generation + 1,
                    )
                    result = await self.pv_enable_attempt(target, epoch)
                    if self.epochs.get(target, 0) != epoch:
                        return
                    if result and result.status == CommandStatus.APPLIED:
                        self.enable_requests.pop(target, None)
                        self.pv_startups.pop(target, None)
                        self.pv_retry_until.pop(target, None)
                        self.pv_confirm(target)
                        self.tasks.pop(target, None)
                        self.launch(target)
                        return
                    if result and result.status != CommandStatus.TEMPORARILY_REJECTED:
                        return
                    self.pv_retry_until[target] = self.monotonic() + 60
                    self.status[target] = "awaiting_applied"
                    self.control.publish(target)
        finally:
            if self.tasks.get(target) is asyncio.current_task():
                self.tasks.pop(target, None)
                self.pv_startups.pop(target, None)
                self.enable_requests.pop(target, None)
                self.pv_retry_until.pop(target, None)
            self.control.publish(target)

    async def pv_sequence(self, target, epoch, *, stop_first=False):
        try:
            if stop_first and self.valid(target, epoch):
                with cycle(self, target, "safety_stop"):
                    # Do not let a quick recovery erase an already observed safety stop.
                    await self.control.apply_stored(
                        target,
                        fence=lambda: self.valid(target, epoch),
                        reuse_applied=True,
                    )
                    self.pv_confirm(target)
            while self.valid(target, epoch):
                await self.control.wait_for_pending_point(target)
                if not self.valid(target, epoch):
                    return
                started = self.monotonic()
                if event := self.optimum_wakes.get(target):
                    event.clear()
                with (
                    self.control.phase_probe(
                        target,
                        enabled=(
                            self.setting(target)["profile"] == "PV_OPTIMUM"
                            and self.control.phase_restricted(target)
                            and self.monotonic() >= self.pv_retry_until.get(target, 0)
                        ),
                    ),
                    cycle(self, target, "regulation"),
                ):
                    result = self.pv_edit(target)
                    point = result.point if result else None
                    intent = self.control.intent(target)
                    waiting_retry = (
                        self.monotonic() < self.pv_retry_until.get(target, 0)
                        and point
                        and point.charging
                        and not (
                            self.setting(target)["profile"] == "PV_OPTIMUM"
                            and self.control.phase_restricted(target)
                            and self.pv_phase_retry.get(target, False)
                        )
                    )
                    holding = (
                        self.status.get(target) == "pv_stop_delay"
                        and point
                        and point.same_setpoint(self.control.confirmed_point(target))
                    )
                    if (
                        point is not None
                        and target not in self.control.pending_points
                        and not holding
                        and not waiting_retry
                        and (
                            point != self.control.confirmed_point(target)
                            or intent.phase_retry
                            or target in self.control._unconfirmed_targets
                            or intent.command_result is None
                        )
                    ):
                        if (
                            point
                            and point.charging
                            and point != self.control.confirmed_point(target)
                            and self.pv_ongoing.get(target, False)
                            and not self.optimum_fast(target)
                        ):
                            await self.debounce_wait(1)
                            if not self.valid(target, epoch):
                                return
                            if self.pv_edit(target) is None:
                                continue
                        # Dispatch this iteration's prepared request. New sensor
                        # samples belong to the next cycle, not caller validity.
                        # The runtime retains live dispatch and safety checks.
                        await self.control.apply_stored(
                            target,
                            fence=lambda: self.valid(target, epoch),
                            reuse_applied=True,
                        )
                        phase_superseded = bool(
                            intent.command_result
                            and intent.command_result.reason == CommandReason.STALE
                            and intent.fence_reason == "pre_dispatch_desired_phase"
                        )
                        if intent.phase_retry or (
                            intent.command_result
                            and intent.command_result.status
                            == CommandStatus.TEMPORARILY_REJECTED
                            and not phase_superseded
                        ):
                            self.pv_retry_request[target] = intent.request
                            self.pv_phase_retry[target] = bool(
                                intent.phase_retry
                                and intent.command_result
                                and (
                                    intent.command_result.status
                                    == CommandStatus.APPLIED
                                    or intent.command_result.reason
                                    == CommandReason.PHASE_SWITCH_LOCKOUT
                                    # Local replanning is not station BUSY: keep
                                    # phase probes bounded but allow same-phase
                                    # correction on the next regulation cycle.
                                    or phase_superseded
                                )
                            )
                            if self.monotonic() >= self.pv_retry_until.get(target, 0):
                                self.pv_retry_until[target] = self.monotonic() + 60
                        else:
                            self.pv_retry_until.pop(target, None)
                            self.pv_retry_request.pop(target, None)
                            self.pv_phase_retry.pop(target, None)
                    self.pv_confirm(target)
                    self.control.publish(target)
                seconds = self.pv_wait_seconds(target)
                if self.optimum_fast(target):
                    seconds = min(
                        seconds,
                        max(0, FAST_OBSERVATION_SECONDS - (self.monotonic() - started)),
                    )
                if self.setting(target)["profile"] == "PV_OPTIMUM":
                    await self.optimum_wait(target, seconds)
                else:
                    await self.wait(seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("PV regulation failed")
            self.pv_ongoing[target] = False
            if self.valid(target, epoch):
                self.control._edit(target, {"target_w": Fraction(0)})
                try:
                    await self.control.apply_stored(
                        target, fence=lambda: self.valid(target, epoch)
                    )
                except Exception:
                    _LOGGER.exception("PV error stop failed")
            self.status[target] = "error"
        finally:
            if self.tasks.get(target) is asyncio.current_task():
                self.tasks.pop(target, None)
            self.control.publish(target)
