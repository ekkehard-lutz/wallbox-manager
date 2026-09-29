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
    def grid_phase(self, target):
        settings = self.setting(target)
        activation = settings.get("grid_activated_at")
        if activation is None:
            return "active", None
        start = activation + (settings.get("grid_start_delay") or 0)
        now = self.wall_time()
        if now < start:
            return "waiting", start
        duration = settings.get("grid_duration")
        if duration is not None:
            end = start + duration
            return ("active", end) if now < end else ("expired", None)
        return "active", None

    def grid_restart(self, target):
        self.setting(target)["grid_activated_at"] = self.wall_time()

    def grid_target(self, target):
        phase, _ = self.grid_phase(target)
        return (
            Fraction(str(self.setting(target)["power_kw"])) * 1000
            if phase == "active"
            else Fraction(0)
        )

    def grid_attributes(self, target):
        settings = self.setting(target)
        activation = settings.get("grid_activated_at")
        return {
            "grid_activation_time": datetime.fromtimestamp(activation, UTC).isoformat()
            if activation is not None
            else None,
            "grid_start_delay_seconds": settings.get("grid_start_delay"),
            "grid_duration_seconds": settings.get("grid_duration"),
            "grid_timing_state": self.grid_phase(target)[0],
        }

    def grid_launch_timer(self, target):
        phase, deadline = self.grid_phase(target)
        if phase != "active":
            self.status[target] = f"grid_{phase}"
        needs_apply = self.control.intent(target).request.target_w != self.grid_target(
            target
        )
        if target in self.grid_timers or (
            deadline is None and phase != "expired" and not needs_apply
        ):
            return
        self.grid_timers[target] = self.hass.async_create_background_task(
            self.grid_timer(target, self.epochs.get(target, 0)), "NETZ timing"
        )

    async def grid_timer(self, target, epoch):
        def current():
            return self.valid(target, epoch) and target not in self.suppressed

        try:
            phase, deadline = self.grid_phase(target)
            if phase == "expired" or self.control.intent(
                target
            ).request.target_w != self.grid_target(target):
                deadline = self.wall_time()
            retry = False
            while current():
                if not retry and deadline is None:
                    return
                await self.timer_wait(
                    60 if retry else max(0, deadline - self.wall_time())
                )
                if not current():
                    return
                phase, deadline = self.grid_phase(target)
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
