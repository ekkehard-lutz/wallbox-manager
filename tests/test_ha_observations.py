"""Real HA registry/state-machine tests for dynamic scoped operational entities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
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
    assert len(entities) == 7  # Five meters and the two canonical state enums.
    assert len({e.unique_id for e in entities}) == 7
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
    assert by_key[(samples[5].channel, "connector_state")].native_value == "occupied"
    assert by_key[(samples[6].channel, "charging_state")].native_value == "connected"
    assert (
        by_key[(samples[5].channel, "connector_state")].extra_state_attributes[
            "connector_id"
        ]
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
    assert by_key[(samples[5].channel, "connector_state")].native_value == "unknown"
    assert by_key[(samples[6].channel, "charging_state")].native_value == "unknown"
    assert not any(
        e.translation_key
        in {"available", "occupied", "vehicle_connected", "charging_active"}
        for p in platforms
        for e in p.entities.values()
    )
    assert set(er.async_get(hass).entities) == ids


async def test_disconnect_reload_preserve_identity_and_unsubscribe(diagnostics):
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
    await unload()
    assert not runtime._listeners


@pytest.mark.parametrize(
    "quantity,value",
    [(Quantity.POWER_L3, 0), (Quantity.CURRENT_L3, 0), (Quantity.POWER, 4200)],
)
async def test_known_meter_outlives_deadline_but_not_connection(
    diagnostics, quantity, value
):
    from custom_components.wallbox_manager.core.events import StationIdentity

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("garage"))
    scope = EvseId(token.station, "1")
    # The last channel update was five minutes ago; the station is still live.
    sample = observation(
        scope, quantity, value, datetime.now(UTC) - timedelta(minutes=5)
    )
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    entity = operational(platforms)[0]
    assert not sample.fresh(datetime.now(UTC))  # Still unsafe for endpoint accounting.
    assert entity.available and entity.native_value == value
    assert hass.states.get(entity.entity_id).state == str(float(value))
    runtime.disconnect(token)
    assert not entity.available
    assert hass.states.get(entity.entity_id).state == "unavailable"
    current = runtime.connect(token.station)
    assert not entity.available
    assert not runtime.observe(token, (sample,))
    assert not entity.available
    runtime.observe(current, (observation(scope, quantity, value),))
    assert entity.available
    boot = runtime.boot(current, StationIdentity())
    assert not entity.available
    assert not runtime.observe(current, (observation(scope, quantity, value),))
    runtime.observe(boot, (observation(scope, quantity, value),))
    assert entity.available and entity.native_value == value


@pytest.mark.parametrize(
    "state",
    [State.CHARGING, State.CONNECTED, State.SUSPENDED_STATION, State.SUSPENDED_VEHICLE],
)
async def test_canonical_charging_enum_keeps_suspension_states(diagnostics, state):
    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("garage"))
    scope = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.observe(token, (observation(scope, Quantity.CHARGING_STATE, state),))
    await hass.async_block_till_done()
    entities = operational(platforms)
    assert len(entities) == 1
    assert entities[0].native_value == state.value
    assert {"charging", "connected", "suspended_station", "suspended_vehicle"} <= set(
        entities[0].options
    )
    assert all(
        e.translation_key == "connected"
        for p in platforms
        if p.domain == "binary_sensor"
        for e in p.entities.values()
    )


async def test_card_scope_join_rejects_ambiguous_parent_meter(diagnostics):
    from custom_components.wallbox_manager.ownership import identity

    hass, config, runtime, platforms, _, _ = diagnostics
    station = StationId("garage")
    evse = EvseId(station, "1")
    connector = ConnectorId(evse, "1")
    token = runtime.connect(station)
    runtime.observe(
        token,
        (
            observation(connector, Quantity.CHARGING_STATE, State.CHARGING),
            observation(evse, Quantity.POWER, 4000),
        ),
    )
    await hass.async_block_till_done()
    power = next(
        e for e in operational(platforms) if e.channel.quantity == Quantity.POWER
    )
    attrs = power.extra_state_attributes
    assert attrs["wallbox_manager_role"] == "power"
    assert attrs["wallbox_manager_targets"] == [identity(config.entry_id, connector)]
    assert attrs["valid_until"]
    runtime.observe(
        token,
        (
            observation(
                ConnectorId(evse, "2"), Quantity.CHARGING_STATE, State.CONNECTED
            ),
        ),
    )
    await hass.async_block_till_done()
    assert "wallbox_manager_targets" not in power.extra_state_attributes
    exact = next(e for e in operational(platforms) if e.channel.scope == connector)
    assert exact.extra_state_attributes["wallbox_manager_target"] == identity(
        config.entry_id, connector
    )
