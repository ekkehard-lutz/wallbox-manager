"""Versioned wire schemas -> scoped, generation-fenced runtime observations."""

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta

import pytest
from test_ocpp_bidirectional import paired
from test_ocpp_reporting_ack import reporting_call
from test_ocpp_transport import peer, state_when
from test_ocpp_transport import server as server

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import Channel, Quantity, State


def assert_generic(value):
    assert not type(value).__module__.startswith(
        ("ocpp", "homeassistant", "websockets")
    )
    if is_dataclass(value):
        for field in fields(value):
            assert_generic(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            assert_generic(item)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_meter_scope_units_and_old_generation(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        now = datetime.now(UTC)

        def groups(value, at=now):
            return [
                {
                    "timestamp": at.isoformat(),
                    "sampled_value": [
                        {
                            "measurand": "Power.Active.Import",
                            "value": value,
                            "unit_of_measure": {"unit": "kW", "multiplier": -1},
                        }
                    ],
                }
            ]

        for evse_id, value in [(0, 100), (1, 30), (2, 70)]:
            result = await station.call(
                station._call.MeterValues(evse_id=evse_id, meter_value=groups(value)),
                suppress=False,
            )
            assert isinstance(result, station._call_result.MeterValues)
        root = StationId("station-a")
        snapshot = server.runtime.get(root)
        assert snapshot.observation(Channel(root, Quantity.POWER)).value == 10000
        assert (
            snapshot.observation(Channel(EvseId(root, "1"), Quantity.POWER)).value
            == 3000
        )
        assert (
            snapshot.observation(Channel(EvseId(root, "2"), Quantity.POWER)).value
            == 7000
        )
        assert_generic(snapshot)
        await station.call(
            station._call.MeterValues(
                evse_id=1, meter_value=groups(999, now - timedelta(seconds=1))
            ),
            suppress=False,
        )
        assert server.runtime.get(root) == snapshot
        adapter = server.sessions[root].adapter
        old_token = adapter.token
        await station.boot()
        assert not server.runtime.observe(old_token, snapshot.observations)
        assert server.runtime.get(root).observations == ()
        assert set(server.runtime.get(root).supported_channels) == set(
            snapshot.supported_channels
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_occupied_is_not_charging_and_transaction_scope(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        root = StationId("station-a")
        scope = ConnectorId(EvseId(root, "1"), "1")
        at = datetime.now(UTC)
        await station.call(
            station._call.StatusNotification(
                timestamp=at.isoformat(),
                connector_status="Occupied",
                evse_id=1,
                connector_id=1,
            ),
            suppress=False,
        )
        snapshot = server.runtime.get(root)
        assert (
            snapshot.observation(Channel(scope, Quantity.CONNECTOR_STATE)).value
            == State.OCCUPIED
        )
        assert snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)) is None
        event = reporting_call(station, "Updated")
        event.timestamp = at.isoformat()
        event.meter_value[0]["timestamp"] = at.isoformat()
        event.transaction_info["charging_state"] = "Charging"
        await station.call(event, suppress=False)
        snapshot = server.runtime.get(root)
        assert (
            snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)).value
            == State.CHARGING
        )
        assert snapshot.observation(Channel(scope, Quantity.VOLTAGE_L1)).value == 231.5
        assert (
            snapshot.observation(Channel(EvseId(root, "1"), Quantity.VOLTAGE_L1))
            is None
        )
        # Offline replay must not masquerade as current runtime state.
        event.offline = True
        event.timestamp = (at + timedelta(seconds=1)).isoformat()
        event.transaction_info["charging_state"] = "Idle"
        await station.call(event, suppress=False)
        assert server.runtime.get(root) == snapshot
        # Older online state also cannot roll back the live channel.
        event.offline = False
        event.timestamp = (at - timedelta(seconds=1)).isoformat()
        await station.call(event, suppress=False)
        assert server.runtime.get(root) == snapshot
        # EVSE-only event remains distinct; no connector 1 assumption.
        event.evse = {"id": 2}
        event.timestamp = at.isoformat()
        await station.call(event, suppress=False)
        assert (
            server.runtime.get(root)
            .observation(Channel(EvseId(root, "2"), Quantity.CHARGING_STATE))
            .value
            == State.IDLE
        )
        assert_generic(server.runtime.get(root))


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("Available", State.AVAILABLE),
        ("Occupied", State.OCCUPIED),
        ("Reserved", State.RESERVED),
        ("Unavailable", State.UNAVAILABLE),
        ("Faulted", State.FAULTED),
    ],
)
async def test_2x_status_mapping(protocol, status, expected):
    async with paired(protocol) as (server, station):
        await station.boot()
        await station.call(
            station._call.StatusNotification(
                timestamp=datetime.now(UTC).isoformat(),
                connector_status=status,
                evse_id=4,
                connector_id=7,
            ),
            suppress=False,
        )
        scope = ConnectorId(EvseId(StationId("station-a"), "4"), "7")
        assert (
            server.runtime.get(scope.evse.station)
            .observation(Channel(scope, Quantity.CONNECTOR_STATE))
            .value
            == expected
        )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Available", State.IDLE),
        ("Preparing", State.PREPARING),
        ("Charging", State.CHARGING),
        ("SuspendedEV", State.SUSPENDED_VEHICLE),
        ("SuspendedEVSE", State.SUSPENDED_STATION),
        ("Finishing", State.FINISHING),
        ("Faulted", State.UNKNOWN),
    ],
)
async def test_16_status_and_meter_values(server, status, expected):
    async with peer(server, "ocpp1.6") as station:
        await station.boot()
        await station.call(
            "StatusNotification",
            {"connectorId": 2, "errorCode": "NoError", "status": status},
        )
        scope = ConnectorId(EvseId(StationId("station-a"), "connector-2"), "2")
        snapshot = server.runtime.get(scope.evse.station)
        assert (
            snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)).value
            == expected
        )
        at = datetime.now(UTC).isoformat()
        for connector_id in (0, 2):
            response = await station.call(
                "MeterValues",
                {
                    "connectorId": connector_id,
                    "meterValue": [
                        {
                            "timestamp": at,
                            "sampledValue": [
                                {
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "kWh",
                                    "value": "2.5",
                                }
                            ],
                        }
                    ],
                },
            )
            assert response[0] == 3
        snapshot = server.runtime.get(scope.evse.station)
        assert snapshot.observation(Channel(scope, Quantity.ENERGY)).value == 2500
        assert (
            snapshot.observation(Channel(scope.evse.station, Quantity.ENERGY)).value
            == 2500
        )
