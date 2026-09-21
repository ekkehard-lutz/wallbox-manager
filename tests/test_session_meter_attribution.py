"""Session-only EVSE attribution, freshness, ambiguity and entity publication."""

from datetime import UTC, datetime, timedelta

import pytest
import test_ocpp_bidirectional as peers
from test_ha_diagnostics import diagnostics as diagnostics
from test_ha_observations import observation, operational
from test_ocpp_transaction_sessions import groups

from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.sessions import SessionEvent
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import Channel, Quantity
from custom_components.wallbox_manager.runtime import Runtime
from custom_components.wallbox_manager.session_entity import SessionEntity


@pytest.fixture
def context():
    runtime = Runtime()
    parent = EvseId(StationId("station-a"), "1")
    scope = ConnectorId(parent, "1")
    token = runtime.connect(parent.station)
    at = datetime.now(UTC) - timedelta(seconds=30)
    return runtime, token, parent, scope, at


def start(runtime, token, scope, at, external="tx", samples=()):
    return runtime.session_event(
        token, SessionEvent(scope, external, Kind.STARTED, at), samples, live=True
    )


def test_parent_baseline_running_and_final_endpoint(context):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    session = runtime.sessions.get(scope)
    assert session.energy_start_wh == 1000 and session.energy_charged_wh == 0
    runtime.observe(
        token,
        (
            observation(parent, Quantity.ENERGY, 1600, at + timedelta(seconds=2)),
            observation(parent, Quantity.POWER, 7000, at + timedelta(seconds=2)),
        ),
    )
    session = runtime.sessions.get(scope)
    assert session.energy_end_wh == 1600 and session.energy_charged_wh == 600
    assert session.current_power_w == 7000 and session.max_power_w == 7000
    assert (
        runtime.get(token.station).observation(Channel(scope, Quantity.POWER)) is None
    )
    runtime.session_event(
        token, SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3))
    )
    final = runtime.sessions.get(scope)
    assert final.energy_end_wh == 1600 and final.energy_charged_wh == 600
    assert final.current_power_w == 0 and not final.active


def test_explicit_endpoint_wins_over_parent(context):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(
        runtime,
        token,
        scope,
        at,
        samples=(observation(scope, Quantity.ENERGY, 1100, at),),
    )
    assert runtime.sessions.get(scope).energy_start_wh == 1100
    runtime.observe(
        token, (observation(parent, Quantity.ENERGY, 1500, at + timedelta(seconds=2)),)
    )
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3)),
        (observation(scope, Quantity.ENERGY, 1800, at + timedelta(seconds=3)),),
        live=True,
    )
    assert runtime.sessions.get(scope).energy_charged_wh == 700
    assert runtime.sessions.get(scope).energy_end_wh == 1800


def test_exact_normal_connector_meter_precedes_parent(context):
    runtime, token, parent, scope, at = context
    start(
        runtime,
        token,
        scope,
        at,
        samples=(observation(scope, Quantity.ENERGY, 1000, at),),
    )
    runtime.observe(
        token,
        (
            observation(scope, Quantity.POWER, 500, at + timedelta(seconds=1)),
            observation(scope, Quantity.ENERGY, 1200, at + timedelta(seconds=1)),
        ),
    )
    runtime.observe(
        token,
        (
            observation(parent, Quantity.POWER, 9000, at + timedelta(seconds=2)),
            observation(parent, Quantity.ENERGY, 9000, at + timedelta(seconds=2)),
        ),
    )
    session = runtime.sessions.get(scope)
    assert session.current_power_w == 500
    assert session.energy_charged_wh == 200
    assert (
        Channel(scope, Quantity.POWER) in runtime.get(token.station).supported_channels
    )


def test_event_sample_wins_tie_but_does_not_freeze_newer_periodic_stream(context):
    runtime, token, parent, scope, at = context
    start(
        runtime,
        token,
        scope,
        at,
        samples=(observation(scope, Quantity.POWER, 500, at),),
    )
    runtime.observe(token, (observation(parent, Quantity.POWER, 9000, at),))
    assert runtime.sessions.get(scope).current_power_w == 500
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 7000, at + timedelta(seconds=1)),)
    )
    assert runtime.sessions.get(scope).current_power_w == 7000


@pytest.mark.parametrize("second_scope", ["connector", "evse"])
def test_ambiguous_evse_invalidates_parent_values(context, second_scope):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 7000, at + timedelta(seconds=2)),)
    )
    other = ConnectorId(parent, "2") if second_scope == "connector" else parent
    start(runtime, token, other, at + timedelta(seconds=3), "other")
    runtime.observe(
        token,
        (
            observation(parent, Quantity.POWER, 8000, at + timedelta(seconds=4)),
            observation(parent, Quantity.ENERGY, 1800, at + timedelta(seconds=4)),
        ),
    )
    session = runtime.sessions.get(scope)
    assert session.current_power_w is None and session.energy_charged_wh is None
    assert session.energy_end_wh is None
    assert (
        runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC)) is None
    )
    # Explicit connector metering remains usable even during overlap.
    runtime.observe(
        token, (observation(scope, Quantity.POWER, 500, at + timedelta(seconds=5)),)
    )
    assert runtime.sessions.get(scope).current_power_w == 500
    runtime.session_event(
        token, SessionEvent(other, "other", Kind.ENDED, at + timedelta(seconds=6))
    )
    runtime.observe(
        token, (observation(parent, Quantity.ENERGY, 2000, at + timedelta(seconds=7)),)
    )
    assert runtime.sessions.get(scope).energy_charged_wh is None


@pytest.mark.parametrize(
    "foreign",
    [EvseId(StationId("station-a"), "2"), EvseId(StationId("elsewhere"), "1")],
)
def test_no_cross_evse_or_station(context, foreign):
    runtime, token, parent, scope, at = context
    other_token = (
        token if foreign.station == token.station else runtime.connect(foreign.station)
    )
    runtime.observe(other_token, (observation(foreign, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    runtime.observe(
        other_token,
        (observation(foreign, Quantity.POWER, 5000, at + timedelta(seconds=2)),),
    )
    session = runtime.sessions.get(scope)
    assert session.energy_start_wh is None and session.current_power_w is None
    assert (
        runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC)) is None
    )


@pytest.mark.parametrize("quantity", [Quantity.ENERGY, Quantity.POWER])
def test_stale_and_future_parent_not_attributed(context, quantity):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, quantity, 1000, at, lifetime=1),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    assert runtime.sessions.get(scope).energy_start_wh is None
    assert runtime.sessions.get(scope).current_power_w is None
    runtime.observe(
        token,
        (observation(parent, quantity, 2000, at + timedelta(seconds=2), lifetime=1),),
    )
    assert runtime.sessions.get(scope).energy_end_wh is None
    runtime.session_event(
        token, SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3))
    )
    assert runtime.sessions.get(scope).energy_end_wh is None


def test_reset_and_restored_session_fencing(context):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    identifier = runtime.sessions.get(scope).session_id
    saved = runtime.sessions.dump()
    # Backward-compatible optional provenance fields for beta.5 documents.
    old = runtime.sessions.dump()
    for row in old["records"]:
        row.pop("energy_parent")
        row.pop("power_parent")
    Runtime().sessions.restore(old)
    restored = Runtime()
    restored.sessions.restore(saved)
    current = restored.connect(parent.station)
    assert not restored.session_event(
        token, SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=2))
    )
    assert (
        restored.sessions.measurement(scope, Quantity.ENERGY, datetime.now(UTC)) is None
    )
    restored.observe(
        current,
        (observation(parent, Quantity.ENERGY, 1500, at + timedelta(seconds=2)),),
    )
    assert restored.sessions.get(scope).session_id == identifier
    assert restored.sessions.get(scope).energy_charged_wh == 500
    restored.observe(
        current, (observation(parent, Quantity.ENERGY, 100, at + timedelta(seconds=3)),)
    )
    assert restored.sessions.get(scope).energy_charged_wh is None
    restored.disconnect(current)
    assert restored.sessions.get(scope).active
    assert (
        restored.sessions.measurement(scope, Quantity.ENERGY, datetime.now(UTC)) is None
    )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_wire_messages_publish_only_normal_meters_and_ten_session_entities(
    diagnostics, monkeypatch, protocol
):
    hass, _, runtime, platforms, _, _ = diagnostics
    monkeypatch.setattr(peers, "Runtime", lambda: runtime)
    async with peers.paired(protocol) as (_, station):
        await station.boot()
        at = datetime.now(UTC) - timedelta(seconds=20)
        parent = EvseId(StationId("station-a"), "1")
        scope = ConnectorId(parent, "1")
        await station.call(
            station._call.MeterValues(evse_id=1, meter_value=groups(at, 1000)),
            suppress=False,
        )
        await station.call(
            station._call.TransactionEvent(
                event_type="Started",
                seq_no=0,
                timestamp=(at + timedelta(seconds=1)).isoformat(),
                trigger_reason="CablePluggedIn",
                transaction_info={
                    "transaction_id": "hardware",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
                meter_value=groups(at + timedelta(seconds=1), 1100, 100),
            ),
            suppress=False,
        )
        await hass.async_block_till_done()
        entities = {
            e.key: e
            for p in platforms
            for e in p.entities.values()
            if isinstance(e, SessionEntity)
        }
        assert len(entities) == 10
        assert entities["power"].native_value == 100
        assert entities["start_meter"].native_value == 1.1
        assert all(
            e.channel.scope == parent or e.channel.quantity == Quantity.CHARGING_STATE
            for e in operational(platforms)
        )
        assert not any(
            c.scope == scope and c.quantity in (Quantity.POWER, Quantity.ENERGY)
            for c in runtime.get(parent.station).supported_channels
        )
        await station.call(
            station._call.MeterValues(
                evse_id=1, meter_value=groups(at + timedelta(seconds=2), 1600, 7000)
            ),
            suppress=False,
        )
        await hass.async_block_till_done()
        assert entities["power"].native_value == 7000
        assert entities["energy"].native_value == 0.5
        normal = [e for e in operational(platforms) if e.channel.scope == parent]
        assert len(normal) == 2
        assert {e.channel.quantity for e in normal} == {Quantity.POWER, Quantity.ENERGY}
        await station.call(
            station._call.TransactionEvent(
                event_type="Ended",
                seq_no=1,
                timestamp=(at + timedelta(seconds=3)).isoformat(),
                trigger_reason="EVDeparted",
                transaction_info={"transaction_id": "hardware"},
                evse={"id": 1, "connector_id": 1},
            ),
            suppress=False,
        )
        assert entities["end_meter"].native_value == 1.6
        assert entities["energy"].native_value == 0.5
        assert entities["power"].native_value == 0
        assert not entities["active"].is_on


def test_future_meter_cannot_seed_older_start_and_no_phase_sum(context):
    runtime, token, parent, scope, at = context
    runtime.observe(
        token, (observation(parent, Quantity.ENERGY, 1000, at + timedelta(seconds=5)),)
    )
    start(runtime, token, scope, at)
    assert runtime.sessions.get(scope).energy_start_wh is None
    runtime.observe(
        token,
        tuple(
            observation(parent, q, 1000, at + timedelta(seconds=1))
            for q in (Quantity.POWER_L1, Quantity.POWER_L2, Quantity.POWER_L3)
        ),
    )
    assert runtime.sessions.get(scope).current_power_w is None


def test_invalid_embedded_end_uses_valid_parent_endpoint(context):
    runtime, token, parent, scope, at = context
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    runtime.observe(
        token, (observation(parent, Quantity.ENERGY, 1800, at + timedelta(seconds=2)),)
    )
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3)),
        (observation(scope, Quantity.ENERGY, None, at + timedelta(seconds=3)),),
    )
    assert runtime.sessions.get(scope).energy_end_wh == 1800
    assert runtime.sessions.get(scope).energy_charged_wh == 800


def test_old_event_baseline_is_not_reused_as_missing_end(context):
    runtime, token, _, scope, at = context
    start(
        runtime,
        token,
        scope,
        at,
        samples=(observation(scope, Quantity.ENERGY, 1000, at),),
    )
    runtime.session_event(
        token, SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3))
    )
    assert runtime.sessions.get(scope).energy_end_wh is None
    assert runtime.sessions.get(scope).energy_charged_wh is None


async def test_ha_fallback_power_expires_and_completed_snapshots_remain(
    diagnostics, monkeypatch
):
    import custom_components.wallbox_manager.session_entity as entities_module

    hass, _, runtime, platforms, _, _ = diagnostics
    at = datetime.now(UTC) - timedelta(seconds=10)
    parent = EvseId(StationId("garage"), "1")
    scope = ConnectorId(parent, "1")
    token = runtime.connect(parent.station)
    runtime.observe(token, (observation(parent, Quantity.ENERGY, 1000, at),))
    start(runtime, token, scope, at + timedelta(seconds=1))
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 5000, at + timedelta(seconds=2)),)
    )
    await hass.async_block_till_done()
    entities = {
        e.key: e
        for p in platforms
        for e in p.entities.values()
        if isinstance(e, SessionEntity)
    }
    assert entities["power"].native_value == 5000

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=130)

    monkeypatch.setattr(entities_module, "datetime", Later)
    assert entities["power"].native_value is None
    runtime.session_event(
        token, SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3))
    )
    assert entities["power"].native_value == 0
    assert entities["start_meter"].native_value == 1
    assert entities["end_meter"].native_value == 1
