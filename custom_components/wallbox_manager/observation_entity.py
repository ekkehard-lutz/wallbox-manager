"""Thin projections of scoped, push-based runtime observations."""

from .core.models import ConnectorId, EvseId
from .core.telemetry import station_of
from .entity import StationEntity, observation_unique_id, scope_attributes


class ObservationEntity(StationEntity):
    """Known values live for the connection generation, not the sample deadline."""

    _attr_entity_category = None

    def __init__(self, runtime, entry_id, channel, projection=None):
        super().__init__(
            runtime,
            entry_id,
            station_of(channel.scope),
            projection or channel.quantity.value,
        )
        self.channel = channel
        self._attr_unique_id = observation_unique_id(entry_id, channel, projection)
        scope = channel.scope
        label = "Station"
        if isinstance(scope, EvseId):
            label = f"EVSE {scope.value}"
        elif isinstance(scope, ConnectorId):
            label = f"EVSE {scope.evse.value} / {scope.value}"
        self._attr_translation_placeholders = {"scope": label}

    @property
    def observation(self):
        return self.snapshot.observation(self.channel) if self.snapshot else None

    @property
    def available(self):
        observation = self.observation
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
