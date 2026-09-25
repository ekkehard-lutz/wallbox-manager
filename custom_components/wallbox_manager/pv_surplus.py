"""PV policy and measurement adapter; execution belongs to ControlRuntime."""

import asyncio
import logging
from datetime import UTC, datetime
from fractions import Fraction

from .control.requests import Direction
from .core.capabilities import EvidenceState

PV_DEFAULTS = {
    "approximation": "down",
    "soll_soc_speicher": 95,
    "soc_hysterese": 5,
    "regulation_interval": 5,
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
        if self.closed:
            return False
        power, direction, _ = self.pv_request(target)
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
        pv = reading(self.hass.states.get(self.references.get("leistung_pv", "")), now)
        load = reading(
            self.hass.states.get(self.references.get("leistung_verbraucher", "")), now
        )
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

    def pv_edit(self, target):
        power, direction, status = self.pv_request(target)
        inputs = self.control.inputs(target)
        if inputs is None or inputs.capabilities.stop.state != EvidenceState.VERIFIED:
            power, status = Fraction(0), "safe_stop_unavailable"
        maximum = self.control.power_ceiling(target)
        if maximum is not None:
            power = min(power, maximum)
        intent = self.control.intent(target)
        intent.profile_modes = None
        if intent.request.target_w != power or intent.request.direction != direction:
            self.control._edit(target, {"target_w": power, "direction": direction})
        _, result, blocked = self.control.resolve(target)
        # The common solver owns minimum-current feasibility and OFF selection.
        if (
            power > 0
            and direction == Direction.DOWN
            and result
            and result.point is None
            and blocked == "direction_unreachable"
        ):
            self.control._edit(target, {"target_w": Fraction(0)})
            _, result, _ = self.control.resolve(target)
        if (
            result
            and result.point
            and not result.point.charging
            and status == "actively_charging"
        ):
            status = "paused_insufficient_pv"
        self.status[target] = status
        if status != "actively_charging":
            self.pv_ongoing[target] = False
        return result

    def pv_confirm(self, target):
        point = self.control.confirmed_point(target)
        session = self.control.runtime.sessions.get(target)
        self.pv_ongoing[target] = bool(
            self.status.get(target) == "actively_charging"
            and point
            and point.charging
            and session
            and session.active
        )
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
                await self.wait(self.setting(target)["regulation_interval"])
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
