"""Power-only regulation primitives; callers own SoC policy and input freshness."""

from dataclasses import dataclass
from fractions import Fraction

from .pv_input_diagnostics import diagnostic_fast_request

GRID_DEADBAND_W = 100
IMPORT_GRACE_SECONDS = 3


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
    graced_import=0,
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
    grace = Fraction(str(graced_import))
    if not 0 <= grace <= max(0, limit - discharge):
        raise ValueError("invalid transient import allowance")
    if deficit or excess_discharge:
        return max(Fraction(0), wallbox - max(0, deficit - grace) - excess_discharge)
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
    import_since: float | None = None

    @diagnostic_fast_request
    def request(self, wallbox, discharge, limit, imported, exported, *, now, interval):
        if interval <= 0:
            raise ValueError("invalid regulation interval")
        elapsed = interval if self.updated_at is None else max(0, now - self.updated_at)
        increase = Fraction(str(1 - 0.75 ** (elapsed / interval)))
        # Validate before changing transient state; invalid evidence breaks grace.
        try:
            wallbox, discharge, limit, imported, exported = (
                Fraction(str(value))
                for value in (wallbox, discharge, limit, imported, exported)
            )
            if min(wallbox, discharge, limit, imported, exported) < 0:
                raise ValueError("negative power measurement")
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            self.import_since = None
            raise
        net_import = imported - exported
        headroom = max(Fraction(0), limit - discharge)
        recovering = self.import_since is not None and net_import <= GRID_DEADBAND_W
        allowance = Fraction(0)
        if net_import > GRID_DEADBAND_W:
            if self.import_since is None:
                self.import_since = now
            if (
                headroom > GRID_DEADBAND_W
                and now - self.import_since < IMPORT_GRACE_SECONDS
            ):
                allowance = min(net_import, headroom)
        else:
            self.import_since = None
        if recovering:
            # The disappearance of a transient is not an upward charging request.
            increase = Fraction(0)
            self.requested = wallbox
        self.requested = fast_discharge(
            wallbox,
            discharge,
            limit,
            imported,
            exported,
            deadband=GRID_DEADBAND_W,
            increase_fraction=increase,
            previous_request=self.requested,
            graced_import=allowance,
        )
        # Keep persistent arithmetic bounded across thousands of evaluations.
        self.requested = Fraction(int(self.requested * 1000), 1000)
        self.updated_at = now
        return self.requested
