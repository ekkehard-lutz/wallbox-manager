"""Relative NETZ schedules, executed exclusively through existing control intents."""

import asyncio
import re
from datetime import UTC, datetime
from fractions import Fraction

from .control.commands import CommandStatus


def duration_seconds(value):
    if value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2,}:[0-5][0-9]", value):
        raise ValueError("duration must be hh:mm (hours may exceed 23)")
    hours, minutes = map(int, value.split(":"))
    return hours * 3600 + minutes * 60


def duration_text(value):
    return "" if value is None else f"{value // 3600:02d}:{value % 3600 // 60:02d}"


class GridTiming:
    def grid_arm(self, target):
        settings = self.setting(target)
        if settings.get("grid_request") and not settings["grid_request"].get(
            "stopping"
        ):
            return
        settings.pop("grid_request", None)
        delay = settings.get("grid_start_delay")
        duration = settings.get("grid_duration")
        settings["grid_timing_state"] = "idle"
        if delay is None and duration is None:
            return
        now = self.wall_time()
        start = now + (delay or 0)
        settings["grid_request"] = {
            "activated_at": now,
            "start_at": start,
            "end_at": (now if duration == 0 else start + duration)
            if duration is not None
            else None,
            "duration": duration,
        }
        settings["grid_start_delay"] = None
        settings["grid_duration"] = None

    def grid_consume(self, target, state):
        settings = self.setting(target)
        if settings.pop("grid_request", None) is not None:
            settings["grid_start_delay"] = None
            settings["grid_duration"] = None
            settings["grid_timing_state"] = state

    def grid_phase(self, target):
        request = self.setting(target).get("grid_request")
        if request is None:
            return "active", None
        now = self.wall_time()
        end = request["end_at"]
        if request.get("stopping") or (end is not None and now >= end):
            return "expired", None
        if now < request["start_at"]:
            return "waiting", request["start_at"]
        return "active", end

    def grid_target(self, target):
        phase, _ = self.grid_phase(target)
        return (
            Fraction(str(self.setting(target)["power_kw"])) * 1000
            if phase == "active"
            else Fraction(0)
        )

    def grid_attributes(self, target):
        settings = self.setting(target)
        request = settings.get("grid_request") or {}
        activation = request.get("activated_at")
        phase, _ = self.grid_phase(target)
        return {
            "grid_activation_time": datetime.fromtimestamp(activation, UTC).isoformat()
            if activation is not None
            else None,
            "grid_start_delay_seconds": settings.get("grid_start_delay"),
            "grid_duration_seconds": settings.get("grid_duration"),
            "grid_request_armed": bool(request),
            "grid_start_deadline": request.get("start_at"),
            "grid_end_deadline": request.get("end_at"),
            "grid_armed_duration_seconds": request.get("duration"),
            "grid_timing_state": ("stopping" if request.get("stopping") else phase)
            if request
            else settings.get("grid_timing_state", "idle"),
        }

    def grid_launch_timer(self, target, *, retry=False):
        phase, deadline = self.grid_phase(target)
        if phase != "active":
            self.status[target] = f"grid_{phase}"
        needs_apply = self.control.intent(target).request.target_w != self.grid_target(
            target
        )
        if target in self.grid_timers or (
            deadline is None
            and phase != "expired"
            and not needs_apply
            and not self.setting(target).get("grid_request")
        ):
            return
        self.grid_timers[target] = self.hass.async_create_background_task(
            self.grid_timer(target, self.epochs.get(target, 0), retry=retry),
            "NETZ timing",
        )

    async def grid_timer(self, target, epoch, *, retry=False):
        def current():
            return (
                self.epochs.get(target, 0) == epoch
                and self.can_control(target)
                and self.setting(target)["profile"] == "NETZ"
                and (
                    self.setting(target).get("grid_request")
                    or (self.valid(target, epoch) and target not in self.suppressed)
                )
            )

        try:
            phase, deadline = self.grid_phase(target)
            if (
                (
                    phase == "active"
                    and deadline is None
                    and self.setting(target).get("grid_request")
                )
                or phase == "expired"
                or self.control.intent(target).request.target_w
                != self.grid_target(target)
            ):
                deadline = self.wall_time()
            while current():
                if not retry and deadline is None:
                    return
                await self.timer_wait(
                    60 if retry else max(0, deadline - self.wall_time())
                )
                if not current():
                    return
                phase, deadline = self.grid_phase(target)
                if phase == "expired":
                    await self.permission(target, False, _grid_expiry=True)
                    return
                if phase == "active" and deadline is None:
                    self.grid_consume(target, "consumed")
                    await self.save()
                    if not current():
                        return
                self.control._edit(target, {"target_w": self.grid_target(target)})
                result = await self.control.apply_stored(
                    target, fence=current, reuse_applied=True
                )
                if not current():
                    return
                self.status[target] = f"grid_{phase}"
                self.control.publish(target)
                retry = result is None or result.status != CommandStatus.APPLIED
                if not retry:
                    if phase == "expired":
                        return
                    if phase == "active" and target not in self.tasks:
                        self.launch(target)
        finally:
            if self.grid_timers.get(target) is asyncio.current_task():
                self.grid_timers.pop(target, None)
