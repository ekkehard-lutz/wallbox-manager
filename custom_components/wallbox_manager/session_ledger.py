"""Entry-owned session ledger, independent of protocol and HA storage types."""

import logging
from dataclasses import asdict, fields, replace
from datetime import datetime
from fractions import Fraction
from uuid import uuid4

from .core.models import ConnectorId, EvseId, StationId
from .core.sessions import ChargingSession, SessionEvent, SessionEventKind
from .core.telemetry import State

_LOGGER = logging.getLogger(__name__)


def scope_parts(scope):
    evse = scope.evse if isinstance(scope, ConnectorId) else scope
    return [
        evse.station.value,
        evse.value,
        scope.value if isinstance(scope, ConnectorId) else None,
    ]


def scope_from_parts(parts):
    station, evse, connector = parts
    scope = EvseId(StationId(station), evse)
    return ConnectorId(scope, connector) if connector is not None else scope


class SessionLedger:
    """All records are retained; latest is a separate per-scope display index.

    Lifecycle ordering is timestamp first, then sequence for equal timestamps.
    Completed IDs are tombstones: replay can never reopen or duplicate them.
    """

    def __init__(self):
        self._records = {}
        self._latest = {}
        self._listeners = set()
        self.next_transaction_id = 1

    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _publish(self):
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Session subscriber failed")

    @property
    def latest(self):
        return tuple(self._records[key] for key in self._latest.values())

    def get(self, scope):
        key = self._latest.get(scope)
        return self._records.get(key)

    def history(self, scope=None):
        """Immutable completed records, optionally filtered to an exact scope."""
        return tuple(
            sorted(
                (
                    s
                    for s in self._records.values()
                    if not s.active and (scope is None or s.scope == scope)
                ),
                key=lambda s: (s.ended_at, s.session_id),
            )
        )

    def find(self, station, external_id):
        matches = [
            s
            for s in self._records.values()
            if s.station_id == station and s.external_transaction_id == external_id
        ]
        return matches[0] if len(matches) == 1 else None

    def start_legacy(self, scope, at, meter):
        # 1.6 has no station-issued ID on StartTransaction. Exact replay reuses
        # our durable ID; distinct starts allocate an entry-wide positive integer.
        for record in self._records.values():
            if record.scope == scope and record.started_at == at:
                return record.external_transaction_id
        previous = self.get(scope)
        if previous is not None and at <= previous.updated_at:
            return None
        external = str(self.next_transaction_id)
        self.next_transaction_id += 1
        self.apply(
            SessionEvent(scope, external, SessionEventKind.STARTED, at, meter_wh=meter)
        )
        return external

    def apply(self, event: SessionEvent, observations=()):
        matches = [
            s
            for s in self._records.values()
            if s.scope == event.scope
            and s.external_transaction_id == event.external_transaction_id
        ]
        old = matches[0] if matches else None
        if old is not None:
            if not old.active or event.at < old.updated_at:
                return False
            if event.kind == SessionEventKind.STARTED:
                return False
            if event.at == old.updated_at:
                if event.sequence is None:
                    if event.kind != SessionEventKind.ENDED:
                        return False
                elif old.sequence is not None and event.sequence <= old.sequence:
                    return False
        else:
            previous = self.get(event.scope)
            if previous is not None and event.at <= previous.updated_at:
                return False
            if previous is not None and previous.active:
                self._records[previous.session_id] = replace(
                    previous,
                    updated_at=event.at,
                    ended_at=event.at,
                    current_power_w=Fraction(0),
                    end_reason="superseded",
                    energy_end_wh=previous.energy_end_wh
                    if previous.energy_at == event.at
                    else None,
                    energy_charged_wh=previous.energy_charged_wh
                    if previous.energy_at == event.at
                    else None,
                )
            old = ChargingSession(
                str(uuid4()),
                event.external_transaction_id,
                event.scope,
                event.at,
                event.at,
                start_known=event.kind == SessionEventKind.STARTED,
            )
        updated = replace(old, updated_at=event.at, sequence=event.sequence)
        if event.charging_state is not None and (
            old.state_at is None or event.at >= old.state_at
        ):
            updated = replace(
                updated, charging_state=event.charging_state, state_at=event.at
            )
        for observation in sorted(observations, key=lambda o: o.observed_at):
            if observation.observed_at <= event.at:
                updated = updated.observe(observation)
        if event.meter_wh is not None:
            updated = updated.meter(event.meter_wh, event.at)
        if event.kind == SessionEventKind.ENDED:
            # No endpoint at the end timestamp means final accounting is unknown,
            # rather than silently labelling an older periodic register as final.
            if updated.energy_at != event.at:
                updated = replace(updated, energy_end_wh=None, energy_charged_wh=None)
            updated = replace(
                updated,
                ended_at=event.at,
                current_power_w=Fraction(0),
                end_reason=event.end_reason,
            )
        self._records[updated.session_id] = updated
        self._latest[event.scope] = updated.session_id
        self._publish()
        return True

    def observe(self, observations, external_id=None):
        changed = False
        for observation in sorted(observations, key=lambda o: o.observed_at):
            old = self.get(observation.channel.scope)
            if (
                old is None
                or not old.active
                or (
                    external_id is not None
                    and old.external_transaction_id != external_id
                )
            ):
                continue
            updated = old.observe(observation)
            if updated != old:
                self._records[old.session_id] = updated
                changed = True
        if changed:
            self._publish()

    def dump(self):
        records = []
        for session in self._records.values():
            row = asdict(session)
            row["scope"] = scope_parts(session.scope)
            for field in fields(session):
                value = getattr(session, field.name)
                if isinstance(value, datetime):
                    row[field.name] = value.isoformat()
                elif isinstance(value, Fraction):
                    row[field.name] = str(value)
            row["charging_state"] = session.charging_state.value
            row["duration_seconds"] = (
                session.duration(session.ended_at)
                if session.ended_at is not None
                else None
            )
            records.append(row)
        return {
            "records": records,
            "latest": list(self._latest.values()),
            "next_transaction_id": self.next_transaction_id,
        }

    def restore(self, data):
        """Validate the complete document before replacing the ledger."""
        records = {}
        for raw in data["records"]:
            row = dict(raw)
            row.pop("duration_seconds", None)
            row["scope"] = scope_from_parts(row["scope"])
            row["charging_state"] = State(row["charging_state"])
            for key in (
                "started_at",
                "updated_at",
                "ended_at",
                "energy_at",
                "power_at",
                "state_at",
                "power_valid_until",
            ):
                if row[key] is not None:
                    row[key] = datetime.fromisoformat(row[key])
            for key in (
                "energy_start_wh",
                "energy_end_wh",
                "energy_charged_wh",
                "current_power_w",
                "max_power_w",
            ):
                if row[key] is not None:
                    row[key] = Fraction(row[key])
            session = ChargingSession(**row)
            if session.session_id in records:
                raise ValueError("duplicate session ID")
            records[session.session_id] = session
        latest = {records[key].scope: key for key in data["latest"]}
        counter = data["next_transaction_id"]
        if (
            type(counter) is not int
            or counter < 1
            or len(latest) != len(data["latest"])
        ):
            raise ValueError("invalid session index")
        self._records, self._latest, self.next_transaction_id = records, latest, counter
        self._publish()
