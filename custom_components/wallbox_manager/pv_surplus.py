"""PV policy and measurement adapter; execution belongs to ControlRuntime."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from fractions import Fraction

from .control.requests import Direction
from .control.runtime import PowerSettings
from .core.capabilities import EvidenceState

PV_DEFAULTS = {
    "approximation": "down",
    "soll_soc_speicher": 95,
    "soc_hysterese": 5,
    "regulation_interval": 5,
    "pv_start_delay": 0,
    "pv_stop_delay": 60,
}
MAX_AGE_SECONDS = 90
_LOGGER = logging.getLogger(__name__)


def reading(state, now, *, soc=False):
    """Reject missing units, non-finite values, old and future observations."""
    if state is None:
        raise ValueError("missing measurement")
    value = Fraction(state.state)
    unit = state.attributes.get("unit_of_measurement")
    units = {"%": 1} if soc else {"W": 1, "kW": 1000, "MW": 1000000}
    if unit not in units:
        raise ValueError("invalid unit")
    timestamp = getattr(state, "last_reported", state.last_updated)
    if not 0 <= (now - timestamp).total_seconds() <= MAX_AGE_SECONDS:
        raise ValueError("stale measurement")
    expiry = state.attributes.get("valid_until")
    if expiry and datetime.fromisoformat(expiry) <= now:
        raise ValueError("expired measurement")
    value *= units[unit]
    if soc and not 0 <= value <= 100:
        raise ValueError("invalid SOC")
    return value


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

    def permits_point(self, target, point):
        """Apply the existing PV policy at the shared command dispatch fence."""
        if self.setting(target)["profile"] != "PV_SURPLUS":
            return True
        if not point.charging:
            # OFF ends continuation even when another primitive control requested it.
            self.pv_ongoing[target] = False
            return True
        if self.closed or "PV_SURPLUS" not in self.available_profiles(target):
            return False
        power, direction, status, _ = self.pv_plan(target, advance=False)
        return (
            power > 0
            and (
                status != "pv_stop_delay"
                or self.control.intent(target).request.target_w == 1
            )
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
            try:
                soc = reading(event.data.get("new_state"), datetime.now(UTC), soc=True)
                power, _, status = decision(
                    Fraction(1), soc, settings, self.pv_ongoing.get(target, False)
                )
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                power, status = Fraction(0), "measurements_unavailable"
            if power > 0:
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

    def pv_measurements(self, target):
        now = datetime.now(UTC)
        pv_state = self.hass.states.get(self.references.get("leistung_pv", ""))
        load_state = self.hass.states.get(
            self.references.get("leistung_verbraucher", "")
        )
        pv, load = reading(pv_state, now), reading(load_state, now)
        soc_entity = self.references.get("soc_speicher_aktuell")
        soc = (
            reading(self.hass.states.get(soc_entity), now, soc=True)
            if soc_entity
            else None
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
        return pv - load + actual, soc

    def pv_request(self, target):
        session = self.control.runtime.sessions.get(target)
        identity = session.session_id if session and session.active else None
        if identity != self.pv_sessions.get(target):
            self.pv_ongoing[target] = False
        self.pv_sessions[target] = identity
        try:
            available, soc = self.pv_measurements(target)
            return decision(
                available, soc, self.setting(target), self.pv_ongoing.get(target, False)
            )
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            return Fraction(0), Direction.DOWN, "measurements_unavailable"

    def pv_plan(self, target, *, advance=True):
        """Time policy around the common solver, never a second electrical solver."""
        power, direction, status = self.pv_request(target)
        settings = self.setting(target)
        now = self.monotonic()
        inputs = self.control.inputs(target)
        if "PV_SURPLUS" not in self.available_profiles(target):
            power, status = Fraction(0), "profile_unavailable"
        elif inputs is None or inputs.capabilities.stop.state != EvidenceState.VERIFIED:
            power, status = Fraction(0), "safe_stop_unavailable"
        maximum = self.control.power_ceiling(target)
        if maximum is not None:
            power = min(power, maximum)

        def resolve(watts, policy):
            return self.control.resolve(target, request=PowerSettings(watts, policy))[1]

        result = resolve(power, direction)
        if (
            status == "actively_charging"
            and result
            and result.point
            and not result.point.charging
        ):
            status = "paused_insufficient_pv"
        ongoing = self.pv_ongoing.get(target, False)
        if status == "paused_insufficient_pv" and ongoing:
            minimum = resolve(Fraction(1), Direction.UP)
            if advance:
                self.pv_start_since.pop(target, None)
                self.pv_stop_since.setdefault(target, now)
            since = self.pv_stop_since.get(target)
            if (
                since is not None
                and now < since + settings["pv_stop_delay"]
                and minimum
                and minimum.point
                and minimum.point.charging
            ):
                return Fraction(1), Direction.UP, "pv_stop_delay", minimum
        elif advance:
            self.pv_stop_since.pop(target, None)
        if (
            status == "actively_charging"
            and result
            and result.point
            and result.point.charging
        ):
            if not ongoing and settings["pv_start_delay"] > 0:
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
        power, direction, status, result = self.pv_plan(target)
        intent = self.control.intent(target)
        intent.profile_modes = None
        if intent.request.target_w != power or intent.request.direction != direction:
            self.control._edit(target, {"target_w": power, "direction": direction})
        self.status[target] = status
        if status not in ("actively_charging", "pv_stop_delay"):
            self.pv_ongoing[target] = False
        return result

    def pv_wait_seconds(self, target):
        now = self.monotonic()
        settings = self.setting(target)
        deadlines = [settings["regulation_interval"]]
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
        """Safety failures wake immediately; a waiting start observes fresh inputs."""
        entity = event.data.get("entity_id")
        if self.closed:
            return
        state = event.data.get("new_state")
        for target in tuple(self.tasks):
            if self.setting(target)["profile"] != "PV_SURPLUS":
                continue
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
                self.invalidate(target)
                self.control._edit(
                    target, {"target_w": Fraction(0), "direction": Direction.DOWN}
                )
                self.status[target] = "measurements_unavailable"
                if self.valid(target, self.epochs[target]):
                    self.launch(target, stop_first=True)
                continue
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
            self.status.get(target) in ("actively_charging", "pv_stop_delay")
            and point
            and point.charging
            and session
            and session.active
        )
        if self.pv_ongoing[target]:
            self.pv_start_since.pop(target, None)
        if (
            self.status.get(target) == "actively_charging"
            and not self.pv_ongoing[target]
        ):
            self.status[target] = "awaiting_applied"

    async def pv_sequence(self, target, epoch, *, stop_first=False):
        try:
            if stop_first and self.valid(target, epoch):
                # Do not let a quick recovery erase an already observed safety stop.
                await self.control.apply_stored(
                    target, fence=lambda: self.valid(target, epoch), reuse_applied=True
                )
                self.pv_confirm(target)
            while self.valid(target, epoch):
                result = self.pv_edit(target)
                point = result.point if result else None
                intent = self.control.intent(target)
                waiting_retry = (
                    intent.phase_retry
                    and self.pv_retry_request.get(target) == intent.request
                    and self.monotonic() < self.pv_retry_until.get(target, 0)
                    and point
                    and point.charging
                )
                if not waiting_retry and (
                    point != self.control.confirmed_point(target)
                    or intent.phase_retry
                    or intent.command_result is None
                ):
                    if (
                        point
                        and point.charging
                        and point != self.control.confirmed_point(target)
                        and self.pv_ongoing.get(target, False)
                    ):
                        await self.debounce_wait(1)
                        if not self.valid(target, epoch):
                            return
                        self.pv_edit(target)
                    expected = self.pv_request(target)
                    stopping = self.control.intent(target).request.target_w == 0
                    await self.control.apply_stored(
                        target,
                        fence=lambda stopping=stopping, expected=expected: (
                            self.valid(target, epoch)
                            and (stopping or self.pv_request(target) == expected)
                        ),
                        reuse_applied=True,
                    )
                    if intent.phase_retry:
                        self.pv_retry_request[target] = intent.request
                        if self.monotonic() >= self.pv_retry_until.get(target, 0):
                            self.pv_retry_until[target] = self.monotonic() + 60
                    else:
                        self.pv_retry_until.pop(target, None)
                        self.pv_retry_request.pop(target, None)
                self.pv_confirm(target)
                self.control.publish(target)
                await self.wait(self.pv_wait_seconds(target))
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
