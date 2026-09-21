"""Genuine versioned OCPP peers validate transaction requests and responses."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from test_ocpp_bidirectional import paired
from test_ocpp_transport import server as server
from websockets.asyncio.client import connect

from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import Channel, Quantity, State


def groups(at, energy, power=0, context="Sample.Periodic"):
    return [
        {
            "timestamp": at.isoformat(),
            "sampled_value": [
                {
                    "measurand": "Energy.Active.Import.Register",
                    "value": energy,
                    "context": context,
                    "unit_of_measure": {"unit": "Wh"},
                },
                {
                    "measurand": "Power.Active.Import",
                    "value": power,
                    "unit_of_measure": {"unit": "W"},
                },
            ],
        }
    ]


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("connector", [None, 7])
async def test_transaction_lifecycle_schemas_ordering_and_boundary_meters(
    protocol, connector
):
    async with paired(protocol) as (server, station):
        await station.boot()
        root = StationId("station-a")
        scope = EvseId(root, "2")
        evse = {"id": 2}
        if connector is not None:
            scope = ConnectorId(scope, str(connector))
            evse["connector_id"] = connector
        at = datetime.now(UTC) - timedelta(seconds=10)

        async def send(
            kind,
            seconds,
            sequence,
            energy,
            power=0,
            charging="Charging",
            with_scope=True,
        ):
            time = at + timedelta(seconds=seconds)
            request = station._call.TransactionEvent(
                event_type=kind,
                timestamp=time.isoformat(),
                trigger_reason="MeterValuePeriodic"
                if kind == "Updated"
                else "CablePluggedIn"
                if kind == "Started"
                else "EVDeparted",
                seq_no=sequence,
                transaction_info={
                    "transaction_id": "station-transaction",
                    "charging_state": charging,
                    **({"stopped_reason": "EVDisconnected"} if kind == "Ended" else {}),
                },
                evse=evse if with_scope else None,
                meter_value=groups(
                    time,
                    energy,
                    power,
                    "Transaction.Begin"
                    if kind == "Started"
                    else "Transaction.End"
                    if kind == "Ended"
                    else "Sample.Periodic",
                ),
            )
            result = await station.call(request, suppress=False)
            assert isinstance(result, station._call_result.TransactionEvent)

        await send("Started", 0, 0, 1000)
        ledger = server.runtime.sessions
        first = ledger.get(scope)
        assert first.external_transaction_id == "station-transaction"
        assert first.scope == scope and first.energy_start_wh == 1000
        await send("Started", 0, 0, 1000)
        assert ledger.get(scope) == first
        await send("Updated", 2, 1, 1400, 7000)
        updated = ledger.get(scope)
        assert updated.session_id == first.session_id
        assert updated.energy_charged_wh == 400 and updated.max_power_w == 7000
        assert updated.charging_state == State.CHARGING
        await send("Updated", 2, 1, 1400, 7000)
        assert ledger.get(scope) == updated
        await send("Updated", 1, 0, 1100, 200, "Idle")
        assert ledger.get(scope) == updated
        await send("Updated", 3, 2, 1500, 0, "EVConnected", with_scope=False)
        assert ledger.get(scope).active
        assert ledger.get(scope).current_power_w == 0
        assert ledger.get(scope).charging_state == State.CONNECTED
        await send("Ended", 5, 3, 1800, 0, "Idle")
        final = ledger.get(scope)
        assert not final.active and final.current_power_w == 0
        assert final.energy_charged_wh == 800 and final.max_power_w == 7000
        assert final.end_reason == "EVDisconnected"
        assert final.duration(datetime.now(UTC)) == 5
        assert (
            final.connector_id is None
            if connector is None
            else final.connector_id.value == "7"
        )
        await send("Ended", 5, 3, 1800, 0, "Idle")
        await send("Updated", 4, 2, 1600, 100)
        assert ledger.get(scope) == final
        assert ledger.history() == (final,)
        assert server.runtime.get(root).connected
        # All embedded measurements remain internal session inputs.
        assert (
            server.runtime.get(root).observation(Channel(scope, Quantity.ENERGY))
            is None
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_evse_meter_matches_only_evse_session_and_offline_history(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        at = datetime.now(UTC) - timedelta(seconds=10)
        scope = EvseId(StationId("station-a"), "1")
        await station.call(
            station._call.TransactionEvent(
                event_type="Started",
                timestamp=at.isoformat(),
                trigger_reason="CablePluggedIn",
                seq_no=0,
                transaction_info={"transaction_id": "offline"},
                evse={"id": 1},
                offline=True,
                meter_value=groups(at, 2000),
            ),
            suppress=False,
        )
        ledger = server.runtime.sessions
        assert ledger.get(scope).energy_start_wh == 2000
        assert server.runtime.get(scope.station).observations == ()
        await station.call(
            station._call.MeterValues(
                evse_id=1, meter_value=groups(at + timedelta(seconds=2), 2400, 5000)
            ),
            suppress=False,
        )
        assert ledger.get(scope).energy_charged_wh == 400
        assert ledger.get(scope).current_power_w == 5000
        await station.boot()
        assert ledger.get(scope).active
        assert ledger.get(scope).energy_charged_wh == 400


class Station16(ChargePoint):
    @on("GetConfiguration")
    def configuration(self, **kwargs):
        return call_result.GetConfiguration(configuration_key=[])


async def test_16_real_peer_start_stop_reconnect_and_transaction_meter_filter(server):
    root = StationId("legacy")
    scope = ConnectorId(EvseId(root, "connector-2"), "2")
    at = datetime.now(UTC) - timedelta(seconds=10)
    ledger = server.runtime.sessions
    external = None
    for attempt in range(2):
        async with connect(
            f"ws://127.0.0.1:{server.port}/legacy", subprotocols=["ocpp1.6"], proxy=None
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
                assert isinstance(response, call_result.StartTransaction)
                assert response.id_tag_info["status"] == "Accepted"
                if external is None:
                    external = response.transaction_id
                    session_id = ledger.get(scope).session_id
                else:
                    assert response.transaction_id == external
                    assert ledger.get(scope).session_id == session_id
                assert ledger.get(scope).external_transaction_id == str(external)
                value_time = at + timedelta(seconds=2 + attempt)
                samples = [
                    {
                        "timestamp": value_time.isoformat(),
                        "sampled_value": [
                            {
                                "value": "1500",
                                "measurand": "Energy.Active.Import.Register",
                                "unit": "Wh",
                            },
                            {
                                "value": "7000",
                                "measurand": "Power.Active.Import",
                                "unit": "W",
                            },
                        ],
                    }
                ]
                await station.call(
                    call.MeterValues(
                        connector_id=2,
                        transaction_id=external + 100,
                        meter_value=samples,
                    ),
                    suppress=False,
                )
                assert ledger.get(scope).energy_charged_wh == (
                    0 if attempt == 0 else 500
                )
                await station.call(
                    call.MeterValues(
                        connector_id=2, transaction_id=external, meter_value=samples
                    ),
                    suppress=False,
                )
                assert ledger.get(scope).energy_charged_wh == 500
                assert ledger.get(scope).max_power_w == 7000
                if attempt:
                    response = await station.call(
                        call.StopTransaction(
                            transaction_id=external,
                            meter_stop=1800,
                            timestamp=(at + timedelta(seconds=5)).isoformat(),
                            reason="Local",
                        ),
                        suppress=False,
                    )
                    assert isinstance(response, call_result.StopTransaction)
                    assert ledger.get(scope).energy_charged_wh == 800
                    assert not ledger.get(scope).active
                    assert ledger.get(scope).end_reason == "Local"
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
        if not attempt:
            assert ledger.get(scope).active  # Disconnect is not StopTransaction.
    assert len(ledger.history()) == 1


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_optional_connector_and_conflicting_scope_do_not_duplicate(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        at = datetime.now(UTC) - timedelta(seconds=10)
        for index, evse in enumerate(
            ({"id": 2, "connector_id": 7}, {"id": 2}, {"id": 3, "connector_id": 8})
        ):
            await station.call(
                station._call.TransactionEvent(
                    event_type="Started" if index == 0 else "Updated",
                    seq_no=index,
                    timestamp=(at + timedelta(seconds=index)).isoformat(),
                    trigger_reason="ChargingStateChanged",
                    transaction_info={
                        "transaction_id": "stable",
                        "charging_state": "EVConnected",
                    },
                    evse=evse,
                ),
                suppress=False,
            )
        sessions = server.runtime.sessions.latest
        assert len(sessions) == 1
        assert sessions[0].connector_id.value == "7"
        assert sessions[0].evse_id.value == "2"
        assert sessions[0].updated_at == at + timedelta(seconds=1)
