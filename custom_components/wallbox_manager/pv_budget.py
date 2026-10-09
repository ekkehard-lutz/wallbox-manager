"""Conservative power-flow evidence; timestamps are not synchronization proof."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

UNCERTAINTY_W = Fraction(100)
SETTLING_SECONDS = 20
IMPORT_CONFIRM_SECONDS = 10


def source_time(state):
    value = state.attributes.get("observed_at")
    at = datetime.fromisoformat(value) if value else state.last_updated
    # HA reports may be refreshed without a new device measurement. For sources
    # lacking acquisition metadata, last_reported is only a report generation.
    if not value:
        at = getattr(state, "last_reported", at)
    if at.tzinfo is None:
        raise ValueError("source timestamp requires timezone")
    return at


@dataclass(frozen=True)
class PowerEvidence:
    """EV first, followed by discharge/import/export/PV/total site load."""

    times: tuple[datetime, ...]
    pv: Fraction
    load: Fraction

    def __post_init__(self):
        if len(self.times) != 6 or any(t.tzinfo is None for t in self.times):
            raise ValueError("incomplete power evidence")
        if self.pv < 0 or self.load < 0:
            raise ValueError("invalid site power")

    @property
    def generation(self):
        return tuple(t.isoformat() for t in self.times)


def battery_budget(ev, discharge, limit, imported, exported, *, evidence, ev_floor):
    """Independent incremental discharge and site-balance bounds, with reserve.

    G=import-export; household=site_load-EV. Existing import is held constant
    for the battery bound, never increased by the minimum-import exception.
    Taking the lower estimate accounts for inconsistent power-flow inputs.
    The EV floor prevents a newer EV response spending older battery headroom.
    """
    ev, discharge, limit, imported, exported, ev_floor = (
        Fraction(str(value))
        for value in (ev, discharge, limit, imported, exported, ev_floor)
    )
    if min(ev, discharge, limit, imported, exported, ev_floor) < 0 or ev_floor > ev:
        raise ValueError("invalid hard-budget measurement")
    grid = imported - exported
    incremental = ev_floor + limit - discharge + max(0, -grid)
    flow = ev_floor + limit + evidence.pv - evidence.load + max(0, grid)
    return max(Fraction(0), min(incremental, flow) - UNCERTAINTY_W)
