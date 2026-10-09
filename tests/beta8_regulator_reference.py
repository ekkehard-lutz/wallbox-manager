"""Frozen beta.8 stateful ramp for reproducible before/after experiments only.

Copied from eb792c3 pv_regulators.py. Never imported by integration code.
The stateless arithmetic below is unchanged between beta.8 and beta.9.
"""

from dataclasses import dataclass
from fractions import Fraction

from custom_components.wallbox_manager.pv_regulators import (
    GRID_DEADBAND_W,
    IMPORT_GRACE_SECONDS,
    fast_discharge,
)


@dataclass
class Beta8Regulator:
    """Time-based smoothing survives unrealizable low requests without a solver.

    Repeated planning/dispatch-fence calls at the same instant cannot accelerate
    the ramp. No SoC, phase, current or profile lifecycle policy is stored here.
    """

    requested: Fraction | None = None
    updated_at: float | None = None
    import_since: float | None = None

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
