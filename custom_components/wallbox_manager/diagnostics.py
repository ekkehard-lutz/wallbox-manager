"""Opt-in Wallbox Manager diagnostics; never participate in control decisions."""

import asyncio
import json
import logging
from contextvars import ContextVar
from functools import wraps

# Retained for compatibility with persisted integration options.
OPTION = "pv_diagnostic_logging"
_LOGGER = logging.getLogger(__name__)
_RECOVERY = ContextVar("wallbox_recovery_diagnostics", default=None)


def recovery_record(stage, **fields):
    """Emit only inside an enabled recovery attempt, without leaking child tasks."""
    context = _RECOVERY.get()
    if (
        context is None
        or context[0] is not asyncio.current_task()
        or not context[2].options.get(OPTION, False)
    ):
        return
    try:
        data = {**context[1], "stage": stage, **fields}
        _LOGGER.info(
            "WBMGR subsystem=recovery %s",
            json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
    except Exception:
        pass  # Diagnostic failures must never affect recovery.


def recovery_snapshot(stage, collect):
    """Keep optional evidence collection lazy and isolated from control."""
    context = _RECOVERY.get()
    if (
        context is None
        or context[0] is not asyncio.current_task()
        or not context[2].options.get(OPTION, False)
    ):
        return
    try:
        recovery_record(stage, **collect())
    except Exception:
        recovery_record(stage, reason="diagnostic_snapshot_failed")


def diagnostic_recovery(method):
    @wraps(method)
    async def wrapped(self, target, *args, **kwargs):
        profile = getattr(self, "profiles", self)
        entry = getattr(profile, "entry", None)
        if not entry:
            return await method(self, target, *args, **kwargs)
        existing = _RECOVERY.get()
        if existing and existing[0] is asyncio.current_task():
            return await method(self, target, *args, **kwargs)
        token = _RECOVERY.set(
            (
                asyncio.current_task(),
                {
                    "station": target.station.value,
                    "evse": target.evse.value,
                    "connector": target.value,
                },
                entry,
            )
        )
        try:
            return await method(self, target, *args, **kwargs)
        finally:
            _RECOVERY.reset(token)

    return wrapped


def profile_name(profile, target):
    """Read without invoking the profile's default-creating setting accessor."""
    from .profiles import target_key

    return profile.settings.get(target_key(target), {}).get("profile")


def diagnostic_event(entry, subsystem, **fields):
    """Transition-only callers share the existing opt-in diagnostic switch."""
    if not entry.options.get(OPTION, False):
        return
    try:
        _LOGGER.info(
            "WBMGR subsystem=%s %s",
            subsystem,
            json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
    except Exception:
        pass
