"""Minimal observed transaction lifecycle; no policy or economic accounting."""

import logging
import math
import os
import stat
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path
from uuid import uuid4

from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.storage import Store
from homeassistant.util.json import json_loads

_LOGGER = logging.getLogger(__name__)
_MISSING = object()

BOUNDARY_AGE = timedelta(seconds=15)

KEYS = (
    "id",
    "started",
    "ended",
    "duration",
    "power",
    "energy",
    "start_meter",
    "end_meter",
    "active",
)


def view(state, now):
    session = state.session
    if session is None:
        return dict.fromkeys(KEYS)
    current = state.connected and session.get("generation") == list(state.generation)
    active = session["ended_at"] is None
    started = (
        datetime.fromisoformat(session["started_at"])
        if session.get("started_at")
        else None
    )
    ended = (
        datetime.fromisoformat(session["ended_at"]) if session.get("ended_at") else None
    )
    channel = session.get("power_channel")
    power = (
        state.value((channel[0], tuple(channel[1]), channel[2])) if channel else None
    )
    return {
        "id": session["session_id"],
        "started": started,
        "ended": ended,
        "duration": max(0, ((ended or now) - started).total_seconds())
        if started and (current or not active)
        else None,
        "power": power if active and current else None if active else 0,
        "energy": session.get("energy"),
        "start_meter": session.get("start_meter"),
        "end_meter": session.get("end_meter"),
        "active": active if current or not active else None,
    }


def publish(state, before, now, *, force=False, save=True):
    after = view(state, now)
    scope = tuple(state.session["scope"])
    state.publish(
        *[("session", scope, key) for key in KEYS if force or before[key] != after[key]]
    )
    if save:
        state.save_session()


def event(state, generation, scope, external_id, kind, at, sequence):
    if not state.current(generation) or not state.accepts(scope) or at < state.epoch:
        return False
    if external_id in state.closed_transactions:
        return False
    old = state.session
    if old and old["external_transaction_id"] == external_id:
        if (
            tuple(old["scope"]) != tuple(scope)
            or old["ended_at"] is not None
            or sequence <= old["sequence"]
            or at < datetime.fromisoformat(old["updated_at"])
        ):
            return False
    elif kind == "Ended" or (old and at <= datetime.fromisoformat(old["updated_at"])):
        return False
    before = view(state, at)
    charging_before = state.charging_value(state.binding)
    if old is None or old["external_transaction_id"] != external_id:
        state.invalidate_idle(at)
        if old:
            state.closed_transactions.add(old["external_transaction_id"])
        # Updated without Started must not invent the start timestamp or meter.
        state.session = {
            "session_id": uuid4().hex,
            "external_transaction_id": external_id,
            "scope": list(scope),
            "started_at": at.isoformat() if kind == "Started" else None,
            "ended_at": None,
            "start_meter": None,
            "end_meter": None,
            "current_meter": None,
            "energy": None,
            "energy_invalid": False,
        }
    confirmed = old is not None and old.get("generation") == list(generation)
    session = state.session
    if session.get("generation") != list(generation):
        session.pop("energy_link_generation", None)
        session.pop("energy_generation", None)
    session.update(
        updated_at=at.isoformat(), sequence=sequence, generation=list(generation)
    )
    if kind == "Ended":
        session.update(ended_at=at.isoformat())
        session.update(end_meter=None, energy=None)
        if (
            session.get("energy_at") == at.isoformat()
            and session.get("energy_generation") == list(generation)
            and not session["energy_invalid"]
        ):
            session["end_meter"] = session.get("current_meter")
            session["end_meter_at"] = session.get("energy_at")
            calculate_energy(session, final=True)
        state.closed_transactions.add(external_id)
    if old and tuple(old["scope"]) != tuple(scope):
        state.publish(*[("session", tuple(old["scope"]), key) for key in KEYS])
    state.add_session(scope)
    state.add_active(scope)
    if charging_before != state.charging_value(state.binding):
        state.publish(("observation", state.binding, "charging_state"))
    publish(
        state,
        before,
        at,
        force=old is not state.session or not confirmed or kind == "Ended",
    )
    return True


def meter(state, quantity, value, at, *, boundary=False, channel=None, before=None):
    session = state.session
    if (
        not session
        or not state.connected
        or session.get("generation") != list(state.generation)
        or at < state.epoch
    ):
        return
    event_at = datetime.fromisoformat(session["updated_at"])
    ended = session["ended_at"] is not None
    start = session.get("started_at")
    start_boundary = boundary and start == session["updated_at"] and not ended
    if boundary:
        if not timedelta(0) <= event_at - at <= BOUNDARY_AGE:
            return
    elif ended or (start and at < datetime.fromisoformat(start)):
        return
    if ended and not boundary:
        return
    before = view(state, at) if before is None else before
    if quantity == "power":
        previous = session.get("power_channel")
        observation = (
            state.observations.get((previous[0], tuple(previous[1]), previous[2]))
            if previous
            else None
        )
        if observation and observation.observed_at > at:
            return
        if channel is None:
            channel = ("transaction", tuple(session["scope"]), "power")
            state.observe(
                state.generation,
                channel,
                value,
                "ocpp2.1:TransactionEvent",
                at,
                unit="W",
            )
        session["power_channel"] = list(channel)
    elif quantity == "energy":
        stream = (
            [channel[0], list(channel[1]), channel[2]]
            if channel
            else ["transaction", session["scope"], "energy"]
        )
        transaction_stream = ["transaction", session["scope"], "energy"]
        if stream != session.get("energy_channel", stream):
            if not linked_energy(state, stream, value, at):
                return
            stream = transaction_stream
        if session.get("energy_channel", stream) != stream:
            return
        if "energy_channel" not in session and session.get("start_meter") is not None:
            session["energy_invalid"] = True  # Legacy baseline has no stream proof.
        session["energy_channel"] = stream
        previous_at = session.get("energy_reported_at", session.get("energy_at"))
        if previous_at and at < datetime.fromisoformat(previous_at):
            return
        previous = session.get("current_meter")
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        if not valid or (
            previous is not None
            and (
                value < previous
                or (previous_at == at.isoformat() and value != previous)
            )
        ):
            session["energy_invalid"] = True
            if ended:
                session.update(end_meter=None, energy=None)
        else:
            if start_boundary and previous_at is None:
                session["start_meter"] = value
                session["start_meter_at"] = at.isoformat()
            session["current_meter"] = value
            session["energy_at"] = at.isoformat()
            session["energy_generation"] = list(state.generation)
            if ended and not session["energy_invalid"]:
                session["end_meter"] = value
                session["end_meter_at"] = at.isoformat()
        session["energy_reported_at"] = at.isoformat()
        session["energy_received_at"] = datetime.now(UTC).isoformat()
        session["energy_source"] = "ocpp2.1:session"
        calculate_energy(session, final=ended)
        if channel is None:
            linked_energy(state, None, value, at)
    else:
        return
    session["observed_at"] = at.isoformat()
    publish(state, before, at, save=quantity == "energy")


def calculate_energy(session, *, final):
    start = session.get("start_meter")
    value = session.get("end_meter" if final else "current_meter")
    session["energy"] = (
        value - start
        if start is not None and value is not None and not session["energy_invalid"]
        else None
    )


def linked_energy(state, stream, value, at):
    """Narrow supported-device mapping, proved by a shared source sample per boot."""
    session = state.session
    parent = (state.binding[0], "1", None)
    expected = ["observation", list(parent), "energy"]
    if (
        not state.current(state.registered_generation)
        or state.binding[1:] != ("1", "1")
        or tuple(session["scope"]) != state.binding
        or state.identity.get("manufacturer") != "wallbox-stationary"
        or state.identity.get("model") != "wallbox-stationary"
        or session.get("energy_channel") != ["transaction", session["scope"], "energy"]
        or (stream is not None and stream != expected)
    ):
        return False
    if stream is not None and session.get("energy_link_generation") == list(
        state.generation
    ):
        return True
    if value is None or session.get("energy_generation") != list(state.generation):
        return False
    if stream is None:
        observation = state.observations.get(("observation", parent, "energy"))
        match = (
            observation
            and state.current(observation.generation)
            and observation.observed_at == at
            and observation.value == value
        )
    else:
        match = (
            session.get("energy_at") == at.isoformat()
            and session.get("current_meter") == value
        )
    if match and not session["energy_invalid"]:
        session["energy_link_generation"] = list(state.generation)
    return session.get("energy_link_generation") == list(state.generation)


class DamagedJournal(ValueError):
    """A readable file that cannot be used as a current Store document."""


def read_snapshot(store):
    # Store.async_load can conflate missing and invalid data, quarantine bad
    # JSON, or rewrite a mismatched minor version before payload validation.
    path = Path(store.path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _MISSING
    if not stat.S_ISREG(info.st_mode):
        raise OSError("Session journal is not a regular file")
    try:
        wrapper = json_loads(path.read_bytes())
    except JSONDecodeError:
        raise DamagedJournal("invalid JSON") from None
    if (
        not isinstance(wrapper, dict)
        or type(wrapper.get("version")) is not int
        or wrapper["version"] != store.version
        or type(wrapper.get("minor_version", 1)) is not int
        or wrapper.get("key") != store.key
        or not isinstance(wrapper.get("data"), dict)
    ):
        raise DamagedJournal("invalid Home Assistant Store document")
    return wrapper["data"]


def archive_snapshot(path, missing):
    try:
        archives = sorted(
            (
                file
                for file in path.parent.iterdir()
                if file.name.startswith(f"{path.name}.corrupt.")
                and file.is_file(follow_symlinks=False)
            ),
            reverse=True,
        )
    except FileNotFoundError:
        if missing:
            return None
        raise
    if missing:
        return next(iter(archives), None)

    # Keep the live name until Store atomically replaces it. Reuse the archive
    # of the same inode after a failed or interrupted normalization.
    for archive in archives:
        if path.samefile(archive):
            return archive
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = path.with_name(f"{path.name}.corrupt.{stamp}.{uuid4().hex}")
    os.link(path, archive, follow_symlinks=False)
    return archive


def transaction_id(value):
    return isinstance(value, str) and 0 < len(value) <= 36 and bool(value.strip())


def journal_time(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.utcoffset() is not None:
            return parsed
    return None


def recover_session(record, state):
    """Return a usable historical record and independently confirmed closure."""
    if not isinstance(record, dict):
        return None, None
    scope = record.get("scope")
    external_id = record.get("external_transaction_id")
    updated = journal_time(record.get("updated_at"))
    started = journal_time(record.get("started_at"))
    ended = journal_time(record.get("ended_at"))
    if (
        not isinstance(scope, list)
        or len(scope) != 3
        or tuple(scope[:2]) != state.binding[:2]
        or not state.accepts(scope)
        or not transaction_id(external_id)
        or updated is None
        or (
            record.get("started_at") is not None
            and (started is None or started > updated)
        )
        or "ended_at" not in record
        or (record["ended_at"] is not None and ended != updated)
    ):
        return None, None

    closed_id = external_id if ended is not None else None
    identity, sequence = record.get("session_id"), record.get("sequence")
    if (
        not isinstance(identity, str)
        or not identity.strip()
        or type(sequence) is not int
        or sequence < 0
        or (ended is None and external_id in state.closed_transactions)
    ):
        return None, closed_id

    restored = record.copy()
    restored.setdefault("started_at", None)
    for key in ("start_meter", "current_meter", "end_meter", "energy"):
        value = record.get(key)
        restored[key] = (
            value
            if type(value) in (int, float) and math.isfinite(value) and value >= 0
            else None
        )
    if ended is None and restored["end_meter"] is not None:
        return None, None
    if type(restored.get("energy_invalid")) is not bool:
        restored["energy_invalid"] = restored["start_meter"] is not None

    for key in (
        "start_meter_at",
        "end_meter_at",
        "observed_at",
        "energy_at",
        "energy_reported_at",
        "energy_received_at",
    ):
        if key in restored and journal_time(restored[key]) is None:
            restored.pop(key)
    for role in ("power", "energy"):
        key = f"{role}_channel"
        if key in restored and restored[key] not in (
            ["transaction", scope, role],
            ["observation", [*scope[:2], None], role],
        ):
            restored.pop(key)
    for key in ("energy_generation", "energy_link_generation"):
        if key in restored:
            value = restored[key]
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(part) is not int or part < 0 for part in value)
            ):
                restored.pop(key)
    if "energy_source" in restored and not isinstance(restored["energy_source"], str):
        restored.pop("energy_source")
    return restored, closed_id


async def load(hass, entry_id, state):
    store = Store(hass, 1, f"wallbox_manager.{entry_id}.passive_session")
    path = Path(store.path)
    reasons = []
    archived = None
    action = "read"
    try:
        try:
            saved = await hass.async_add_executor_job(read_snapshot, store)
        except DamagedJournal as error:
            reasons.append(str(error))
            saved = None
        if saved is _MISSING:
            archived = await hass.async_add_executor_job(archive_snapshot, path, True)
            if archived is not None:
                reasons.append("current journal missing after quarantine")
        elif saved is not None:
            if type(saved.get("schema")) is not int or saved["schema"] != 2:
                raise ConfigEntryError(
                    "Unsupported passive session journal schema (expected 2)"
                )
            raw_ids = saved.get("closed_transactions")
            if isinstance(raw_ids, list):
                state.closed_transactions = {
                    item for item in raw_ids if transaction_id(item)
                }
            if not isinstance(raw_ids, list) or any(
                not transaction_id(item) for item in raw_ids
            ):
                reasons.append("missing or invalid closed transaction history")
            record = saved.get("session")
            state.session, closed_id = recover_session(record, state)
            if closed_id is not None and closed_id not in state.closed_transactions:
                state.closed_transactions.add(closed_id)
                reasons.append("completed transaction missing from closed history")
            if "session" not in saved or state.session != record:
                reasons.append("incomplete or inconsistent session record")
            if state.session is not None:
                state.session["generation"] = None

        if reasons:
            if archived is None:
                action = "archive"
                archived = await hass.async_add_executor_job(
                    archive_snapshot, path, False
                )
            action = "write and verify"
            normalized = document(state)
            await store.async_save(normalized)
            # HA Store can log WriteError and return normally.
            if await hass.async_add_executor_job(read_snapshot, store) != normalized:
                raise OSError("Normalized journal could not be verified")
            _LOGGER.warning(
                "Recovered passive session journal for entry %s (%s). Original: %s. "
                "Historical session statistics and closed-transaction replay "
                "protection may be incomplete.",
                entry_id,
                ", ".join(reasons),
                archived,
            )
    except ConfigEntryError:
        raise
    except Exception as error:
        _LOGGER.error(
            "Cannot %s passive session journal for entry %s at %s (%s). "
            "Recovery did not complete.",
            action,
            entry_id,
            path,
            type(error).__name__,
        )
        raise ConfigEntryError(f"Cannot {action} passive session journal") from None

    state.save_session = lambda: store.async_delay_save(lambda: document(state), 1)
    return store


def document(state):
    snapshot = state.session.copy() if state.session is not None else None
    if snapshot is not None:
        snapshot.pop("generation", None)
    return {
        "schema": 2,
        "session": snapshot,
        "closed_transactions": sorted(state.closed_transactions),
    }
