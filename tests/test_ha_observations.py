"""Real HA registry/state-machine tests for dynamic scoped operational entities."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers import entity_registry as er
from test_ha_diagnostics import diagnostics as diagnostics

from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)
from custom_components.wallbox_manager.observation_entity import ObservationEntity


def operational(platforms):
    return [
        entity
        for platform in platforms
        for entity in platform.entities.values()
        if isinstance(entity, ObservationEntity)
    ]


def observation(scope, quantity, value, now=None, lifetime=120):
    now = now or datetime.now(UTC)
    return Observation(
        Channel(scope, quantity),
        value,
        now,
        now,
        now + timedelta(seconds=lifetime),
        "test",
    )


async def test_observed_capability_entities_metadata_and_scopes(diagnostics):
    hass, config, runtime, platforms, _, _ = diagnostics
    station = StationId("a")
    token = runtime.connect(station)
    await hass.async_block_till_done()
    assert not operational(platforms)
    evse = EvseId(station, "1")
    connector = ConnectorId(evse, "1")
    invalid = observation(evse, Quantity.VOLTAGE_L3, None)
    runtime.observe(token, (invalid,))
    await hass.async_block_till_done()
    assert not operational(platforms)
    samples = (
        observation(station, Quantity.ENERGY, 12345),
        observation(evse, Quantity.POWER, 5000),
        observation(evse, Quantity.VOLTAGE_L1, 231),
        observation(evse, Quantity.CURRENT_L1, 16),
        observation(EvseId(station, "2"), Quantity.POWER, 2000),
        observation(connector, Quantity.CONNECTOR_STATE, State.OCCUPIED),
        observation(connector, Quantity.CHARGING_STATE, State.CONNECTED),
    )
    runtime.observe(token, samples)
    await hass.async_block_till_done()
    entities = operational(platforms)
    assert len(entities) == 11  # Five meters, two state enums, four projections.
    assert len({e.unique_id for e in entities}) == 11
    assert all(e.entity_category is None and not e.should_poll for e in entities)
    by_key = {(e.channel, e.translation_key): e for e in entities}
    for sample, unit, device_class in zip(
        samples[:4],
        ["kWh", "W", "V", "A"],
        [
            SensorDeviceClass.ENERGY,
            SensorDeviceClass.POWER,
            SensorDeviceClass.VOLTAGE,
            SensorDeviceClass.CURRENT,
        ],
        strict=True,
    ):
        entity = by_key[(sample.channel, sample.channel.quantity.value)]
        assert entity.native_unit_of_measurement == unit
        assert entity.device_class == device_class
        assert entity.state_class == (
            SensorStateClass.TOTAL_INCREASING
            if unit == "kWh"
            else SensorStateClass.MEASUREMENT
        )
        assert entity.available
    assert by_key[(samples[0].channel, "energy")].native_value == 12.345
    assert by_key[(samples[5].channel, "occupied")].is_on is True
    assert by_key[(samples[6].channel, "vehicle_connected")].is_on is True
    assert by_key[(samples[6].channel, "charging_active")].is_on is False
    assert (
        by_key[(samples[5].channel, "occupied")].extra_state_attributes["connector_id"]
        == "1"
    )
    ids = set(er.async_get(hass).entities)
    runtime.observe(
        token,
        tuple(
            replace(
                s, observed_at=s.observed_at + timedelta(seconds=1), value=State.UNKNOWN
            )
            for s in samples[5:]
        ),
    )
    assert by_key[(samples[5].channel, "occupied")].is_on is None
    assert by_key[(samples[6].channel, "charging_active")].is_on is None
    assert set(er.async_get(hass).entities) == ids


async def test_expiry_disconnect_reload_preserve_identity_and_unsubscribe(diagnostics):
    hass, config, runtime, platforms, setup, unload = diagnostics
    token = runtime.connect(StationId("garage"))
    sample = observation(
        EvseId(token.station, "1"), Quantity.POWER_L2, 200, lifetime=0.15
    )
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    entity = operational(platforms)[0]
    entity_id, unique_id = entity.entity_id, entity.unique_id
    assert hass.states.get(entity_id).state == "200.0"
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert not entity.available and entity._expire_cancel is None
    fresh = observation(sample.channel.scope, Quantity.POWER_L2, 0)
    runtime.observe(token, (fresh,))
    assert hass.states.get(entity_id).state == "0.0"
    runtime.observe(
        token,
        (
            replace(
                fresh, observed_at=fresh.observed_at + timedelta(seconds=1), value=None
            ),
        ),
    )
    assert hass.states.get(entity_id).state == "unavailable"
    runtime.disconnect(token)
    assert not entity.available
    assert entity._expire_cancel is None
    await unload()
    assert not runtime._listeners
    runtime = await setup()
    entity = operational(platforms)[0]
    assert entity.entity_id == entity_id and entity.unique_id == unique_id
    assert not entity.available
    token = runtime.connect(token.station)
    runtime.observe(token, (observation(sample.channel.scope, Quantity.POWER_L2, 300),))
    await hass.async_block_till_done()
    assert len(operational(platforms)) == 1
    assert entity.available and entity.native_value == 300
    assert entity._expire_cancel is not None
    await unload()
    assert not runtime._listeners and entity._expire_cancel is None
