"""Minimal observed transaction lifecycle; no policy or economic accounting."""

from datetime import UTC, datetime
from uuid import uuid4

from homeassistant.helpers.storage import Store

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
    if old is None or old["external_transaction_id"] != external_id:
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
            "energy": None,
            "energy_invalid": False,
        }
    confirmed = old is not None and old.get("generation") == list(generation)
    session = state.session
    session.update(
        updated_at=at.isoformat(), sequence=sequence, generation=list(generation)
    )
    if kind == "Ended":
        session.update(ended_at=at.isoformat())
        if session.get("energy_at") != at.isoformat():
            session["end_meter_before_end"] = session.get("end_meter")
            session.update(end_meter=None, energy=None)
        state.closed_transactions.add(external_id)
    if old and tuple(old["scope"]) != tuple(scope):
        state.publish(*[("session", tuple(old["scope"]), key) for key in KEYS])
    state.add_session(scope)
    state.add_active(scope)
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
        or session.get("generation") != list(state.generation)
        or (
            session.get("started_at") is not None
            and at < datetime.fromisoformat(session["started_at"])
        )
    ):
        return
    if session["ended_at"] and at != datetime.fromisoformat(session["ended_at"]):
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
        # Transaction registers may be session-relative; never subtract a
        # lifetime/parent register from a transaction-scoped baseline.
        stream = (
            [channel[0], list(channel[1]), channel[2]]
            if channel
            else ["transaction", session["scope"], "energy"]
        )
        if session.get("energy_channel", stream) != stream:
            return
        if "energy_channel" not in session and session.get("start_meter") is not None:
            session["energy_invalid"] = True  # Legacy baseline has no stream proof.
        session["energy_channel"] = stream
        previous_at = session.get("energy_at")
        if previous_at and at < datetime.fromisoformat(previous_at):
            return
        previous = session.get("end_meter")
        if previous is None:
            previous = session.pop("end_meter_before_end", None)
        if value is None or (
            previous is not None
            and (
                value < previous
                or (previous_at == at.isoformat() and value != previous)
            )
        ):
            session["energy_invalid"] = True
        if boundary and session["started_at"] == at.isoformat() and previous_at is None:
            session["start_meter"] = value
        session["end_meter"] = value
        start = session.get("start_meter")
        session["energy"] = (
            value - start
            if start is not None and value is not None and not session["energy_invalid"]
            else None
        )
        session["energy_at"] = at.isoformat()
        session["energy_received_at"] = datetime.now(UTC).isoformat()
        session["energy_source"] = "ocpp2.1:session"
    else:
        return
    session["observed_at"] = at.isoformat()
    after = view(state, at)
    publish(
        state,
        before,
        at,
        save=quantity == "energy" and before["end_meter"] != after["end_meter"],
    )


async def load(hass, entry_id, state):
    store = Store(hass, 1, f"wallbox_manager.{entry_id}.passive_session")
    saved = await store.async_load()
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
                "end_meter": energy("energy_end_wh"),
                "energy": energy("energy_charged_wh"),
                "sequence": old.get("sequence") or -1,
                "generation": None,
                "energy_invalid": old.get("energy_invalid", False),
            }
    state.save_session = lambda: store.async_delay_save(lambda: document(state), 1)
    return store


def document(state):
    return {
        "session": state.session,
        "closed_transactions": sorted(state.closed_transactions),
    }
