"""Thin projections of scoped, push-based runtime observations."""

from datetime import UTC, datetime

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .core.models import ConnectorId, EvseId
from .core.telemetry import station_of
from .entity import StationEntity, observation_unique_id


class ObservationEntity(StationEntity):
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
        self._expire_cancel = None

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
            and observation.fresh(datetime.now(UTC))
        )

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        scope = self.channel.scope
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

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_expiry)
        self._schedule_expiry()

    @callback
    def _changed(self, snapshot):
        super()._changed(snapshot)
        if snapshot.token.station == self.station:
            self._schedule_expiry()

    @callback
    def _cancel_expiry(self):
        if self._expire_cancel is not None:
            self._expire_cancel()
            self._expire_cancel = None

    @callback
    def _schedule_expiry(self):
        self._cancel_expiry()
        observation = self.observation
        if self.available and observation.valid_until is not None:
            self._expire_cancel = async_track_point_in_utc_time(
                self.hass, self._expired, observation.valid_until
            )

    @callback
    def _expired(self, now):
        self._expire_cancel = None
        self.async_write_ha_state()
