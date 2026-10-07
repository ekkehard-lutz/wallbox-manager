"""Opt-in Wallbox Manager diagnostics; never participate in control decisions."""

import asyncio
import json
import logging
from contextvars import ContextVar
from functools import wraps

# Retained for compatibility with persisted integration options.
OPTION = "pv_diagnostic_logging"
LEVEL_OPTION = "diagnostic_level"
_EVENTS = {}
_LOGGER = logging.getLogger(__name__)
_RECOVERY = ContextVar("wallbox_recovery_diagnostics", default=None)


def diagnostic_level(entry):
    options = entry.options
    value = options.get(LEVEL_OPTION, 3 if options.get(OPTION, False) else 0)
    if isinstance(value, str) and value in ("0", "1", "2", "3"):
        value = int(value)
    level = value if type(value) is int and 0 <= value <= 3 else 0
    cached = _EVENTS.get(id(entry))
    if cached is not None and cached[1] != level:
        reset_diagnostics(entry)
    return level


def migrate_diagnostics(options):
    """An explicit level wins; legacy enabled means the complete trace."""
    from types import SimpleNamespace

    data = dict(options)
    data[LEVEL_OPTION] = diagnostic_level(SimpleNamespace(options=data))
    data.pop(OPTION, None)
    return data


def reset_diagnostics(entry):
    _EVENTS.pop(id(entry), None)


def setpoint(value):
    if not isinstance(value, dict):
        return value
    return {key: value.get(key) for key in ("phases", "current_a", "charging")}


# Payload fields never implicitly become identity. In particular watts, SoC,
# voltage, timestamps, generation counters and delay countdowns are excluded.
_SEMANTIC = frozenset(
    {
        "event",
        "stage",
        "profile",
        "phase",
        "mode",
        "initialized",
        "previous_mode",
        "target_profile",
        "date",
        "decision",
        "reason",
        "result",
        "status",
        "optimum_mode",
        "pv_day_state",
        "enabled",
        "connected",
        "authority",
        "control_authority",
        "ownership",
        "profile_status",
        "control_status",
        "minimum_positive_hold",
        "deliberate_pause",
        "stop_delay_holding",
        "phase_transition_blocked",
        "phase_lockout",
        "startup_pending",
        "reconciliation_required",
        "command_fence_reason",
        "fresh",
        "quantity",
        "command_status",
        "command_reason",
        "pending",
        "retrying",
    }
)
_POINTS = frozenset({"desired", "executable", "applied", "selected", "commanded"})
_COMMAND_EVENTS = frozenset(
    {
        "enable_requested",
        "disable_requested",
        "enable_retry",
        "enable_confirmed",
        "enable_failed",
        "disable_failed",
        "command_attempt",
        "command_outcome",
    }
)


def event_record(entry, subsystem, fields, *, force=False):
    """Build one centrally deduplicated event; data collection cannot affect control."""
    level = diagnostic_level(entry)
    state = _EVENTS.get(id(entry))
    if state is None or state[0] is not entry or state[1] != level:
        state = (entry, level, {})
        _EVENTS[id(entry)] = state
    if not level:
        return None
    scope = {
        key: fields[key] for key in ("station", "evse", "connector") if key in fields
    }
    semantic = {key: value for key, value in fields.items() if key in _SEMANTIC}
    if fields.get("event") == "target_soc":
        # A scheduled planner target change is itself an event. Live targets in
        # SoC-mode/cycle context must never create measurement-only events.
        semantic["target_soc"] = fields.get("target_soc")
    semantic.update(
        {key: setpoint(value) for key, value in fields.items() if key in _POINTS}
    )
    family = fields.get("event", fields.get("stage", "decision"))
    key = (subsystem, tuple(scope.items()), family)
    fingerprint = json.dumps(semantic, sort_keys=True, allow_nan=False)
    if not force and family not in _COMMAND_EVENTS and state[2].get(key) == fingerprint:
        return None
    state[2][key] = fingerprint
    return {**scope, **semantic} if level == 1 else dict(fields)


def cycle_event(entry, data):
    """Level 1/2 project full cycles onto stable control state and real commands."""
    explanatory = {
        "target_soc",
        "optimum_target_soc",
        "lower_stop_threshold",
        "pv_start_threshold",
        "fast_start_threshold",
        "pv_start_evidence",
        "raw_pv_power_w",
        "raw_consumption_power_w",
        "site_load_w",
        "surplus_w",
        "measured_power_w",
        "raw_regulator_target_w",
        "optimum_mode",
        "pv_day_state",
        "external",
        "delays",
        "retry_remaining_s",
        "policy_reason",
        "phase_transition_blocked",
        "stop_delay_holding",
        "minimum_positive_hold",
        "deliberate_pause",
        "startup_pending",
        "reconciliation_required",
        "command_fence_reason",
        "control_status",
        "station",
        "evse",
        "connector",
        "profile",
        "enabled",
    }
    fields = {
        key: value
        for key, value in data.items()
        if key in _SEMANTIC or key in _POINTS or key in explanatory
    }
    if "external" in fields:
        fields["external"] = {
            key: {
                name: value
                for name, value in sample.items()
                if name in ("value", "freshness", "unit")
            }
            for key, sample in fields["external"].items()
            if sample.get("entity_id")
        }
    fields["retrying"] = data.get("retry_remaining_s", 0) > 0
    fields["event"] = "decision"
    # START/INCREASE/HOLD are descriptions of this evaluation, not stable state.
    fields["decision"] = data.get("policy_reason", data.get("reason"))
    fields["control_status"] = (
        "pending" if data.get("startup_pending") else data.get("control_status")
    )
    fields.pop("command_status", None)
    fields.pop("command_reason", None)
    diagnostic_event(entry, "pv", **fields)


def recovery_record(stage, **fields):
    """Emit only inside an enabled recovery attempt, without leaking child tasks."""
    context = _RECOVERY.get()
    if (
        context is None
        or context[0] is not asyncio.current_task()
        or not diagnostic_level(context[2])
    ):
        return
    try:
        data = {**context[1], "stage": stage, **fields}
        if diagnostic_level(context[2]) < 3:
            data = event_record(context[2], "recovery", data)
        if data is not None:
            _LOGGER.info(
                "WBMGR subsystem=recovery %s",
                json.dumps(
                    data, sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
            )
    except Exception:
        pass  # Diagnostic failures must never affect recovery.


def recovery_snapshot(stage, collect):
    """Keep optional evidence collection lazy and isolated from control."""
    context = _RECOVERY.get()
    if (
        context is None
        or context[0] is not asyncio.current_task()
        or not diagnostic_level(context[2])
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
    try:
        if not diagnostic_level(entry):
            event_record(entry, subsystem, fields)
            return
        if diagnostic_level(entry) < 3:
            fields = event_record(entry, subsystem, fields)
            if fields is None:
                return
        _LOGGER.info(
            "WBMGR subsystem=%s %s",
            subsystem,
            json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False),
        )
    except Exception:
        pass


def profile_event(profile, target, event, **fields):
    """One semantic lifecycle boundary, independent of diagnostic cycles."""
    diagnostic_event(
        profile.entry,
        "control",
        station=target.station.value,
        evse=target.evse.value,
        connector=target.value,
        event=event,
        profile=profile_name(profile, target),
        **fields,
    )


def command_event(control, target, event, *, operating_point=None, result=None):
    """Observe real operation attempts/outcomes, not reuse of confirmations."""
    try:
        if result is not None and result.status.value == "failed":
            _LOGGER.error(
                "Wallbox control operation failed: %s (%s)",
                result.reason,
                result.detail,
            )
        profile = getattr(control, "profiles", None)
        if profile is None:
            return

        from .pv_diagnostics import point

        profile_event(
            profile,
            target,
            event,
            commanded=point(operating_point),
            command_status=result.status.value if result else None,
            command_reason=result.reason.value if result and result.reason else None,
            generation=control.intent(target).generation,
            command_fence_reason=control.intent(target).fence_reason,
            enabled=control.runtime.enabled(target),
        )
    except Exception:
        pass
