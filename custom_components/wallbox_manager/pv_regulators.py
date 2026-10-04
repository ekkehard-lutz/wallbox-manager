"""Power-only regulation primitives; callers own SoC policy and input freshness."""

from dataclasses import dataclass
from fractions import Fraction


def pv_balance(pv_power, consumer_power, wallbox_power):
    """Available watts; consumer measurement includes the controlled wallbox.

    Preserve negative balance for the caller's existing pause/delay policy.
    Approximation and electrical operating points belong to the common solver.
    """
    return pv_power - consumer_power + wallbox_power


def fast_discharge(
    wallbox_power,
    battery_discharge,
    max_discharge,
    grid_import,
    grid_export,
    *,
    deadband,
    increase_fraction,
    previous_request=None,
):
    """Request watts with gradual increases and immediate deficit correction.

    Inputs are fresh nonnegative watts, with battery discharge excluding charging.
    The caller supplies tuning; no profile targets or timing state live here.
    Meaningful import suppresses *all* increases, including battery headroom.
    Discharge above the policy limit also reduces the request immediately.
    """
    values = tuple(
        Fraction(str(value))
        for value in (
            wallbox_power,
            battery_discharge,
            max_discharge,
            grid_import,
            grid_export,
            deadband,
            increase_fraction,
        )
    )
    wallbox, discharge, limit, imported, exported, band, increase = values
    if any(value < 0 for value in values) or not 0 <= increase <= 1:
        raise ValueError("invalid regulation measurement or limit")
    net_import = imported - exported
    deficit = net_import if net_import > band else Fraction(0)
    excess_discharge = max(Fraction(0), discharge - limit)
    if deficit or excess_discharge:
        return max(Fraction(0), wallbox - deficit - excess_discharge)
    export = -net_import if net_import < -band else Fraction(0)
    headroom = max(Fraction(0), limit - discharge)
    ceiling = wallbox + export + headroom
    previous = wallbox if previous_request is None else Fraction(str(previous_request))
    if previous < 0:
        raise ValueError("negative previous request")
    baseline = min(ceiling, max(wallbox, previous))
    return baseline + increase * (ceiling - baseline)


@dataclass
class FastDischargeRegulator:
    """Time-based smoothing survives unrealizable low requests without a solver.

    Repeated planning/dispatch-fence calls at the same instant cannot accelerate
    the ramp. No SoC, phase, current or profile lifecycle policy is stored here.
    """

    requested: Fraction | None = None
    updated_at: float | None = None

    def request(self, wallbox, discharge, limit, imported, exported, *, now, interval):
        if interval <= 0:
            raise ValueError("invalid regulation interval")
        elapsed = interval if self.updated_at is None else max(0, now - self.updated_at)
        increase = Fraction(str(1 - 0.75 ** (elapsed / interval)))
        self.requested = fast_discharge(
            wallbox,
            discharge,
            limit,
            imported,
            exported,
            deadband=100,
            increase_fraction=increase,
            previous_request=self.requested,
        )
        # Keep persistent arithmetic bounded across thousands of evaluations.
        self.requested = Fraction(int(self.requested * 1000), 1000)
        self.updated_at = now
        return self.requested
