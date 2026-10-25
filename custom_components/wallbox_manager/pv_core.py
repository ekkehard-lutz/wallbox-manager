"""Pure shared FAST/BALANCE controller; adapters own observations and execution."""

import math
from dataclasses import dataclass, field

from .grid_point import Point
from .pv_soc import soc_policy

ZERO = Point(0, None, 0)


def signed_storage(charge, discharge):
    if (
        charge is None
        or discharge is None
        or not math.isfinite(charge)
        or not math.isfinite(discharge)
        or min(charge, discharge) < 0
    ):
        raise ValueError("storage_power_invalid")
    return 0 if charge > 0 and discharge > 0 else charge - discharge


@dataclass(frozen=True)
class Inputs:
    ev: float
    imported: float
    exported: float
    battery: bool = True
    soc: float | None = None
    target: float | None = None
    storage: float | None = None
    surplus: bool = False


@dataclass(frozen=True)
class Decision:
    point: Point
    mode: str
    reason: str
    fields: dict = field(default_factory=dict)


class Controller:
    def __init__(self):
        self.mode = "STOP"
        self.startup = None
        self.up_cycles = 0
        self.down_cycles = None
        self.down_reason = None

    def sent(self, point, previous):
        """An accepted executable request starts timing; ACK is not consumption."""
        if point.amps > 0:
            if previous is not None and previous.amps == 0:
                self.startup = 0
            if previous is None or point.watts > previous.watts:
                self.up_cycles = 0
        else:
            self.startup = None
        self.down_cycles = self.down_reason = None

    def decide(
        self, inputs, settings, ranges, current, *, pending=False, economic_stop=False
    ):
        i, c = inputs, current
        held = c or ZERO
        context = {}
        if i.battery:
            if i.soc is None or i.target is None:
                raise ValueError("soc_target_invalid")
            policy = soc_policy(
                i.target, settings["soc_hysterese"], i.soc, self.mode, surplus=i.surplus
            )
            self.mode = policy.mode
            context = dict(
                soc_pct=i.soc,
                target_soc_pct=i.target,
                stop_pct=float(policy.stop),
                lower_pct=float(policy.low),
                middle_pct=float(policy.middle),
                upper_pct=float(policy.upper),
            )
        else:
            self.mode = "SURPLUS"
        if self.mode == "STOP":
            return Decision(ZERO, self.mode, "soc_stop", context)
        if economic_stop:
            return Decision(ZERO, self.mode, "import_budget")
        # Ordinary timing advances only in an eligible, resolved control context.
        settling = False
        if not pending:
            self.up_cycles += 1
            if self.startup is not None:
                self.startup += 1
                settling = self.startup <= settings["pv_startup_hold_cycles"]
        else:
            settling = (
                self.startup is not None
                and self.startup < settings["pv_startup_hold_cycles"]
            )
        if (
            i.battery
            and self.mode == "BALANCE"
            and not pending
            and not settling
            and i.soc < policy.low
            and c
            and c.phases == 1
            and c.amps == 6
            and 1 in ranges.ranges
            and c in ranges.points(1)
            and i.storage is not None
            and i.storage < 0
        ):
            self.mode = "STOP"
            return Decision(
                ZERO,
                self.mode,
                "balance_minimum_discharge",
                dict(**context, battery_w=i.storage, applied="1p/6A"),
            )
        if pending:
            return Decision(held, self.mode, "response_pending")
        if settling:
            return Decision(
                held,
                self.mode,
                "startup_hold",
                {"hold_cycles": settings["pv_startup_hold_cycles"]},
            )
        grid = i.imported - i.exported
        if self.mode == "FAST":
            context = dict(
                grid_w=grid, ev_actual_w=i.ev, deadband_w=settings["pv_grid_deadband_w"]
            )
            if grid > settings["pv_grid_deadband_w"]:
                ideal, reason = i.ev - grid, "grid_import"
            else:
                # Exploration is bounded by the next legal point, even at night
                # with a battery compensating demand and zero grid exchange.
                step = ranges.navigate(
                    held.display_watts, held.phases if held.amps > 0 else None, 1
                )
                ideal = float(step.watts) if step else float(held.watts)
                reason = "grid_exploration"
        elif self.mode == "BALANCE":
            if i.storage is None:
                raise ValueError("storage_power_invalid")
            ideal, reason = i.ev + i.storage, "battery_balance"
            context = dict(
                battery_w=i.storage,
                ev_actual_w=i.ev,
                soc_pct=i.soc,
                target_soc_pct=i.target,
            )
        else:
            ideal, reason = i.ev - grid, "grid_surplus"
            context = dict(grid_w=grid, ev_actual_w=i.ev)
        candidate = ranges.regulation_target(ideal, c)
        if self.mode == "BALANCE":
            bias = -1 if i.soc < policy.low else 1 if i.soc > policy.middle else 0
            # A deliberate step above a zero ideal is a SoC decision, not the
            # ordinary minimum continuation floor for an already positive offer.
            basis = ZERO if ideal == 0 and bias > 0 else candidate
            if bias and (basis.amps > 0 or bias > 0 and ideal == 0):
                candidate = (
                    ranges.navigate(basis.display_watts, basis.phases, bias)
                    or candidate
                )
                reason = "soc_recharge" if bias < 0 else "soc_discharge"
                context["bias_steps"] = bias
            if grid > settings["pv_grid_deadband_w"]:
                correction = ranges.regulation_target(i.ev - grid, c)
                if correction.watts < candidate.watts:
                    candidate, reason = correction, "balance_grid_import"
                    context = dict(
                        grid_w=grid,
                        ev_actual_w=i.ev,
                        deadband_w=settings["pv_grid_deadband_w"],
                    )
        if c and c.amps > 0 and candidate.watts > c.watts:
            self.down_cycles = self.down_reason = None
            if i.ev < float(c.watts) * settings["pv_ev_uptake_threshold_pct"] / 100:
                return Decision(
                    c,
                    self.mode,
                    "ev_underconsumption",
                    dict(
                        ev_actual_w=i.ev,
                        offered_w=float(c.watts),
                        uptake_pct=settings["pv_ev_uptake_threshold_pct"],
                    ),
                )
            if self.up_cycles < settings["pv_increase_interval_cycles"]:
                return Decision(c, self.mode, "increase_interval")
            candidate = ranges.navigate(c.display_watts, c.phases, 1) or c
        elif c and candidate.watts < c.watts:
            if self.down_reason != reason:
                self.down_cycles, self.down_reason = 0, reason
            else:
                self.down_cycles += 1
            if self.down_cycles < settings["pv_decrease_delay_cycles"]:
                return Decision(c, self.mode, "decrease_delay")
        else:
            self.down_cycles = self.down_reason = None
        return Decision(candidate, self.mode, reason, context)
