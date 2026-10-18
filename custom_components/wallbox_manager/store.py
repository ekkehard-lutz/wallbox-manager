"""One entry's canonical observations; direct, keyed entity publication."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Observation:
    value: object
    source: str
    observed_at: datetime
    received_at: datetime
    generation: tuple[int, int]
    unit: str | None = None
    valid_until: datetime | None = None
    status: str = "ready"


class State:
    def __init__(self, binding):
        self.binding = tuple(binding)
        self.runtime_id = uuid4().hex
        self.generation = (0, 0)
        self.epoch = datetime.now(UTC)
        self.missing = set()
        self.add_session = lambda scope: None
        self.add_active = lambda scope: None
        self.connected = False
        self.identity = {}
        self.observations = {}
        self.listeners = {}
        self.add_sensor = None
        self.session = None
        self.closed_transactions = set()
        self.save_session = lambda: None
        self.accepted = self.changed = 0

    def accepts(self, scope):
        return all(
            a is None or a == b for a, b in zip(scope, self.binding, strict=True)
        )

    def watch(self, key, listener):
        self.listeners.setdefault(key, set()).add(listener)

        def remove():
            self.listeners[key].discard(listener)
            if not self.listeners[key]:
                del self.listeners[key]

        return remove

    def publish(self, *keys):
        for key in keys:
            for listener in tuple(self.listeners.get(key, ())):
                try:
                    listener()
                except Exception:
                    _LOGGER.exception("Entity publication failed for %s", key)

    def reset(self, *, connected, boot=False):
        connection, incarnation = self.generation
        self.generation = (
            (connection, incarnation + 1)
            if boot
            else (connection + 1, 0)
            if connected
            else self.generation
        )
        self.epoch = datetime.now(UTC)
        self.connected = connected
        self.missing = set(
            (
                "voltage_l1",
                "voltage_l2",
                "voltage_l3",
                "current_l1",
                "current_l2",
                "current_l3",
                "power",
                "energy",
                "connector_state",
                "charging_state",
                "control_authority",
                "charging_enabled",
                "physical_phases",
            )
        )
        # Last session remains historical; live activity must be re-established.
        self.publish(*tuple(self.listeners))
        return self.generation

    def current(self, generation):
        return self.connected and generation == self.generation

    def observe(
        self,
        generation,
        key,
        value,
        source,
        at=None,
        *,
        unit=None,
        valid_until=None,
        status="ready",
    ):
        if not self.current(generation) or not self.accepts(key[1]):
            return False
        now = datetime.now(UTC)
        at = at or now
        if at.utcoffset() is None or at < self.epoch or at > now + timedelta(seconds=5):
            return False
        previous = self.observations.get(key)
        same_generation = previous and previous.generation == generation
        if same_generation and at < previous.observed_at:
            return False
        if same_generation and at == previous.observed_at:
            if previous.value == value:
                return False
            value, status = None, "unknown"
        if value is None and status == "ready":
            status = "unknown"
        observation = Observation(
            value, source, at, now, generation, unit, valid_until, status
        )
        if observation == previous:
            return False
        self.accepted += 1
        self.changed += previous is None or previous.value != value
        self.observations[key] = observation
        if (
            previous is None
            and self.add_sensor
            and key[0] in ("observation", "capability")
        ):
            self.add_sensor(key)
        # Timestamp refreshes belong to the canonical store, not Recorder.
        # Permission retains its one-shot expiry publication and exact deadline.
        if (
            not same_generation
            or (previous.value, previous.source, previous.unit, previous.status)
            != (value, source, unit, status)
            or key[2] == "charging_enabled"
        ):
            self.publish(key)
        if key[2] in self.missing and value is not None:
            self.missing.discard(key[2])
            self.publish(("station", (self.binding[0], None, None), "discovery"))
        return True

    def last_observed_value(self, key):
        """Current-generation value, without asserting short-lived proof validity."""
        observation = self.observations.get(key)
        if not observation or not self.current(observation.generation):
            return None
        return observation.value

    def value(self, key):
        observation = self.observations.get(key)
        if observation is None or not self.current(observation.generation):
            return None
        if observation.valid_until and datetime.now(UTC) >= observation.valid_until:
            return None
        return observation.value
