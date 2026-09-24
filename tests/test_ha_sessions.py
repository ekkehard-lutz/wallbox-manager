"""Real HA entity states and registry identities for current/last sessions."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers import entity_registry as er
from test_ha_diagnostics import diagnostics as diagnostics
from test_ha_observations import observation

from custom_components.wallbox_manager.core.models import EvseId, StationId
from custom_components.wallbox_manager.core.sessions import SessionEvent
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import Quantity, State
from custom_components.wallbox_manager.session_entity import SessionEntity


def sessions(platforms):
    return {
        e.key: e
        for p in platforms
        for e in p.entities.values()
        if isinstance(e, SessionEntity)
    }


async def test_active_completed_replacement_and_reload(diagnostics):
    hass, config, runtime, platforms, setup, unload = diagnostics
    scope = EvseId(StationId("garage"), "1")
    token = runtime.connect(scope.station)
    at = datetime.now(UTC) - timedelta(seconds=10)
    await hass.async_block_till_done()
    assert not sessions(platforms)
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.STARTED, at, State.CONNECTED, meter_wh=1000),
    )
    runtime.observe(
        token,
        (
            observation(scope, Quantity.POWER, 5000, at + timedelta(seconds=1)),
            observation(scope, Quantity.ENERGY, 1400, at + timedelta(seconds=1)),
        ),
    )
    await hass.async_block_till_done()
    entities = sessions(platforms)
    assert len(entities) == 9
    assert "charging_state" not in entities
    assert entities["active"].is_on
    assert entities["ended"].native_value is None
    assert entities["started"].device_class == SensorDeviceClass.TIMESTAMP
    assert entities["ended"].device_class == SensorDeviceClass.TIMESTAMP
    assert entities["started"].native_value == at
    assert entities["duration"].native_unit_of_measurement == "s"
    assert entities["duration"].native_value >= 10
    assert entities["power"].native_value == 5000
    assert entities["energy"].native_value == 0.4
    assert entities["energy"].state_class is None
    assert runtime.sessions.get(scope).charging_state == State.CONNECTED
    assert all(e.available and e.entity_category is None for e in entities.values())
    identities = {k: (e.unique_id, e.entity_id) for k, e in entities.items()}
    runtime.disconnect(token)
    assert entities["active"].is_on and entities["power"].native_value is None
    persisted = runtime.sessions.dump()
    await unload()
    assert not runtime.sessions._listeners
    runtime = await setup()
    runtime.sessions.restore(persisted)
    await hass.async_block_till_done()
    entities = sessions(platforms)
    assert {k: (e.unique_id, e.entity_id) for k, e in entities.items()} == identities
    assert entities["active"].is_on and entities["power"].native_value is None
    token = runtime.connect(scope.station)
    end = at + timedelta(seconds=5)
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.ENDED, end, State.IDLE, "Local", meter_wh=1900),
    )
    await hass.async_block_till_done()
    assert not entities["active"].is_on
    assert hass.states.get(entities["active"].entity_id).state == "off"
    assert entities["power"].native_value == 0
    assert entities["energy"].native_value == 0.9
    assert entities["start_meter"].native_value == 1
    assert entities["end_meter"].native_value == 1.9
    assert entities["duration"].native_value == 5
    assert entities["ended"].native_value == end
    assert runtime.sessions.get(scope).charging_state == State.IDLE
    old_id = entities["id"].native_value
    runtime.disconnect(token)
    assert entities["power"].native_value == 0
    assert entities["energy"].native_value == 0.9
    token = runtime.connect(scope.station)
    runtime.session_event(
        token,
        SessionEvent(
            scope, "new", Kind.STARTED, end + timedelta(seconds=1), meter_wh=2000
        ),
    )
    assert entities["id"].native_value != old_id
    assert entities["energy"].native_value == 0
    assert entities["ended"].native_value is None
    assert entities["active"].is_on
    assert len(runtime.sessions.history()) == 1
    assert (
        len(
            [
                e
                for e in er.async_get(hass).entities.values()
                if ":session:" in e.unique_id
            ]
        )
        == 9
    )


async def test_offline_session_power_does_not_replace_live_reading(diagnostics):
    hass, _, runtime, platforms, _, _ = diagnostics
    scope = EvseId(StationId("garage"), "1")
    token = runtime.connect(scope.station)
    at = datetime.now(UTC) - timedelta(seconds=5)
    runtime.session_event(token, SessionEvent(scope, "tx", Kind.STARTED, at))
    runtime.observe(token, (observation(scope, Quantity.POWER, 1000, at),))
    await hass.async_block_till_done()
    power = sessions(platforms)["power"]
    assert power.native_value == 1000
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, at + timedelta(seconds=1)),
        (observation(scope, Quantity.POWER, 2000, at + timedelta(seconds=1)),),
    )
    assert power.native_value is None


async def test_card_session_metadata_includes_scoped_role_and_meter_expiry(diagnostics):
    from custom_components.wallbox_manager.core.models import ConnectorId
    from custom_components.wallbox_manager.ownership import identity

    hass, config, runtime, platforms, _, _ = diagnostics
    scope = ConnectorId(EvseId(StationId("garage"), "1"), "1")
    token = runtime.connect(scope.station)
    at = datetime.now(UTC) - timedelta(seconds=10)
    runtime.session_event(
        token, SessionEvent(scope, "tx", Kind.STARTED, at, meter_wh=1000)
    )
    sample = observation(scope, Quantity.ENERGY, 1500, at + timedelta(seconds=1))
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    entity = sessions(platforms)["energy"]
    attrs = entity.extra_state_attributes
    assert attrs["wallbox_manager_role"] == "session_energy"
    assert attrs["wallbox_manager_target"] == identity(config.entry_id, scope)
    assert attrs["session_active"] and attrs["connected"]
    assert attrs["valid_until"] == sample.valid_until.isoformat()
    runtime.disconnect(token)
    assert not entity.extra_state_attributes["connected"]
