"""Prospective tick accounting and persistent economic STOP, independent of SoC."""

import math
from dataclasses import dataclass


@dataclass
class Budget:
    scope: list | None = None
    wh: float = 0
    stopped: bool = False
    reset: bool = False
    cause: str | None = None
    # These interval endpoints are deliberately never restored across downtime.
    previous: tuple | None = None
    low_since: float | None = None

    def saved(self):
        return dict(
            version=1,
            scope=self.scope,
            wh=self.wh,
            stopped=self.stopped,
            reset=self.reset,
            cause=self.cause,
        )

    @classmethod
    def restore(cls, record):
        if not record:
            return cls()
        if (
            not isinstance(record, dict)
            or record.get("version") != 1
            or not (
                isinstance(record.get("scope"), list) or record.get("scope") is None
            )
            or type(record.get("wh")) not in (int, float)
            or not math.isfinite(record["wh"])
            or record["wh"] < 0
            or type(record.get("stopped")) is not bool
        ):
            # Unknown history must never create a fresh positive allowance.
            return cls(scope=["unreconciled"], stopped=True, cause="recovery_unknown")
        return cls(
            scope=record["scope"],
            wh=record["wh"],
            stopped=record["stopped"],
            reset=False,
            cause=record.get("cause"),
        )

    def interrupt(self):
        self.previous = self.low_since = None
        # A completed reset is retained during normal operation; restored resets
        # are not trusted without new uninterrupted evidence after downtime.

    def bind(self, scope, *, ended=False):
        if self.scope is None or ended:
            self.scope, self.wh, self.stopped, self.reset, self.cause = (
                scope,
                0,
                False,
                False,
                None,
            )
            self.interrupt()
        return self.scope == scope

    def sample(self, now, imported, minimum, *, active, valid=True):
        if not valid:
            self.interrupt()
            return
        threshold = minimum / 2
        if self.previous is not None:
            before, power, prior_threshold, prior_active = self.previous
            dt = max(0, now - before)
            if prior_active and active and power > prior_threshold:
                self.wh += power * dt / 3600
        if imported <= threshold:
            if self.low_since is None:
                self.low_since = now
            if now - self.low_since > 15:
                self.wh, self.reset = 0, True
        else:
            self.low_since = None
            self.reset = False
        self.previous = (now, imported, threshold, active)

    def exhaust(self, limit):
        if self.wh >= limit:
            self.stopped, self.reset, self.cause = True, False, "import_budget"
        return self.stopped

    def restart_reason(
        self,
        *,
        off,
        mode,
        battery_w=None,
        household=None,
        house_limit=500,
        surplus=False,
        battery=True,
    ):
        if not self.stopped or not self.reset or not off or mode == "STOP":
            return None
        if battery and battery_w is not None and battery_w > 0:
            return "battery_charging"
        if (
            battery
            and mode == "FAST"
            and household is not None
            and household < house_limit
        ):
            return "fast_low_household"
        if not battery and surplus:
            return "grid_surplus"
        return None

    def restarted(self):
        self.stopped, self.reset, self.cause = False, False, None
