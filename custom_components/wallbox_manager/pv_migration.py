"""Read-only beta.18 journal import; no old response model or timers are restored."""

import json
from fractions import Fraction


def legacy_record(economy, days, entry_id, binding):
    record = {}
    if isinstance(days, dict) and days.get("version") == 2:
        day = days.get("days", {}).get(json.dumps(list(binding)), {})
        if day.get("state") in ("BEFORE_SURPLUS", "DYNAMIC", "FINISHED"):
            record["day"] = dict(version=1, date=day.get("date"), phase=day["state"])
    if economy is None:
        return record
    try:
        if not isinstance(economy, dict) or economy.get("version") != 1:
            raise ValueError("legacy journal version")
        records = economy["records"]
        if not isinstance(records, list):
            raise ValueError("legacy records")
        matches = []
        for item in records:
            identity = item["identity"]
            if identity["entry_id"] == entry_id and tuple(
                identity[k] for k in ("station", "evse", "connector")
            ) == tuple(binding):
                matches.append(item)
        if not records:
            return record
        if len(matches) != 1:
            raise ValueError("legacy identity mismatch")
        item = matches[0]
        identity, policy, state = item["identity"], item["policy"], item["state"]
        if (
            policy.get("version") != 2
            or state.get("version") != 2
            or policy.get("scope") != "pv_charging"
            or policy.get("reset_seconds") != 15
            or policy.get("reset_strict") is not True
        ):
            raise ValueError("legacy policy mismatch")
        energy = state["energy_ws"]
        if (
            type(energy["numerator"]) is not int
            or type(energy["denominator"]) is not int
            or energy["numerator"] < 0
            or energy["denominator"] <= 0
        ):
            raise ValueError("legacy energy")
        wh = float(Fraction(energy["numerator"], energy["denominator"]) / 3600)
        flags = ("paused", "restart_required", "off_pending", "off_in_flight")
        if any(type(state[k]) is not bool for k in flags):
            raise ValueError("legacy STOP provenance")
        stopped = any(state[k] for k in flags) or wh >= 50
        if not isinstance(identity["transaction"], str) or not identity["transaction"]:
            raise ValueError("legacy transaction")
        record["economics"] = dict(
            version=1,
            scope=[*binding, "remote", identity["transaction"]],
            wh=wh,
            stopped=stopped,
            reset=False,
            cause="import_budget" if stopped else None,
        )
        hardware = identity["station_identity"]
        record["station_identity"] = {
            key: hardware[old]
            for key, old in (
                ("manufacturer", "vendor"),
                ("model", "model"),
                ("serial_number", "serial"),
                ("sw_version", "firmware"),
            )
            if hardware.get(old) is not None
        }
        if not record["station_identity"] or any(
            not isinstance(v, str) for v in record["station_identity"].values()
        ):
            raise ValueError("legacy hardware identity")
    except ValueError, KeyError, TypeError, OverflowError:
        record["economics"] = dict(
            version=1,
            scope=["unreconciled"],
            wh=0,
            stopped=True,
            reset=False,
            cause="legacy_recovery_unknown",
        )
    return record
