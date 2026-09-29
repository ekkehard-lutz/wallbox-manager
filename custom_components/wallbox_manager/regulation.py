"""Central regulation options with deterministic, lossless legacy migration."""

DEFAULTS = {
    "regulation_interval": 5,
    "pv_start_delay": 0,
    "pv_stop_delay": 60,
    "soc_hysterese": 5,
    "power_smoothing_window": 5,
}


def migrate_regulation(options, profiles):
    """Explicit options win; otherwise first sorted stored target wins per key.

    Original per-target values are archived in options for audit/recovery.
    """
    result = dict(options)
    if profiles and "legacy_regulation" not in result:
        result["legacy_regulation"] = {
            key: {field: value for field, value in profile.items() if field in DEFAULTS}
            for key, profile in profiles.items()
        }
    for field, default in DEFAULTS.items():
        result.setdefault(
            field,
            next(
                (
                    profiles[key][field]
                    for key in sorted(profiles)
                    if field in profiles[key]
                ),
                default,
            ),
        )
    return result
