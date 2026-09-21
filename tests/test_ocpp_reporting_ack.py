"""ACK-only interoperability using genuine versioned OCPP library peers."""

import asyncio

import pytest
from test_ocpp_bidirectional import paired
from test_ocpp_transport import inventory, state_when

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.core.models import StationId

STAMP = "2026-09-21T00:00:00Z"


def meter_groups():
    # Shape emitted by wallbox-stationary's build_meter_values, including units.
    return [
        {
            "timestamp": STAMP,
            "sampled_value": [
                {
                    "value": 231.5,
                    "measurand": "Voltage",
                    "phase": "L1-N",
                    "context": "Sample.Periodic",
                    "location": "Outlet",
                    "unit_of_measure": {"unit": "V"},
                }
            ],
        }
    ]


def reporting_call(station, kind):
    if kind == "MeterValues":
        return station._call.MeterValues(evse_id=1, meter_value=meter_groups())
    if kind == "NotifyEvent":
        return station._call.NotifyEvent(
            generated_at=STAMP,
            seq_no=0,
            tbc=False,
            event_data=[
                {
                    "event_id": 0,
                    "timestamp": STAMP,
                    "component": {"name": "WallboxController"},
                    "variable": {"name": "ControlAuthority"},
                    "actual_value": "Local",
                    "event_notification_type": "HardWiredNotification",
                    "trigger": "Delta",
                }
            ],
        )
    event = "Started" if kind == "WithToken" else kind
    data = dict(
        event_type=event,
        timestamp=STAMP,
        trigger_reason={
            "Started": "CablePluggedIn",
            "Updated": "ChargingStateChanged",
            "Ended": "EVDeparted",
        }[event],
        seq_no={"Started": 0, "Updated": 1, "Ended": 2}[event],
        transaction_info={
            "transaction_id": "station-transaction",
            "charging_state": "Idle" if event == "Ended" else "EVConnected",
        },
        evse={"id": 1, "connector_id": 1},
        meter_value=meter_groups(),
    )
    if event == "Ended":
        data["transaction_info"]["stopped_reason"] = "EVDisconnected"
    if kind == "WithToken":
        data["id_token"] = {"id_token": "unverified-token", "type": "Local"}
    return station._call.TransactionEvent(**data)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize(
    "kind", ["MeterValues", "Started", "Updated", "Ended", "WithToken", "NotifyEvent"]
)
async def test_reporting_ack_during_discovery(protocol, kind):
    async with paired(protocol) as (server, station):
        station.emit_report = False
        await station.boot()
        await asyncio.wait_for(station.first_request.wait(), 1)
        adapter = server.sessions[StationId("station-a")].adapter
        before = server.runtime.get(adapter.token.station)
        assert not adapter.discovery_task.done()
        response = await station.call(reporting_call(station, kind), suppress=False)
        expected_type = getattr(
            station._call_result,
            "TransactionEvent" if kind not in ("MeterValues", "NotifyEvent") else kind,
        )
        assert type(response) is expected_type
        if kind == "WithToken":
            assert response.id_token_info == {"status": "Unknown"}
        else:
            assert all(value is None for value in vars(response).values())
        # No measurements, transactions, authority, identity or evidence published.
        assert server.runtime.get(adapter.token.station) == before
        assert not adapter.discovery_task.done()
        await station.call(
            station._call.NotifyReport(
                request_id=station.requests[0],
                generated_at=STAMP,
                seq_no=0,
                tbc=False,
                report_data=inventory(),
            ),
            suppress=False,
        )
        complete = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        await asyncio.sleep(server.response_timeout + 0.05)
        await station.call(station._call.Heartbeat(), suppress=False)
        assert server.runtime.get(adapter.token.station) == complete
        assert complete.connected
        assert (
            complete.token.connection_generation == complete.token.boot_generation == 1
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize("kind", ["MeterValues", "Started"])
async def test_invalid_reporting_call_is_not_acknowledged(protocol, kind):
    from ocpp.exceptions import OCPPError

    async with paired(protocol) as (server, station):
        await station.boot()
        await asyncio.wait_for(station.acked.wait(), 1)
        before = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        payload = reporting_call(station, kind)
        if kind == "MeterValues":
            payload.evse_id = "not-an-integer"
        else:
            payload.event_type = "not-an-event"
        # Bypass only the test sender's validation: CSMS validation must remain on.
        with pytest.raises(OCPPError):
            await station.call(payload, suppress=False, skip_schema_validation=True)
        assert server.runtime.get(before.token.station) == before
        await station.call(station._call.Heartbeat(), suppress=False)
