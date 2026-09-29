"""Thin projections of scoped, push-based runtime observations."""

from dataclasses import replace

from .core.models import ConnectorId, EvseId
from .core.telemetry import STATE_OPTIONS, Quantity, State, station_of
from .entity import StationEntity, observation_unique_id, scope_attributes


class ObservationEntity(StationEntity):
    """Meters follow connection generations; physical enums retain display history."""

    _attr_entity_category = None

    def __init__(self, runtime, entry_id, channel, projection=None):
        super().__init__(
            runtime,
            entry_id,
            station_of(channel.scope),
            projection or channel.quantity.value,
        )
        self.channel = channel
        self._last_known = None
        self._last_live = None
        self._attr_unique_id = observation_unique_id(entry_id, channel, projection)
        scope = channel.scope
        label = "Station"
        if isinstance(scope, EvseId):
            label = f"EVSE {scope.value}"
        elif isinstance(scope, ConnectorId):
            label = f"EVSE {scope.evse.value} / {scope.value}"
        self._attr_translation_placeholders = {"scope": label}

    @property
    def physical_state(self):
        return self.channel.quantity in STATE_OPTIONS

    @property
    def observation(self):
        value = self.snapshot.observation(self.channel) if self.snapshot else None
        if not self.physical_state:
            return value
        if (
            value
            and value.value not in (None, State.UNKNOWN, State.UNAVAILABLE)
            and value is not self._last_live
        ):
            self._last_known = value
        self._last_live = value
        target = self.runtime.cp_scope(self.channel.scope)
        enabled = self.runtime.enabled_observation(target) if target else None
        if (
            self.channel.quantity == Quantity.CHARGING_STATE
            and self._last_known
            and self._last_known.value == State.CHARGING
            and enabled
            and self.runtime.enabled(target) is False
        ):
            # Permission readback proves charging stopped, but cannot prove
            # departure. Keep the canonical enum and retain connector history.
            self._last_known = replace(
                self._last_known,
                value=State.CONNECTED,
                observed_at=enabled.observed_at,
                received_at=enabled.observed_at,
                valid_until=None,
                source="runtime:charging_disabled",
            )
        return self._last_known

    @property
    def state_fresh(self):
        observation = self.observation
        if observation and observation.source == "runtime:charging_disabled":
            return False
        live = self.snapshot.observation(self.channel) if self.snapshot else None
        return bool(
            live
            and live == self.observation
            and self.runtime.physical_state_fresh(live)
        )

    @property
    def available(self):
        observation = self.observation
        if self.physical_state:
            return bool(observation and observation.value is not None)
        return bool(
            self.snapshot
            and self.snapshot.connected
            and observation
            and observation.value is not None
        )

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        scope = self.channel.scope
        attrs.update(scope_attributes(self.runtime, self.entry_id, scope))
        if isinstance(scope, (EvseId, ConnectorId)):
            attrs["evse_id"] = (
                scope.value if isinstance(scope, EvseId) else scope.evse.value
            )
        if isinstance(scope, ConnectorId):
            attrs["connector_id"] = scope.value
        if self.physical_state:
            attrs.update(
                state_fresh=self.state_fresh,
                state_represents="confirmed_charging_disabled"
                if self.observation
                and self.observation.source == "runtime:charging_disabled"
                else "last_known_observation",
            )
        if observation := self.observation:
            attrs.update(
                observed_at=observation.observed_at.isoformat(),
                received_at=observation.received_at.isoformat(),
                source=observation.source,
                valid_until=observation.valid_until.isoformat()
                if observation.valid_until
                else None,
            )
        return attrs
