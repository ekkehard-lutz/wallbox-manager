"""Read-only, opt-in PV evaluation records, separate from control policy.

Nested solver/fence calls are part of their enclosing evaluation, not extra
cycles. Context ownership prevents child tasks inheriting a parent's record.
"""

import asyncio
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from fractions import Fraction
from functools import wraps

from .core.telemetry import Channel, Quantity, state_flag

OPTION = "pv_diagnostic_logging"
_LOGGER = logging.getLogger(__name__)
_ACTIVE = ContextVar("pv_diagnostic_cycle", default=None)


def number(value):
    if isinstance(value, Fraction):
        try:
            return float(value)
        except OverflowError:
            return str(value)
    return value


def point(value):
    if value is None:
        return None
    return {
        "phases": value.mode.count if value.mode else 0,
        "current_a": number(value.current_a),
        "power_w": number(value.offered_power_w),
        "charging": value.charging,
        "voltages_v": list(map(number, value.phase_voltages_v)),
    }


def entity_sample(state, entity_id, now):
    """HA report age, not value-change age or invented device sample time."""
    from .pv_surplus import MAX_AGE_SECONDS

    result = {
        "entity_id": entity_id,
        "state": None,
        "value": None,
        "available": False,
        "age_s": None,
        "freshness": "missing",
    }
    if not entity_id:
        result["freshness"] = "not_configured"
    if state is None:
        return result
    timestamp = getattr(state, "last_reported", state.last_updated)
    age = (now - timestamp).total_seconds()
    result.update(
        state=state.state,
        available=state.state not in ("unknown", "unavailable"),
        age_s=round(age, 3),
        timestamp=timestamp.isoformat(),
        age_basis="last_reported"
        if hasattr(state, "last_reported")
        else "last_updated",
        unit=state.attributes.get("unit_of_measurement"),
        freshness="fresh"
        if 0 <= age <= MAX_AGE_SECONDS
        else "stale"
        if age > MAX_AGE_SECONDS
        else "future",
    )
    try:
        result["value"] = float(Fraction(state.state))
    except ValueError, TypeError, ZeroDivisionError, OverflowError:
        result["freshness"] = "non_numeric" if result["available"] else state.state
    expiry = state.attributes.get("valid_until")
    if expiry:
        try:
            result["valid_until"] = datetime.fromisoformat(expiry).isoformat()
            if datetime.fromisoformat(expiry) <= now:
                result["freshness"] = "expired"
        except ValueError, TypeError:
            result["freshness"] = "invalid_expiry"
    return result


def active(profile, target):
    record = _ACTIVE.get()
    return (
        record
        if record and record.owner == (profile, target, asyncio.current_task())
        else None
    )


class Cycle:
    def __init__(self, profile, target, trigger):
        self.owner = profile, target, asyncio.current_task()
        self.before = profile.control.confirmed_point(target)
        self.command_before = profile.control.intent(target).command_result
        self.ongoing_before = profile.pv_ongoing.get(target, False)
        self.data = {
            "station": target.station.value,
            "evse": target.evse.value,
            "connector": target.value,
            "trigger": trigger,
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.exception = None
        self.plan = None
        self.capture_inputs()

    def capture_inputs(self, selected=None):
        try:
            self._capture_inputs(selected)
        except Exception:
            self.data["diagnostic_error"] = "snapshot_failed"

    def _capture_inputs(self, selected=None):
        p, t, _ = self.owner
        now = datetime.now(UTC)
        self.data["evaluated_at"] = now.isoformat()
        for key in ("site_load_w", "surplus_w", "measured_power_w"):
            self.data.pop(key, None)
        self.data["external"] = {
            key: entity_sample(
                p.hass.states.get(p.references[key]) if p.references.get(key) else None,
                p.references.get(key),
                now,
            )
            for key in (
                "min_soc_speicher",
                "soc_speicher_aktuell",
                "leistung_pv",
                "leistung_verbraucher",
            )
        }
        if selected is not None:
            self.data["wallbox_power_sources"] = [
                entity_sample(s, s.entity_id, now) for s in selected
            ]
        runtime = p.control.runtime
        state = runtime.get(t.station)
        telemetry = {}
        for q in Quantity:
            if q == Quantity.ENERGY:
                continue
            obs = state.observation(Channel(t, q)) if state else None
            if obs is None and state and q.value.startswith("voltage_"):
                obs = state.observation(Channel(t.evse, q))
            telemetry[q.value] = (
                None
                if obs is None
                else {
                    "value": number(obs.value),
                    "scope": "connector" if obs.channel.scope == t else "evse",
                    "age_s": round((now - obs.observed_at).total_seconds(), 3),
                    "fresh": obs.observed_at <= now and obs.fresh(now),
                    "observed_at": obs.observed_at.isoformat(),
                    "valid_until": obs.valid_until.isoformat()
                    if obs.valid_until
                    else None,
                }
            )
        self.data["telemetry"] = telemetry
        session = runtime.sessions.get(t)
        charging = telemetry["charging_state"]
        connector = telemetry["connector_state"]
        self.data.update(
            profile=p.setting(t)["profile"],
            authority=runtime.authority(t.station).value,
            has_control=p.can_control(t),
            connected=bool(state and state.connected),
            enabled=runtime.enabled(t),
            available=state_flag(connector["value"], "available")
            if connector and connector["fresh"]
            else None,
            vehicle_connected=state_flag(charging["value"], "vehicle_connected")
            if charging and charging["fresh"]
            else None,
            charging=p.active(t),
            pv_continuation=p.pv_ongoing.get(t, False),
            session_active=bool(session and session.active),
            parameters=dict(p.setting(t)),
        )
        self.data["physical_phases"] = (
            [
                {
                    "phases": o.mode.count if o.mode else None,
                    "age_s": round((now - o.observed_at).total_seconds(), 3),
                    "fresh": o.fresh(now),
                    "valid_until": o.valid_until.isoformat(),
                }
                for o in state.physical_phases
                if o.scope == t
            ]
            if state
            else []
        )
        intent = p.control.intent(t)
        inputs = p.control.inputs(t)
        self.data["electrical_envelopes"] = (
            [
                {
                    "phases": e.mode.count,
                    "minimum_a": number(e.min_current_a),
                    "maximum_a": number(e.max_current_a),
                    "step_a": number(e.current_step_a),
                    "evidence": e.evidence.state.value,
                }
                for e in inputs.capabilities.envelopes
            ]
            if inputs
            else None
        )
        self.data["eligible_phases"] = (
            [m.count for m in inputs.eligible_modes] if inputs else None
        )
        self.data["physical_phase_count"] = (
            inputs.current_mode.count if inputs and inputs.current_mode else None
        )
        self.data["limits"] = {
            "current_a": {str(k): number(v) for k, v in intent.current_limits.items()},
            "phase_switch_deviation_pct": number(intent.phase_switch_deviation_pct),
            "power_ceiling_w": number(p.control.power_ceiling(t)),
            "dynamic": [
                {
                    "phases": v.mode.count,
                    "minimum_a": number(v.min_current_a),
                    "maximum_a": number(v.max_current_a),
                }
                for v in inputs.limits
            ]
            if inputs
            else None,
        }

    def finish(self):
        p, t, _ = self.owner
        intent = p.control.intent(t)
        after = p.control.confirmed_point(t)
        result = intent.command_result
        reason = self.data.get("policy_reason", p.status.get(t, "idle"))
        decision = "HOLD"
        # Only a new command outcome belongs to this cycle.
        command = result if result is not self.command_before else None
        if self.exception:
            decision, reason = (
                self.exception,
                "evaluation_interrupted"
                if self.exception == "CANCELLED"
                else "evaluation_exception",
            )
        elif not p.can_control(t):
            decision, reason = (
                "NO_AUTHORITY",
                (
                    "closed"
                    if p.closed
                    else "inactive_wallbox"
                    if not p.control.profile_permitted(t)
                    else "no_authority"
                ),
            )
        elif command and command.status.value != "applied":
            decision, reason = (
                "COMMAND_FAILED",
                command.reason.value if command.reason else command.status.value,
            )
        elif intent.status not in ("applied", "idle", "pending") and after is None:
            decision, reason = "HOLD", intent.status
        elif intent.phase_retry:
            decision, reason = "WAIT_PHASE_LOCKOUT", "phase_switch_lockout"
        elif reason == "pv_start_delay":
            decision = "START_PENDING"
        elif reason == "pv_stop_delay":
            decision = "STOP_PENDING"
        elif reason == "measurements_unavailable":
            decision = "INPUT_UNAVAILABLE"
        elif self.plan is not None and self.plan.point is None:
            decision, reason = "HOLD", self.plan.reason.value
        elif self.data["trigger"] in ("plan", "soc_event"):
            decision = "PLANNED"
        elif after is not None:
            if not after.charging:
                decision = "STOP" if self.before and self.before.charging else "OFF"
            elif not self.before or not self.before.charging:
                decision = "START"
            elif after.offered_power_w != self.before.offered_power_w:
                decision = (
                    "INCREASE"
                    if after.offered_power_w > self.before.offered_power_w
                    else "DECREASE"
                )
        elif self.plan is not None:
            decision = "PLANNED"  # Wake-up/policy evaluation; no command attempted.
        self.data.update(
            decision=decision,
            reason=reason,
            profile_status=p.status.get(t),
            control_status=intent.status,
            command_fence_reason=intent.fence_reason,
            startup_pending=t in p.pv_startups,
            policy_allows_charging=self.data.get("policy_reason")
            == "actively_charging",
            ongoing_before=self.ongoing_before,
            ongoing_after=p.pv_ongoing.get(t, False),
            applied_before=point(self.before),
            applied=point(after),
            awaiting_confirmation=t in p.control.pending_points,
            reconciliation_required=t in p.control._unconfirmed_targets,
            in_flight=point(p.control.pending_points.get(t)),
            selected=point(self.plan.point) if self.plan else None,
            commanded=point(intent.solver_result.point)
            if intent.solver_result
            else None,
            target_w=number(intent.request.target_w),
            direction=intent.request.direction.value,
            command_evaluated=command is not None,
            command_status=command.status.value if command else None,
            last_command_status=result.status.value if result else None,
            last_command_reason=result.reason.value
            if result and result.reason
            else None,
            command_reason=command.reason.value if command and command.reason else None,
            phase_lockout=bool(intent.phase_retry),
            enable_lockout="not_implemented",
            retry_remaining_s=max(0, p.pv_retry_until.get(t, 0) - p.monotonic()),
            pending_target_w=number(
                self.data.get("policy_target_w", intent.request.target_w)
            )
            if intent.status == "pending"
            or intent.phase_retry
            or p.pv_retry_until.get(t, 0) > p.monotonic()
            else None,
        )
        self.data["delays"] = {}
        for key, timers in (
            ("pv_start_delay", p.pv_start_since),
            ("pv_stop_delay", p.pv_stop_since),
        ):
            elapsed = max(0, p.monotonic() - timers[t]) if t in timers else None
            total = p.setting(t)[key]
            self.data["delays"][key] = {
                "configured_s": total,
                "elapsed_s": elapsed,
                "remaining_s": max(0, total - elapsed) if elapsed is not None else None,
            }
        _LOGGER.info(
            "PVCTRL %s",
            json.dumps(
                self.data, separators=(",", ":"), sort_keys=True, allow_nan=False
            ),
        )


@contextmanager
def cycle(profile, target, trigger):
    """Exactly one record even on early return, cancellation or failed dispatch."""
    if active(profile, target) or not profile.entry.options.get(OPTION, False):
        yield
        return
    record = None
    token = None
    try:
        record = Cycle(profile, target, trigger)
        token = _ACTIVE.set(record)
    except Exception:
        # Diagnostic collection can never suppress or change charging behavior.
        pass
    try:
        yield
    except BaseException as exc:
        if record:
            record.exception = (
                "CANCELLED"
                if isinstance(exc, asyncio.CancelledError)
                else "COMMAND_FAILED"
            )
        raise
    finally:
        try:
            if record:
                record.finish()
            else:
                _LOGGER.info(
                    "PVCTRL %s",
                    '{"decision":"DIAGNOSTIC_UNAVAILABLE","reason":"snapshot_failed"}',
                )
        except Exception:
            _LOGGER.info(
                "PVCTRL %s",
                '{"decision":"DIAGNOSTIC_UNAVAILABLE","reason":"serialization_failed"}',
            )
        finally:
            if token is not None:
                _ACTIVE.reset(token)


def diagnostic_plan(method):
    @wraps(method)
    def wrapped(self, target, *, advance=True, **kwargs):
        if not advance:
            return method(self, target, advance=False, **kwargs)
        with cycle(self, target, "plan"):
            result = method(self, target, advance=True, **kwargs)
            if record := active(self, target):
                record.plan = result[3]
                record.data.update(
                    policy_target_w=number(result[0]),
                    policy_direction=result[1].value,
                    policy_reason=result[2],
                    solver_reason=result[3].reason.value if result[3] else None,
                    solver_min_w=number(result[3].min_power_w) if result[3] else None,
                    solver_max_w=number(result[3].max_power_w) if result[3] else None,
                )
            return result

    return wrapped


def diagnostic_permission(method):
    @wraps(method)
    async def wrapped(self, target, enabled):
        if self.setting(target)["profile"] != "PV_SURPLUS":
            return await method(self, target, enabled)
        with cycle(self, target, "permission"):
            return await method(self, target, enabled)

    return wrapped
