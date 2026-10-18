"""One entry's canonical observations; direct, keyed entity publication."""

import logging
from dataclasses import dataclass, replace
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
    status: str = "ready"
    # Phase-only report watermark and consecutive invalid count (saturated at 2).
    # Acquisition/provenance fields continue to describe the last valid sample.
    reported_at: datetime | None = None
    reported_generation: tuple[int, int] | None = None
    invalid_count: int = 0


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
        phase = key[0] == "station" and key[2] == "physical_phases"
        before = self.last_observed_value(key) if phase else None
        latest = (previous.reported_at or previous.observed_at) if previous else None
        same_report_generation = (
            previous
            and (previous.reported_generation or previous.generation) == generation
        )
        if same_report_generation and at < latest:
            return False
        if same_report_generation and at == latest:
            # Conflicts count as one invalid report; replay cannot count twice
            # or recover a value without a newer valid sample.
            if (phase and previous.invalid_count) or previous.value == value:
                return False
            value, status = None, "unknown"
        if value is None and status == "ready":
            status = "unknown"
        observation = Observation(
            value,
            source,
            at,
            now,
            generation,
            unit,
            status,
            reported_at=at if phase else None,
            reported_generation=generation if phase else None,
            invalid_count=1 if phase and value is None else 0,
        )
        if phase and value is None and previous:
            observation = replace(
                previous,
                reported_at=at,
                reported_generation=generation,
                invalid_count=min(2, previous.invalid_count + 1)
                if same_report_generation
                else 1,
            )
        if observation == previous:
            return False
        self.accepted += 1
        self.changed += previous is None or previous.value != observation.value
        self.observations[key] = observation
        if (
            previous is None
            and self.add_sensor
            and key[0] in ("observation", "capability")
        ):
            self.add_sensor(key)
        # Timestamp refreshes belong to the canonical store, not Recorder.
        if (
            previous is None
            or (not phase and not same_generation)
            or (previous.value, previous.source, previous.unit, previous.status)
            != (
                observation.value,
                observation.source,
                observation.unit,
                observation.status,
            )
            or (phase and before != self.last_observed_value(key))
        ):
            self.publish(key)
        if key[2] in self.missing and value is not None:
            self.missing.discard(key[2])
            self.publish(("station", (self.binding[0], None, None), "discovery"))
        return True

    def value(self, key):
        """Current-generation last value, tolerating one invalid phase report."""
        observation = self.observations.get(key)
        if (
            not observation
            or not self.current(observation.generation)
            or observation.invalid_count >= 2
        ):
            return None
        return observation.value

    def last_observed_value(self, key):
        """Compatibility alias for the single canonical observed-value view."""
        return self.value(key)
