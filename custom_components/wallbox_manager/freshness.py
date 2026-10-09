"""HA report-age classes shared by input validation and diagnostics (seconds)."""

LIVE_FRESHNESS = 90
SLOW_FRESHNESS = 900


def freshness_for(reference):
    """Only explicitly classified planning inputs may use the longer window."""
    return SLOW_FRESHNESS if reference == "remaining_pv_energy" else LIVE_FRESHNESS
