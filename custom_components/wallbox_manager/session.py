"""Minimal observed transaction lifecycle; no policy or economic accounting."""

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from homeassistant.helpers.storage import Store

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


async def load(hass, entry_id, state):
    store = Store(hass, 1, f"wallbox_manager.{entry_id}.passive_session")
    saved = await store.async_load()
    if saved and saved.get("schema", 1) < 2:
        old = saved.get("session")
        if old:
            old["current_meter"] = old.get("end_meter")
            if old["ended_at"] is None:
                old["end_meter"] = None
    if saved:
        state.session = saved["session"]
        if state.session:
            state.session["generation"] = None
        state.closed_transactions = set(saved.get("closed_transactions", []))
    else:
        # Read-only projection of the last legacy session; leave its full ledger intact.
        legacy = (
            await Store(hass, 1, f"wallbox_manager.{entry_id}.sessions").async_load()
            or {}
        )
        rows = {r["session_id"]: r for r in legacy.get("records", [])}
        state.closed_transactions = {
            r["external_transaction_id"]
            for r in rows.values()
            if r["ended_at"] is not None
        }
        matches = [
            rows[k]
            for k in legacy.get("latest", [])
            if k in rows and state.accepts(rows[k]["scope"])
        ]
        if len(matches) > 1:
            # Prefer the bound connector to a parent session, without altering history.
            matches = [r for r in matches if tuple(r["scope"]) == state.binding]
        if len(matches) == 1:
            old = matches[0]

            def energy(key):
                value = old.get(key)
                if value is None:
                    return None
                from fractions import Fraction

                return float(Fraction(value) / 1000)

            state.session = {
                "session_id": old["session_id"],
                "external_transaction_id": old["external_transaction_id"],
                "scope": old["scope"],
                "started_at": old["started_at"]
                if old.get("start_known", True)
                else None,
                "updated_at": old["updated_at"],
                "ended_at": old["ended_at"],
                "start_meter": energy("energy_start_wh"),
                "end_meter": energy("energy_end_wh") if old["ended_at"] else None,
                "current_meter": energy("energy_end_wh"),
                "energy": energy("energy_charged_wh"),
                "sequence": old.get("sequence") or -1,
                "generation": None,
                "energy_invalid": old.get("energy_invalid", False),
            }
    state.save_session = lambda: store.async_delay_save(lambda: document(state), 1)
    return store


def document(state):
    return {
        "schema": 2,
        "session": state.session,
        "closed_transactions": sorted(state.closed_transactions),
    }
