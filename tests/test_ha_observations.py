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
    assert by_key[(samples[5].channel, "connector_state")].native_value == "occupied"
    assert not by_key[(samples[5].channel, "connector_state")].state_fresh
    assert by_key[(samples[6].channel, "charging_state")].native_value == "connected"
    assert not by_key[(samples[6].channel, "charging_state")].state_fresh
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


@pytest.mark.parametrize("parent", [False, True])
async def test_cp_off_retains_physical_states_and_reload_only_restores_display(
    diagnostics, parent
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation
    from custom_components.wallbox_manager.core.sessions import (
        SessionEvent,
        SessionEventKind,
    )

    hass, _, runtime, platforms, setup, unload = diagnostics
    token = runtime.connect(StationId("cp"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    now = datetime.now(UTC)
    runtime.session_event(
        token,
        SessionEvent(target, "vehicle", SessionEventKind.STARTED, now, State.CHARGING),
        live=True,
    )
    runtime.observe(
        token,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
            observation(
                target.evse if parent else target,
                Quantity.CHARGING_STATE,
                State.CHARGING,
            ),
        ),
    )
    await hass.async_block_till_done()
    states = {e.channel.quantity: e for e in operational(platforms)}
    assert all(e.state_fresh for e in states.values())
    at = datetime.now(UTC)
    runtime.observe_enabled(
        token, EnabledObservation(target, False, at, at + timedelta(seconds=60))
    )
    runtime.observe(
        token,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),
            observation(
                target.evse if parent else target, Quantity.CHARGING_STATE, State.IDLE
            ),
        ),
    )
    assert states[Quantity.CONNECTOR_STATE].native_value == "occupied"
    assert states[Quantity.CHARGING_STATE].native_value == "charging"
    assert not states[Quantity.CONNECTOR_STATE].state_fresh
    assert not states[Quantity.CHARGING_STATE].state_fresh
    assert (
        hass.states.get(states[Quantity.CHARGING_STATE].entity_id).state == "charging"
    )
    assert runtime.sessions.get(target).active
    assert runtime.enabled(target) is False
    await unload()
    runtime = await setup()
    states = {e.channel.quantity: e for e in operational(platforms)}
    assert states[Quantity.CONNECTOR_STATE].native_value == "occupied"
    assert states[Quantity.CHARGING_STATE].native_value == "charging"
    assert all(e.available and not e.state_fresh for e in states.values())
    token = runtime.connect(target.station)
    at = datetime.now(UTC)
    runtime.observe_enabled(
        token, EnabledObservation(target, True, at, at + timedelta(seconds=60))
    )
    assert all(not e.state_fresh for e in states.values())
    runtime.observe(
        token,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),
            observation(
                target.evse if parent else target, Quantity.CHARGING_STATE, State.IDLE
            ),
        ),
    )
    assert states[Quantity.CONNECTOR_STATE].native_value == "available"
    assert states[Quantity.CHARGING_STATE].native_value == "idle"
    assert all(e.state_fresh for e in states.values())


async def test_explicit_departure_still_ends_session_when_cp_is_off(diagnostics):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation
    from custom_components.wallbox_manager.core.sessions import (
        SessionEvent,
        SessionEventKind,
    )

    _, _, runtime, _, _, _ = diagnostics
    token = runtime.connect(StationId("cp"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    at = datetime.now(UTC)
    runtime.session_event(
        token,
        SessionEvent(target, "vehicle", SessionEventKind.STARTED, at, State.CHARGING),
        live=True,
    )
    runtime.observe_enabled(
        token, EnabledObservation(target, False, at, at + timedelta(seconds=60))
    )
    runtime.session_event(
        token,
        SessionEvent(
            target,
            "vehicle",
            SessionEventKind.ENDED,
            datetime.now(UTC),
            State.IDLE,
            "EVDeparted",
        ),
        live=True,
    )
    assert not runtime.sessions.get(target).active


@pytest.mark.parametrize("parent", [False, True])
@pytest.mark.parametrize("read_first", [False, True])
async def test_restart_cp_events_before_or_after_first_on_read_are_fresh(
    diagnostics, parent, read_first
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, setup, unload = diagnostics
    target = ConnectorId(EvseId(StationId("cp"), "1"), "1")
    scope = target.evse if parent else target

    def samples():
        return (
            observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
            observation(scope, Quantity.CHARGING_STATE, State.CHARGING),
        )

    token = runtime.connect(target.station)
    runtime.observe(token, samples())
    await hass.async_block_till_done()
    await unload()
    runtime = await setup()
    states = operational(platforms)
    assert all(e.available and not e.state_fresh for e in states)
    token = runtime.connect(target.station)
    assert all(not e.state_fresh for e in states)  # Socket alone proves nothing.
    if not read_first:
        runtime.observe(token, samples())
    at = datetime.now(UTC)
    runtime.observe_enabled(
        token, EnabledObservation(target, True, at, at + timedelta(seconds=60))
    )
    if read_first:
        assert all(not e.state_fresh for e in states)
        runtime.observe(token, samples())
    assert all(e.state_fresh for e in states)
    assert all(hass.states.get(e.entity_id).attributes["state_fresh"] for e in states)


async def test_real_off_on_transition_still_requires_new_cp_evidence(diagnostics):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("cp"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.observe(
        token,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
            observation(target, Quantity.CHARGING_STATE, State.CHARGING),
        ),
    )
    await hass.async_block_till_done()
    for enabled in (True, False, True):
        at = datetime.now(UTC)
        runtime.observe_enabled(
            token, EnabledObservation(target, enabled, at, at + timedelta(seconds=60))
        )
    states = {e.channel.quantity: e for e in operational(platforms)}
    assert states[Quantity.CHARGING_STATE].native_value == "charging"
    assert all(not e.state_fresh for e in states.values())
    runtime.observe(
        token, (observation(target, Quantity.CHARGING_STATE, State.CHARGING),)
    )
    assert states[Quantity.CHARGING_STATE].native_value == "charging"
    assert states[Quantity.CHARGING_STATE].state_fresh


@pytest.mark.parametrize("initial", [None, True, False])
@pytest.mark.parametrize("read_first", [False, True])
async def test_unknown_permission_recovery_only_fences_confirmed_off(
    diagnostics, initial, read_first
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("cp"))
    target = ConnectorId(EvseId(token.station, "1"), "1")

    def permission(value):
        at = datetime.now(UTC)
        runtime.observe_enabled(
            token, EnabledObservation(target, value, at, at + timedelta(seconds=60))
        )

    def samples():
        return (
            observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
            observation(target, Quantity.CHARGING_STATE, State.CHARGING),
        )

    if initial is not None:
        permission(initial)
    permission(None)  # Failed first discovery read, or a transient polling failure.
    if read_first:
        permission(True)
    evidence = samples()
    runtime.observe(token, evidence)
    await hass.async_block_till_done()
    if not read_first:
        assert all(
            e.state_fresh == (initial is not False) for e in operational(platforms)
        )
        permission(True)
    fresh = initial is not False or read_first
    assert all(e.state_fresh == fresh for e in operational(platforms))
    assert all(
        hass.states.get(e.entity_id).attributes["state_fresh"] == fresh
        for e in operational(platforms)
    )
    # Reordered older reports do not replace new evidence or move its timestamp.
    runtime.observe(
        token,
        tuple(
            replace(o, observed_at=o.observed_at - timedelta(seconds=1))
            for o in evidence
        ),
    )
    assert all(
        runtime.get(token.station).observation(o.channel) == (o if fresh else None)
        for o in evidence
    )
    runtime.observe(token, samples())
    assert all(e.state_fresh for e in operational(platforms))


async def test_permission_recovery_cannot_extend_expired_physical_evidence(diagnostics):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("cp"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    sample = observation(target, Quantity.CHARGING_STATE, State.CHARGING)
    sample = replace(sample, valid_until=sample.observed_at)
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    for value in (None, True):
        at = datetime.now(UTC)
        runtime.observe_enabled(
            token, EnabledObservation(target, value, at, at + timedelta(seconds=60))
        )
    assert all(not e.state_fresh for e in operational(platforms))
    assert runtime.get(token.station).observation(sample.channel) == sample
    runtime.observe(
        token, (observation(target, Quantity.CHARGING_STATE, State.CHARGING),)
    )
    assert all(e.state_fresh for e in operational(platforms))


@pytest.mark.parametrize("version", ["1.6", "2.0.1", "2.1"])
@pytest.mark.parametrize("read_first", [False, True])
async def test_cold_available_creates_idle_without_transaction_or_old_channel(
    diagnostics, version, read_first
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(
        StationId("cold"), protocol="ocpp", protocol_version=version
    )
    target = ConnectorId(EvseId(token.station, "1"), "1")

    def permission(value):
        at = datetime.now(UTC)
        runtime.observe_enabled(
            token, EnabledObservation(target, value, at, at + timedelta(seconds=60))
        )

    permission(None)
    if read_first:
        permission(True)
    assert runtime.get(token.station).supported_channels == ()
    sample = replace(
        observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),
        source=f"ocpp{version}:StatusNotification",
        valid_until=None,
    )
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    states = {e.channel.quantity: e for e in operational(platforms)}
    assert states[Quantity.CONNECTOR_STATE].native_value == "available"
    assert states[Quantity.CHARGING_STATE].native_value == "idle"
    assert all(e.state_fresh for e in states.values())
    if not read_first:
        permission(True)
    assert all(e.state_fresh for e in states.values())
    assert not runtime.sessions.latest
    for e in states.values():
        attrs = hass.states.get(e.entity_id).attributes
        assert attrs["observed_at"] == sample.observed_at.isoformat()
        assert attrs["source"] == sample.source
        assert attrs["cp_observability"] == "unverified"  # No GPIO-origin claim.
        assert attrs["state_fresh"] is True


async def test_repeat_conflict_and_old_available_preserve_source_evidence(diagnostics):
    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("ordered"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    first = observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE)
    runtime.observe(token, (first,))
    await hass.async_block_till_done()
    newer = observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE)
    runtime.observe(token, (newer,))
    runtime.observe(token, (first, replace(newer, received_at=datetime.now(UTC))))
    assert runtime.get(token.station).observation(first.channel) == newer
    assert all(e.state_fresh for e in operational(platforms))
    runtime.observe(token, (replace(newer, value=State.OCCUPIED),))
    assert runtime.get(token.station).observation(first.channel).value is None
    assert all(not e.state_fresh for e in operational(platforms))
    # Equal-time replay cannot undo a contradictory observation.
    runtime.observe(token, (newer,))
    assert all(not e.state_fresh for e in operational(platforms))
    runtime.observe(
        token, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
    )
    assert all(e.state_fresh for e in operational(platforms))


@pytest.mark.parametrize("reset", ["socket", "boot", "reload"])
async def test_old_acquisition_on_new_generation_cannot_refresh_display(
    diagnostics, reset
):
    from custom_components.wallbox_manager.core.events import StationIdentity

    hass, _, runtime, platforms, setup, unload = diagnostics
    token = runtime.connect(StationId("generation"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    sample = observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE)
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    if reset == "reload":
        await unload()
        runtime = await setup()
        current = runtime.connect(token.station)
    elif reset == "boot":
        current = runtime.boot(token, StationIdentity())
    else:
        runtime.disconnect(token)
        current = runtime.connect(token.station)
    assert not runtime.observe(token, (sample,))
    runtime.observe(current, (replace(sample, received_at=datetime.now(UTC)),))
    assert runtime.get(token.station).observations == ()
    assert all(not e.state_fresh for e in operational(platforms))
    assert all(e.native_value in ("available", "idle") for e in operational(platforms))
    runtime.observe(
        current, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
    )
    assert all(e.state_fresh for e in operational(platforms))


@pytest.mark.parametrize("offline", [False, True])
async def test_pause_preserves_history_until_live_departure_and_new_available(
    diagnostics, offline
):
    from custom_components.wallbox_manager.core.sessions import (
        SessionEvent,
        SessionEventKind,
    )

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("pause"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.session_event(
        token,
        SessionEvent(target, "real", SessionEventKind.STARTED, datetime.now(UTC)),
        live=True,
    )
    samples = (
        observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
        observation(target, Quantity.CHARGING_STATE, State.CHARGING),
    )
    runtime.observe(token, samples)
    await hass.async_block_till_done()
    runtime.observe_zero_point(token, target)
    runtime.observe(
        token, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
    )
    states = {e.channel.quantity: e for e in operational(platforms)}
    assert states[Quantity.CONNECTOR_STATE].native_value == "occupied"
    assert states[Quantity.CHARGING_STATE].native_value == "charging"
    assert all(not e.state_fresh for e in states.values())
    assert all(
        e.extra_state_attributes["physical_observability"] == "pause_cp_unverified"
        for e in states.values()
    )
    assert all(e.observation in samples for e in states.values())
    runtime.session_event(
        token,
        SessionEvent(
            target,
            "real",
            SessionEventKind.ENDED,
            datetime.now(UTC),
            State.IDLE,
            "EVDeparted",
        ),
        live=not offline,
    )
    runtime.observe(
        token, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
    )
    assert all(e.state_fresh is (not offline) for e in states.values())
    assert states[Quantity.CONNECTOR_STATE].native_value == (
        "occupied" if offline else "available"
    )
    assert states[Quantity.CHARGING_STATE].native_value == (
        "charging" if offline else "idle"
    )


@pytest.mark.parametrize("absent", [False, True])
async def test_off_current_report_cannot_invent_independent_cp_evidence(
    diagnostics, absent
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("off"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    sample = observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED)
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    at = datetime.now(UTC)
    runtime.observe_enabled(
        token, EnabledObservation(target, False, at, at + timedelta(seconds=60))
    )
    epoch = runtime._cp_epoch[target]
    if absent:
        runtime.observe(
            token, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
        )
    refreshed = datetime.now(UTC)
    runtime.observe_enabled(
        token,
        EnabledObservation(target, False, refreshed, refreshed + timedelta(seconds=60)),
    )
    assert (
        runtime._cp_epoch[target] == epoch
    )  # Same OFF is not a new physical transition.
    entity = operational(platforms)[0]
    assert entity.native_value == "occupied" and not entity.state_fresh
    assert entity.observation == sample
    assert (
        entity.extra_state_attributes["physical_observability"]
        == "permission_cp_unverified"
    )
    assert runtime.enabled(target) is False


async def test_authority_and_unknown_permission_do_not_invalidate_independent_state(
    diagnostics,
    monkeypatch,
):
    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("independent"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.observe(
        token, (observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),)
    )
    await hass.async_block_till_done()
    for value in (ControlAuthority.LOCAL, ControlAuthority.REMOTE):
        at = datetime.now(UTC)
        runtime.observe_authority(
            token, AuthorityObservation(token.station, value, at, "test")
        )
        runtime.observe_enabled(
            token, EnabledObservation(target, None, at, at + timedelta(seconds=60))
        )
        assert all(e.state_fresh for e in operational(platforms))
    # The physical evidence also outlives permission's validity, with no new timeout.
    at = datetime.now(UTC)
    runtime.observe_enabled(
        token, EnabledObservation(target, True, at, at + timedelta(seconds=60))
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=61)

    monkeypatch.setattr("custom_components.wallbox_manager.runtime.datetime", Clock)
    assert runtime.enabled(target) is None
    assert all(e.state_fresh for e in operational(platforms))


async def test_reload_keeps_pause_inhibition_without_restoring_live_evidence(
    diagnostics,
):
    hass, _, runtime, platforms, setup, unload = diagnostics
    token = runtime.connect(StationId("paused-reload"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.observe(
        token,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.OCCUPIED),
            observation(target, Quantity.CHARGING_STATE, State.CHARGING),
        ),
    )
    await hass.async_block_till_done()
    runtime.observe_zero_point(token, target)
    since = runtime.physical_limit_since(target)
    runtime.disconnect(token)
    await unload()
    runtime = await setup()
    current = runtime.connect(target.station)
    runtime.observe(
        current,
        (
            observation(target, Quantity.CONNECTOR_STATE, State.AVAILABLE),
            observation(target, Quantity.CHARGING_STATE, State.CONNECTED),
        ),
    )
    assert runtime.physical_limit_since(target) == since
    assert runtime.physical_limitation(target) == "pause_cp_unverified"
    assert runtime.get(target.station).observations == ()
    assert all(not e.state_fresh for e in operational(platforms))
    assert {e.native_value for e in operational(platforms)} == {"occupied", "charging"}
