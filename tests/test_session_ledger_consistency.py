"""Event-bound measurements must agree with the live session projection."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.v16 import call
from test_ha_observations import observation
from test_ocpp_bidirectional import paired
from test_ocpp_transaction_sessions import Station16, groups
from test_ocpp_transport import server as server
from test_session_meter_attribution import context as context
from test_session_meter_attribution import start
from websockets.asyncio.client import connect

from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.sessions import SessionEvent
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import Quantity
from custom_components.wallbox_manager.session_entity import SessionSensor


@pytest.mark.parametrize("excluded_value", [1800, None])
def test_later_embedded_sample_cannot_replace_accepted_power(context, excluded_value):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    sensor = SessionSensor(runtime, "test", scope, "power")
    accepted = observation(parent, Quantity.POWER, 1380, at + timedelta(seconds=10))
    runtime.observe(token, (accepted,))
    identity = runtime.sessions.get(scope).session_id
    published = []
    unsubscribe = runtime.sessions.subscribe(
        lambda: published.append(sensor.native_value)
    )
    try:
        assert runtime.session_event(
            token,
            SessionEvent(
                scope, "tx", Kind.UPDATED, at + timedelta(seconds=12), sequence=1
            ),
            (
                observation(
                    scope, Quantity.POWER, excluded_value, at + timedelta(seconds=12.4)
                ),
            ),
            live=True,
        )
    finally:
        unsubscribe()
    session = runtime.sessions.get(scope)
    selected = runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC))
    assert selected == accepted
    assert session.session_id == identity
    assert session.current_power_w == selected.value == 1380
    assert session.power_at == selected.observed_at
    assert session.power_parent
    assert sensor.power_status(datetime.now(UTC))[0] == "valid"
    assert (
        sensor.extra_state_attributes["observed_at"] == accepted.observed_at.isoformat()
    )
    assert published == [1380]

    newer = observation(parent, Quantity.POWER, 1800, at + timedelta(seconds=14))
    runtime.observe(token, (newer,))
    assert (
        runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC)) == newer
    )
    assert runtime.sessions.get(scope).power_at == newer.observed_at
    assert sensor.native_value == 1800


def test_excluded_start_and_end_samples_preserve_energy_boundaries(context):
    runtime, token, parent, scope, at = context
    start(
        runtime,
        token,
        scope,
        at,
        samples=(
            observation(scope, Quantity.ENERGY, 1000, at),
            observation(scope, Quantity.ENERGY, 9000, at + timedelta(seconds=0.4)),
            observation(scope, Quantity.POWER, 9000, at + timedelta(seconds=0.4)),
        ),
    )
    session = runtime.sessions.get(scope)
    assert session.energy_start_wh == session.energy_end_wh == 1000
    assert session.energy_charged_wh == 0
    assert session.current_power_w is None
    assert (
        runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC)) is None
    )
    assert (
        runtime.sessions.measurement(scope, Quantity.ENERGY, datetime.now(UTC)).value
        == 1000
    )
    runtime.observe(
        token,
        (
            observation(parent, Quantity.POWER, 1380, at + timedelta(seconds=2)),
            observation(parent, Quantity.ENERGY, 1200, at + timedelta(seconds=2)),
        ),
    )
    assert runtime.sessions.get(scope).energy_charged_wh == 200
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.ENDED, at + timedelta(seconds=3), sequence=1),
        (
            observation(scope, Quantity.ENERGY, 1300, at + timedelta(seconds=3)),
            observation(scope, Quantity.ENERGY, 9999, at + timedelta(seconds=3.4)),
            observation(scope, Quantity.POWER, 9999, at + timedelta(seconds=3.4)),
        ),
        live=True,
    )
    final = runtime.sessions.get(scope)
    assert final.energy_start_wh == 1000 and final.energy_end_wh == 1300
    assert final.energy_charged_wh == 300 and final.energy_at == final.ended_at
    assert final.max_power_w == 1380 and final.current_power_w == 0
    assert SessionSensor(runtime, "test", scope, "power").native_value == 0


def test_rejected_event_and_old_connection_cannot_cache_newer_samples(context):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    accepted = observation(parent, Quantity.POWER, 1380, at + timedelta(seconds=10))
    runtime.observe(token, (accepted,))
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, at + timedelta(seconds=12), sequence=2),
        live=True,
    )
    assert not runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, at + timedelta(seconds=11), sequence=1),
        (observation(scope, Quantity.POWER, 9000, at + timedelta(seconds=14)),),
        live=True,
    )
    assert (
        runtime.sessions.measurement(scope, Quantity.POWER, datetime.now(UTC))
        == accepted
    )
    runtime.disconnect(token)
    current = runtime.connect(scope.station)
    assert not runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, at + timedelta(seconds=15), sequence=3),
        (observation(scope, Quantity.POWER, 9000, at + timedelta(seconds=15)),),
        live=True,
    )
    sensor = SessionSensor(runtime, "test", scope, "power")
    assert sensor.native_value is None
    runtime.observe(
        current,
        (observation(parent, Quantity.POWER, 1800, at + timedelta(seconds=16)),),
    )
    assert sensor.native_value == 1800


@pytest.mark.parametrize("value", [1380, 1400, None])
def test_same_time_embedded_values_preserve_conflict_detection(context, value):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    sample_at = at + timedelta(seconds=10)
    runtime.observe(token, (observation(parent, Quantity.POWER, 1380, sample_at),))
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, sample_at, sequence=1),
        (observation(scope, Quantity.POWER, value, sample_at),),
        live=True,
    )
    sensor = SessionSensor(runtime, "test", scope, "power")
    assert sensor.native_value == (1380 if value == 1380 else None)
    runtime.observe(
        token,
        (observation(parent, Quantity.POWER, 1800, sample_at + timedelta(seconds=1)),),
    )
    assert sensor.native_value == 1800


def test_foreign_event_sample_never_enters_another_scope_cache(context):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 1380, at + timedelta(seconds=10)),)
    )
    other = ConnectorId(EvseId(scope.station, "2"), "1")
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, at + timedelta(seconds=12), sequence=1),
        (observation(other, Quantity.POWER, 9000, at + timedelta(seconds=12)),),
        live=True,
    )
    assert (
        runtime.sessions.measurement(other, Quantity.POWER, datetime.now(UTC)) is None
    )
    assert SessionSensor(runtime, "test", scope, "power").native_value == 1380


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("sample_seconds", [12.4, 90])
async def test_wire_transaction_sample_after_event_retains_power_until_normal_meter(
    protocol,
    sample_seconds,
):
    async with paired(protocol) as (server, station):
        await station.boot()
        scope = ConnectorId(EvseId(StationId("station-a"), "1"), "1")
        at = datetime.now(UTC) - timedelta(seconds=30)
        await station.call(
            station._call.TransactionEvent(
                event_type="Started",
                timestamp=at.isoformat(),
                trigger_reason="CablePluggedIn",
                seq_no=0,
                transaction_info={"transaction_id": "tx"},
                evse={"id": 1, "connector_id": 1},
                meter_value=groups(at, 1000),
            ),
            suppress=False,
        )
        await station.call(
            station._call.MeterValues(
                evse_id=1,
                meter_value=groups(at + timedelta(seconds=10), 1200, 1380),
            ),
            suppress=False,
        )
        sensor = SessionSensor(server.runtime, "test", scope, "power")
        assert sensor.native_value == 1380
        await station.call(
            station._call.TransactionEvent(
                event_type="Updated",
                timestamp=(at + timedelta(seconds=12)).isoformat(),
                trigger_reason="ChargingStateChanged",
                seq_no=1,
                transaction_info={"transaction_id": "tx", "charging_state": "Charging"},
                evse={"id": 1, "connector_id": 1},
                meter_value=groups(at + timedelta(seconds=sample_seconds), 1400, 1800),
            ),
            suppress=False,
        )
        assert sensor.native_value == 1380
        assert sensor.extra_state_attributes["power_availability_reason"] == "valid"
        assert server.runtime.sessions.get(scope).energy_end_wh == 1200
        await station.call(
            station._call.MeterValues(
                evse_id=1,
                meter_value=groups(at + timedelta(seconds=14), 1500, 1800),
            ),
            suppress=False,
        )
        assert sensor.native_value == 1800
        assert server.runtime.sessions.get(scope).energy_charged_wh == 500


async def test_legacy_wire_meter_order_conflicts_and_boundary_accounting(server):
    scope = ConnectorId(EvseId(StationId("legacy"), "connector-2"), "2")
    at = datetime.now(UTC) - timedelta(seconds=30)
    async with connect(
        f"ws://127.0.0.1:{server.port}/legacy",
        subprotocols=["ocpp1.6"],
        proxy=None,
    ) as ws:
        station = Station16("legacy", ws, response_timeout=1)
        reader = asyncio.create_task(station.start())
        try:
            await station.call(
                call.BootNotification(
                    charge_point_vendor="Test", charge_point_model="Peer"
                ),
                suppress=False,
            )
            response = await station.call(
                call.StartTransaction(
                    connector_id=2,
                    id_tag="local",
                    meter_start=1000,
                    timestamp=at.isoformat(),
                ),
                suppress=False,
            )
            sensor = SessionSensor(server.runtime, "test", scope, "power")

            async def send(seconds, watts):
                await station.call(
                    call.MeterValues(
                        connector_id=2,
                        transaction_id=response.transaction_id,
                        meter_value=[
                            {
                                "timestamp": (
                                    at + timedelta(seconds=seconds)
                                ).isoformat(),
                                "sampled_value": [
                                    {
                                        "value": str(watts),
                                        "measurand": "Power.Active.Import",
                                        "unit": "W",
                                    }
                                ],
                            }
                        ],
                    ),
                    suppress=False,
                )

            await send(10, 1380)
            await send(9, 9000)
            await send(10, 1380)
            assert sensor.native_value == 1380
            await send(10, 1400)
            assert sensor.native_value is None
            await send(14, 1800)
            assert sensor.native_value == 1800
            selected = server.runtime.sessions.measurement(
                scope, Quantity.POWER, datetime.now(UTC)
            )
            assert selected.observed_at == server.runtime.sessions.get(scope).power_at
            assert selected.source == "ocpp1.6:MeterValues"
            await station.call(
                call.StopTransaction(
                    transaction_id=response.transaction_id,
                    meter_stop=1600,
                    timestamp=(at + timedelta(seconds=20)).isoformat(),
                    reason="Local",
                ),
                suppress=False,
            )
            assert sensor.native_value == 0
            assert server.runtime.sessions.get(scope).energy_charged_wh == 600
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
