"""Pure immutable observations, scoped support and generation/time fencing."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
    state_flag,
)
from custom_components.wallbox_manager.runtime import Runtime


def reading(now, scope=None, quantity=Quantity.POWER, value=1500):
    return Observation(
        Channel(scope or StationId("a"), quantity),
        value,
        now,
        now,
        now + timedelta(seconds=120),
        "test",
    )


def test_observation_immutable_and_native_units(now):
    observation = reading(now)
    with pytest.raises(FrozenInstanceError):
        observation.value = 0
    with pytest.raises(FrozenInstanceError):
        observation.channel.scope = StationId("b")
    assert observation.fresh(now)
    assert not observation.fresh(observation.valid_until)
    assert not replace(observation, value=None).fresh(now)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "0"])
def test_invalid_meter_value_rejected(now, value):
    with pytest.raises(ValueError):
        reading(now, value=value)


def test_observation_contracts(now):
    with pytest.raises(ValueError):
        replace(reading(now), valid_until=None)
    with pytest.raises(ValueError):
        replace(reading(now), observed_at=now.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(reading(now), valid_until=now - timedelta(seconds=1))
    with pytest.raises(ValueError):
        reading(now, quantity=Quantity.CONNECTOR_STATE, value=State.CHARGING)
    with pytest.raises(ValueError):
        Channel("a", Quantity.POWER)


def test_runtime_fences_boot_connection_and_incarnation(now):
    runtime = Runtime()
    old = runtime.connect(StationId("a"))
    sample = reading(now)
    assert runtime.observe(old, (sample,))
    saved = runtime.get(old.station)
    boot = runtime.boot(old, StationIdentity())
    assert not runtime.observe(old, (reading(now, value=9),))
    assert runtime.get(old.station).observations == ()
    assert runtime.get(old.station).supported_channels == (sample.channel,)
    assert saved.observations == (sample,)
    runtime.disconnect(boot)
    assert not runtime.observe(boot, (sample,))
    new = runtime.connect(old.station)
    assert not runtime.observe(boot, (sample,))
    assert runtime.observe(new, (sample,))
    assert not Runtime().observe(new, (sample,))


def test_monotonic_scoped_updates_and_invalidations(now):
    runtime = Runtime()
    token = runtime.connect(StationId("a"))
    scopes = [
        token.station,
        EvseId(token.station, "1"),
        EvseId(token.station, "2"),
        ConnectorId(EvseId(token.station, "1"), "1"),
        ConnectorId(EvseId(token.station, "1"), "2"),
    ]
    samples = tuple(reading(now, scope, value=n) for n, scope in enumerate(scopes))
    runtime.observe(token, samples)
    assert len(runtime.get(token.station).observations) == 5
    channel = samples[1].channel
    runtime.observe(
        token, (replace(samples[1], observed_at=now - timedelta(seconds=1), value=999),)
    )
    assert runtime.get(token.station).observation(channel).value == 1
    runtime.observe(token, (replace(samples[1], value=999),))
    assert runtime.get(token.station).observation(channel).value is None
    runtime.observe(
        token, (replace(samples[1], observed_at=now + timedelta(seconds=1), value=8),)
    )
    assert runtime.get(token.station).observation(channel).value == 8
    assert runtime.get(token.station).observation(samples[2].channel).value == 2
    with pytest.raises(ValueError):
        runtime.observe(token, (reading(now, StationId("other")),))


def test_support_requires_a_valid_observation_and_survives_value_loss(now):
    runtime = Runtime()
    token = runtime.connect(StationId("a"))
    sample = reading(now)
    runtime.observe(token, (replace(sample, value=None),))
    assert not runtime.get(token.station).supported_channels
    runtime.observe(token, (sample,))
    runtime.observe(
        token, (replace(sample, observed_at=now + timedelta(seconds=1), value=None),)
    )
    snapshot = runtime.get(token.station)
    assert snapshot.supported_channels == (sample.channel,)
    assert not snapshot.observation(sample.channel).fresh(now)
    assert snapshot.capabilities.envelopes == ()


@pytest.mark.parametrize(
    "state,flag,expected",
    [
        (State.AVAILABLE, "occupied", False),
        (State.OCCUPIED, "occupied", True),
        (State.FAULTED, "occupied", None),
        (State.UNKNOWN, "occupied", None),
        (None, "charging_active", None),
        (State.UNKNOWN, "charging_active", None),
        (State.CONNECTED, "charging_active", False),
        (State.CONNECTED, "vehicle_connected", True),
        (State.PREPARING, "vehicle_connected", None),
        (State.FINISHING, "vehicle_connected", None),
        (State.CHARGING, "charging_active", True),
        (State.SUSPENDED_VEHICLE, "charging_active", False),
        (State.SUSPENDED_STATION, "vehicle_connected", True),
        (State.IDLE, "vehicle_connected", False),
    ],
)
def test_state_flags_are_three_valued(state, flag, expected):
    assert state_flag(state, flag) is expected


def test_new_connector_status_reconciles_only_existing_same_scope_charging(now):
    runtime = Runtime()
    token = runtime.connect(StationId("a"))
    evse = EvseId(token.station, "1")
    connector = ConnectorId(evse, "1")

    def state(scope, quantity, value, offset=0):
        return Observation(
            Channel(scope, quantity),
            value,
            now + timedelta(seconds=offset),
            now + timedelta(seconds=offset),
            None,
            "status",
        )

    runtime.observe(
        token,
        (
            state(connector, Quantity.CHARGING_STATE, State.CHARGING),
            state(evse, Quantity.CHARGING_STATE, State.CHARGING),
        ),
    )
    runtime.observe(
        token, (state(connector, Quantity.CONNECTOR_STATE, State.AVAILABLE, 1),)
    )
    snapshot = runtime.get(token.station)
    assert (
        snapshot.observation(Channel(connector, Quantity.CHARGING_STATE)).value
        == State.IDLE
    )
    assert (
        snapshot.observation(Channel(evse, Quantity.CHARGING_STATE)).value
        == State.CHARGING
    )
    runtime.observe(
        token, (state(connector, Quantity.CONNECTOR_STATE, State.FAULTED, 2),)
    )
    assert (
        runtime.get(token.station)
        .observation(Channel(connector, Quantity.CHARGING_STATE))
        .value
        == State.UNKNOWN
    )
    sibling = ConnectorId(evse, "2")
    runtime.observe(
        token, (state(sibling, Quantity.CONNECTOR_STATE, State.AVAILABLE, 3),)
    )
    assert (
        runtime.get(token.station).observation(
            Channel(sibling, Quantity.CHARGING_STATE)
        )
        is None
    )
