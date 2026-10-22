"""Explicit HA wake-up entity, exact OCPP frames, outcomes and generation fences."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from conftest import config, entities, eventually
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from ocpp.routing import on
from ocpp.v21 import call, call_result
from test_active_control import Controllable, event, observed, ready
from test_protocol import peer

from custom_components.wallbox_manager.control import (
    DISPATCH,
    WAKEUP_MESSAGE,
    WAKEUP_TIMEOUT,
    WAKEUP_VENDOR,
)
from custom_components.wallbox_manager.ocpp import Adapter


class WakeupStation(Controllable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.values["ControlAuthority"] = "OCPP"
        self.wake_requests = []
        self.wake_status = "Accepted"
        self.wake_data = {"result": "completed", "cp_restored": True}

    @on("DataTransfer")
    async def wake(self, vendor_id, message_id, data):
        self.wake_requests.append((vendor_id, message_id, data))
        self.started.set()
        if self.release:
            await self.release.wait()
        return call_result.DataTransfer(status=self.wake_status, data=self.wake_data)


def button(ha, entry):
    return next(e for e in entities(ha, entry) if e.role == "wake_up_vehicle")


async def test_button_registration_identity_and_no_optimistic_vehicle_state(ha):
    entry = await ha.install()
    wake = button(ha, entry)
    assert wake.unique_id == f"{entry.entry_id}:Wallbox01:wake_up_vehicle"
    assert wake.translation_key == "wake_up_vehicle"
    assert not wake.available
    assert wake.should_poll is False
    assert wake.entity_category is None
    registry = er.async_get(ha).async_get(wake.entity_id)
    assert registry.config_entry_id == entry.entry_id
    assert registry.device_id
    assert registry.hidden_by is None and registry.disabled_by is None
    takeover = next(e for e in entities(ha, entry) if e.role == "take_control")
    assert (
        er.async_get(ha).async_get(takeover.entity_id).device_id == registry.device_id
    )
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        assert wake.available
        state = entry.runtime_data.state
        before = dict(state.observations), state.session
        await wake.async_press()
        assert station.wake_requests == [
            (WAKEUP_VENDOR, WAKEUP_MESSAGE, {"evse_id": 1})
        ]
        assert not station.writes
        assert (dict(state.observations), state.session) == before
        assert observed(entry, "ChargingEnabled") is True
        assert wake.extra_state_attributes["command_status"] == "confirmed"
        assert wake.extra_state_attributes["command_reason"] == "cp_restored"
        # A later explicit press is a new operation, never skipped as already observed.
        await wake.async_press()
        assert len(station.wake_requests) == 2
        assert control.intents["WakeUpVehicle"].dispatched_at is not None


@pytest.mark.parametrize(
    "status,data,expected,reason",
    [
        ("Rejected", {"result": "busy"}, "rejected", "busy"),
        ("Rejected", {"result": "unsafe_pause"}, "rejected", "unsafe_pause"),
        ("Rejected", {"result": "unsafe_evidence"}, "rejected", "unsafe_evidence"),
        ("Rejected", {"result": "no_authority"}, "rejected", "no_authority"),
        ("Rejected", {"result": "hardware_failure"}, "rejected", "hardware_failure"),
        (
            "Rejected",
            {"result": "wakeup_failed", "cp_restored": True},
            "rejected",
            "wakeup_failed",
        ),
        (
            "Rejected",
            {"result": "restoration_unknown", "cp_restored": False},
            "rejected",
            "restoration_unknown",
        ),
        (
            "UnknownVendorId",
            {"result": "unsupported_vendor"},
            "unsupported",
            "unsupported_vendor",
        ),
        (
            "UnknownMessageId",
            {"result": "unsupported_message"},
            "unsupported",
            "unsupported_message",
        ),
        ("Accepted", None, "communication_failure", "mismatched_response"),
        (
            "Accepted",
            {"result": "completed", "cp_restored": False},
            "communication_failure",
            "mismatched_response",
        ),
        (
            "Accepted",
            {"result": "completed", "cp_restored": 1},
            "communication_failure",
            "mismatched_response",
        ),
        (
            "Rejected",
            {"result": "arbitrary backend exception"},
            "communication_failure",
            "mismatched_response",
        ),
        (
            "Rejected",
            {"result": "restoration_unknown", "cp_restored": True},
            "communication_failure",
            "mismatched_response",
        ),
    ],
)
async def test_bounded_outcomes_are_exposed_without_claiming_vehicle_awake(
    ha, status, data, expected, reason
):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        station.wake_status, station.wake_data = status, data
        wake = button(ha, entry)
        with pytest.raises(HomeAssistantError):
            await wake.async_press()
        assert control.attributes("WakeUpVehicle")["command_status"] == expected
        assert control.attributes("WakeUpVehicle")["command_reason"] == reason
        assert observed(entry, "ChargingEnabled") is True


@pytest.mark.parametrize("authority", ["Local", None])
async def test_local_and_unknown_authority_never_dispatch(ha, authority):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        event(entry, "ControlAuthority", authority)
        assert not button(ha, entry).available
        assert await control.request("WakeUpVehicle", "1") == "rejected"
        assert not station.wake_requests


async def test_disconnected_unregistered_and_unsupported_evse_never_dispatch(ha):
    entry = await ha.install()
    control = entry.runtime_data.server.control
    assert await control.request("WakeUpVehicle", "1") == "rejected"
    async with peer(entry, station_type=WakeupStation) as station:
        await ready(entry, station)
        state = entry.runtime_data.state
        state.registered_generation = None
        assert await control.request("WakeUpVehicle", "1") == "rejected"
        state.registered_generation = state.generation
        state.binding = (state.binding[0], "2", "1")
        assert await control.request("WakeUpVehicle", "1") == "rejected"
        assert (
            control.attributes("WakeUpVehicle")["execution_blocked_reason"]
            == "unsupported_evse"
        )
        assert not station.wake_requests


async def test_concurrent_presses_and_cancelled_caller_share_owned_request(ha):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        first = asyncio.create_task(control.request("WakeUpVehicle", "1"))
        await station.started.wait()
        second = asyncio.create_task(control.request("WakeUpVehicle", "1"))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert control.intents["WakeUpVehicle"].status == "pending"
        station.release.set()
        assert await second == "confirmed"
        assert len(station.wake_requests) == 1


async def test_authority_loss_and_reacquisition_supersede_response(ha):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("WakeUpVehicle", "1"))
        await station.started.wait()
        event(entry, "ControlAuthority", "Local")
        assert control.attributes("WakeUpVehicle")["command_status"] == "superseded"
        assert not button(ha, entry).available
        event(entry, "ControlAuthority", "OCPP")
        station.release.set()
        assert await pending == "superseded"
        assert len(station.wake_requests) == 1


@pytest.mark.parametrize("evse", [2, True])
async def test_dispatch_revalidates_authority_and_exact_payload(ha, monkeypatch, evse):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        adapter = entry.runtime_data.server.adapter
        original = adapter._send
        mutate = True

        async def altered(message):
            nonlocal mutate
            frame = json.loads(message)
            if frame[0] == 2 and frame[2] == "DataTransfer" and mutate:
                mutate = False
                frame[3]["data"]["evseId"] = evse
                message = json.dumps(frame)
            await original(message)

        monkeypatch.setattr(adapter, "_send", altered)
        assert await control.request("WakeUpVehicle", "1") == "superseded"
        assert not station.wake_requests
        # Wait under the existing transport lock, then lose authority before dispatch.
        async with adapter._call_lock:
            pending = asyncio.create_task(control.request("WakeUpVehicle", "1"))
            await eventually(lambda: control.intents["WakeUpVehicle"].task is not None)
            await asyncio.sleep(0)
            event(entry, "ControlAuthority", "Local")
        assert await pending == "superseded"
        assert not station.wake_requests


@pytest.mark.parametrize(
    "failure,expected", [(TimeoutError, "timeout"), (OSError, "communication_failure")]
)
async def test_transport_failure_is_reported(ha, monkeypatch, failure, expected):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        monkeypatch.setattr(
            entry.runtime_data.server.adapter, "request", AsyncMock(side_effect=failure)
        )
        assert await control.request("WakeUpVehicle", "1") == expected
        assert not station.wake_requests


async def test_only_wakeup_extends_response_wait_and_preserves_general_timeout(
    ha, monkeypatch
):
    entry = await ha.install()
    waits = []
    original = Adapter._get_specific_response

    async def record(adapter, unique_id, response_timeout):
        from custom_components.wallbox_manager.ocpp import RESPONSE_TIMEOUT

        waits.append(RESPONSE_TIMEOUT.get() or response_timeout)
        return await original(adapter, unique_id, response_timeout)

    monkeypatch.setattr(Adapter, "_get_specific_response", record)
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        general = entry.runtime_data.server.adapter._response_timeout
        waits.clear()
        assert await control.request("WakeUpVehicle", "1") == "confirmed"
        assert waits == [WAKEUP_TIMEOUT]
        waits.clear()
        assert await control.request("ChargingEnabled", "false") == "confirmed"
        assert waits and all(wait == general for wait in waits)
        assert entry.runtime_data.server.adapter._response_timeout == general
        assert WAKEUP_TIMEOUT >= 300 + 4


@pytest.mark.parametrize("change", ["disconnect", "boot"])
async def test_disconnect_and_boot_supersede_without_replay(ha, change):
    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        control = await ready(entry, station)
        adapter = entry.runtime_data.server.adapter
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("WakeUpVehicle", "1"))
        await station.started.wait()
        if change == "disconnect":
            await station._connection.close()
            await eventually(lambda: adapter.closed)
            station.release.set()
            await asyncio.gather(pending, return_exceptions=True)
        else:
            generation = adapter.generation
            await station._connection.send(
                json.dumps(
                    [
                        2,
                        "reboot",
                        "BootNotification",
                        {
                            "chargingStation": {
                                "vendorName": "Test",
                                "model": "Wakeup",
                            },
                            "reason": "PowerUp",
                        },
                    ]
                )
            )
            await eventually(lambda: adapter.generation != generation)
            station.release.set()
            assert await pending == "superseded"
        assert control.intents["WakeUpVehicle"].status == "superseded"
        assert len(station.wake_requests) == 1
    async with peer(entry, station_type=WakeupStation) as station:
        await ready(entry, station)
        assert not station.wake_requests
        assert not station.writes
        assert control.intents["WakeUpVehicle"].status == "superseded"


async def test_multiple_entries_and_direct_datatransfer_restriction(ha):
    one, two = await ha.install(), await ha.install(config())
    async with (
        peer(one, station_type=WakeupStation) as a,
        peer(two, station_type=WakeupStation) as b,
    ):
        ca, cb = await ready(one, a), await ready(two, b)
        assert await ca.request("WakeUpVehicle", "1") == "confirmed"
        assert not b.wake_requests
        assert cb.intents == {}
        for payload in [
            call.DataTransfer(
                vendor_id=WAKEUP_VENDOR, message_id=WAKEUP_MESSAGE, data={"evse_id": 1}
            ),
            call.DataTransfer(vendor_id="arbitrary", data={}),
        ]:
            with pytest.raises(RuntimeError, match="boundary"):
                await two.runtime_data.server.adapter.request(payload)
        assert not b.wake_requests
        assert DISPATCH.get() is None


async def test_unmatched_responses_do_not_restart_extended_deadline(ha, monkeypatch):
    from ocpp.v21 import ChargePoint

    from custom_components.wallbox_manager.ocpp import RESPONSE_TIMEOUT

    entry = await ha.install()
    async with peer(entry, station_type=WakeupStation) as station:
        await ready(entry, station)
        adapter = entry.runtime_data.server.adapter
        waits = []

        async def library_wait(self, unique_id, response_timeout):
            waits.append(response_timeout)
            if len(waits) == 1:
                return await self._get_specific_response(
                    unique_id, response_timeout - 1
                )
            return "matched"

        monkeypatch.setattr(ChargePoint, "_get_specific_response", library_wait)
        token = RESPONSE_TIMEOUT.set(WAKEUP_TIMEOUT)
        try:
            assert await adapter._get_specific_response("expected", 10) == "matched"
        finally:
            RESPONSE_TIMEOUT.reset(token)
        assert waits == [WAKEUP_TIMEOUT, WAKEUP_TIMEOUT - 1]
