"""Level-3 taps on consumed inputs; never poll sensors or run a regulator twice."""

import asyncio
from datetime import datetime, timedelta
from functools import wraps

from .diagnostics import diagnostic_level
from .freshness import LIVE_FRESHNESS
from .pv_diagnostics import _ACTIVE, entity_sample, number

REFERENCES = (
    "min_soc_speicher",
    "soc_speicher_aktuell",
    "leistung_pv",
    "leistung_verbraucher",
    "storage_discharge_power",
    "storage_capacity",
    "remaining_pv_energy",
    "grid_import_power",
    "grid_export_power",
)
CALCULATIONS = (
    "raw_pv_power_w",
    "smoothed_pv_power_w",
    "raw_consumption_power_w",
    "smoothed_consumption_power_w",
    "measured_power_w",
    "site_load_w",
    "surplus_w",
    "raw_regulator_target_w",
    "observed_net_grid_import_w",
)


def passive_settings(profile, target):
    """Do not call setting(): it reads reserve and may clamp persistent intent."""
    from .profiles import target_key
    from .regulation import DEFAULTS

    settings = dict(profile.settings.get(target_key(target), {}))
    settings.update(
        {k: profile.entry.options[k] for k in DEFAULTS if k in profile.entry.options}
    )
    return settings


def detailed_record():
    try:
        record = _ACTIVE.get()
        if (
            record is not None
            and record.owner[2] is asyncio.current_task()
            and diagnostic_level(record.owner[0].entry) == 3
        ):
            return record
    except Exception:
        pass  # Diagnostics must not change control-path results or exceptions.
    return None


def timestamp(value):
    """Only preserve explicitly supplied, timezone-aware measurement times."""
    if value is None:
        return None
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.isoformat() if value.tzinfo is not None else None


def input_sample(state, entity_id, now, max_age, value, accepted):
    sample = entity_sample(state, entity_id, now, max_age=max_age)
    sample.update(
        evaluated_at=now.isoformat(),
        normalized_value=number(value) if accepted else None,
        reading_accepted=accepted,
        last_updated=None,
        last_reported=None,
        source_observed_at=None,
        source_received_at=None,
        source_timestamp_status="unavailable",
        effective_valid_until=None,
    )
    if state is not None:
        updated = state.last_updated
        reported = getattr(state, "last_reported", None)
        deadline = (reported or updated) + timedelta(seconds=max_age)
        if expiry := state.attributes.get("valid_until"):
            try:
                deadline = min(deadline, datetime.fromisoformat(expiry))
            except ValueError, TypeError:
                deadline = None
        sample.update(
            last_updated=updated.isoformat(),
            last_reported=reported.isoformat() if reported else None,
            effective_valid_until=deadline.isoformat() if deadline else None,
            power_availability_reason=state.attributes.get("power_availability_reason"),
        )
        for attr, field in (
            ("observed_at", "source_observed_at"),
            ("received_at", "source_received_at"),
        ):
            try:
                sample[field] = timestamp(state.attributes.get(attr))
            except ValueError, TypeError, AttributeError:
                sample[field] = None
        sample["source_timestamp_status"] = (
            "provided"
            if sample["source_observed_at"]
            else "invalid"
            if state.attributes.get("observed_at") is not None
            else "unavailable"
        )
        if sample["source_observed_at"] is not None and deadline is not None:
            acquired = datetime.fromisoformat(sample["source_observed_at"])
            sample["source_age_s"] = (now - acquired).total_seconds()
            sample["effective_valid_until"] = min(
                deadline, acquired + timedelta(seconds=max_age)
            ).isoformat()
    return sample


def diagnostic_reading(method):
    @wraps(method)
    def wrapped(state, now, **kwargs):
        key = kwargs.pop("diagnostic_key", None)
        record = detailed_record()
        if record is None:
            return method(state, now, **kwargs)
        value, accepted = None, False
        try:
            value = method(state, now, **kwargs)
            accepted = True
            return value
        finally:
            try:
                p = record.owner[0]
                entity_id = getattr(state, "entity_id", None)
                if key is None:
                    key = (
                        next(
                            (k for k in REFERENCES if p.references.get(k) == entity_id),
                            None,
                        )
                        if entity_id
                        else None
                    )
                sample = input_sample(
                    state,
                    p.references.get(key) if key in REFERENCES else entity_id,
                    now,
                    kwargs.get("max_age", LIVE_FRESHNESS),
                    value,
                    accepted,
                )
                sample["reference"] = key
                sample["normalized_unit"] = (
                    "%" if kwargs.get("soc") else "Wh" if kwargs.get("energy") else "W"
                )
                record.data.setdefault("input_reads", []).append(sample)
                if key in REFERENCES:
                    record.data["external"][key] = sample
                if key == "selected_ev_power":
                    record.data["wallbox_power_sources"] = [sample]
            except Exception:
                record.data["diagnostic_error"] = "input_snapshot_failed"

    return wrapped


def empty_inputs(profile):
    return {
        key: {
            "entity_id": profile.references.get(key),
            "value": None,
            "normalized_value": None,
            "freshness": "not_read"
            if profile.references.get(key)
            else "not_configured",
        }
        for key in REFERENCES
    }


def diagnostic_request(method):
    @wraps(method)
    def wrapped(profile, target, *args, **kwargs):
        record = detailed_record()
        if record is None:
            return method(profile, target, *args, **kwargs)
        try:
            evaluations = record.data.setdefault("regulator_evaluations", [])
            evaluation = {
                "evaluation_id": f"{record.data['started_at']}/{len(evaluations) + 1}",
                "profile": passive_settings(profile, target).get("profile"),
            }
            record.data["external"] = empty_inputs(profile)
            record.data["input_reads"] = []
            record.data.pop("regulator", None)
            record.data.pop("wallbox_power_sources", None)
            for key in (
                *CALCULATIONS,
                "mode",
                "optimum_mode",
                "optimum_target_soc",
                "pv_day_state",
                "target_soc",
                "soc",
                "lower_stop_threshold",
                "pv_start_threshold",
                "fast_start_threshold",
            ):
                record.data.pop(key, None)
        except Exception:
            return method(profile, target, *args, **kwargs)
        result = method(profile, target, *args, **kwargs)
        try:
            evaluation.update(
                mode=record.data.get("mode"),
                target_soc=record.data.get("target_soc"),
                input_reads=record.data.pop("input_reads"),
                calculations={k: record.data.get(k) for k in CALCULATIONS},
                regulator=record.data.get("regulator"),
                request_w=number(result[0]),
                direction=result[1].value,
                reason=result[2],
                battery_max_discharge_power_w=passive_settings(profile, target).get(
                    "optimum_max_discharge_w"
                ),
                battery_charge_power_w=None,
                battery_power_signed_w=None,
                battery_dynamic_discharge_limit_w=None,
                battery_unavailable_reason="no_existing_mapping_or_limit_provider",
                lower_stop_threshold=record.data.get("lower_stop_threshold"),
                pv_start_threshold=record.data.get("pv_start_threshold"),
                fast_start_threshold=record.data.get("fast_start_threshold"),
            )
            values = {
                k: v.get("normalized_value") for k, v in record.data["external"].items()
            }
            if evaluation["regulator"] is None and "surplus_w" in record.data:
                evaluation["regulator"] = {
                    "algorithm": "PV_BALANCE"
                    if evaluation["mode"] == "PV_BALANCE"
                    else "PV_SURPLUS",
                    "previous_request_w": None,
                    "request_w": number(result[0]),
                }
            imported, exported = (
                values.get("grid_import_power"),
                values.get("grid_export_power"),
            )
            evaluation.update(
                battery_soc_pct=values.get("soc_speicher_aktuell"),
                battery_discharge_power_w=values.get("storage_discharge_power"),
                grid_net_power_w=imported - exported
                if imported is not None and exported is not None
                else None,
                grid_net_basis="import_minus_export_same_evaluation"
                if imported is not None and exported is not None
                else "incomplete_or_unused_inputs",
            )
            evaluations.append(evaluation)
            record.data["evaluation_id"] = evaluation["evaluation_id"]
        except Exception:
            record.data["diagnostic_error"] = "regulator_snapshot_failed"
        return result

    return wrapped


def diagnostic_fast_request(method):
    @wraps(method)
    def wrapped(
        regulator,
        wallbox,
        discharge,
        limit,
        imported,
        exported,
        *,
        now,
        interval,
        **kwargs,
    ):
        record = detailed_record()
        if record is None:
            return method(
                regulator,
                wallbox,
                discharge,
                limit,
                imported,
                exported,
                now=now,
                interval=interval,
                **kwargs,
            )
        snapshot = None
        try:
            from .pv_budget import IMPORT_CONFIRM_SECONDS, SETTLING_SECONDS
            from .pv_regulators import GRID_DEADBAND_W, IMPORT_GRACE_SECONDS

            snapshot = {
                "algorithm": "FAST_DISCHARGE",
                "wallbox_power_w": number(wallbox),
                "battery_discharge_power_w": number(discharge),
                "battery_max_discharge_power_w": number(limit),
                "grid_import_power_w": number(imported),
                "grid_export_power_w": number(exported),
                "previous_request_w": number(regulator.requested),
                "previous_updated_at_monotonic": regulator.updated_at,
                "previous_import_since_monotonic": regulator.import_since,
                "now_monotonic": now,
                "interval_s": interval,
                "grid_deadband_w": GRID_DEADBAND_W,
                "import_grace_s": IMPORT_CONFIRM_SECONDS
                if kwargs.get("evidence") is not None
                else IMPORT_GRACE_SECONDS,
                "response_settling_s": SETTLING_SECONDS,
            }
        except Exception:
            record.data["diagnostic_error"] = "regulator_snapshot_failed"
        result = method(
            regulator,
            wallbox,
            discharge,
            limit,
            imported,
            exported,
            now=now,
            interval=interval,
            **kwargs,
        )
        try:
            if snapshot is not None:
                snapshot.update(
                    request_w=number(result),
                    hard_max_w=number(regulator.hard_max),
                    measurement_generation=(
                        regulator.evidence.generation if regulator.evidence else None
                    ),
                    state_revision=regulator.revision,
                    evidence_decision=regulator.reason,
                    updated_at_monotonic=regulator.updated_at,
                    import_since_monotonic=regulator.import_since,
                )
                record.data["regulator"] = snapshot
        except Exception:
            record.data["diagnostic_error"] = "regulator_snapshot_failed"
        return result

    return wrapped
