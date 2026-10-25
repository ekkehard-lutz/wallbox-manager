"""Explicit control over real OCPP frames; no hardware or regulation."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import config, eventually
from ocpp.routing import on
from ocpp.v21 import call, call_result
from test_protocol import Station, boot, peer

from custom_components.wallbox_manager.button import TakeControl
from custom_components.wallbox_manager.control import endpoint
from custom_components.wallbox_manager.switch import Permission


class Controllable(Station):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for row in self.rows[:2]:
            row["variable_attribute"][0]["mutability"] = "ReadWrite"
        self.writes = []
        self.result = "Accepted"
        self.apply = True
        self.release = None
        self.started = asyncio.Event()
        self.wrong_result = False

    @on("SetVariables")
    async def write(self, set_variable_data):
        self.writes.extend(set_variable_data)
        self.started.set()
        if self.release:
            await self.release.wait()
        item = set_variable_data[0]
        if self.apply and self.result == "Accepted":
            self.values[item["variable"]["name"]] = item["attribute_value"]
        return call_result.SetVariables(
            set_variable_result=[
                {
                    **endpoint(
                        "ChargingEnabled"
                        if self.wrong_result
                        else item["variable"]["name"]
                    ),
                    "attribute_type": "Actual",
                    "attribute_status": self.result,
                }
            ]
        )


async def ready(entry, station):
    await boot(station)
    await eventually(
        lambda: entry.runtime_data.server.initialization["status"] == "missing"
    )
    return entry.runtime_data.server.control


def observed(entry, name):
    return entry.runtime_data.state.value(entry.runtime_data.server.control.key(name))


def event(entry, name, value, *, component=None, variable=None):
    entry.runtime_data.server.adapter.variable(
        entry.runtime_data.state.generation,
        component or endpoint(name)["component"],
        variable or endpoint(name)["variable"],
        value,
        datetime.now(UTC),
        "NotifyEvent",
    )


async def test_explicit_takeover_and_permission_without_vehicle_or_profile(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        assert not station.writes
        assert observed(entry, "ControlAuthority") == "local"
        assert await control.request("ChargingEnabled", "false") == "confirmed"
        assert observed(entry, "ControlAuthority") == "local"
        assert len(station.writes) == 1  # Hardware permission needs no takeover.
        assert await control.request("ControlAuthority", "OCPP") == "confirmed"
        assert observed(entry, "ControlAuthority") == "remote"
        assert await control.request("ChargingEnabled", "false") == "confirmed"
        assert observed(entry, "ChargingEnabled") is False
        assert await control.request("ChargingEnabled", "true") == "confirmed"
        assert observed(entry, "ChargingEnabled") is True
        assert entry.runtime_data.state.session is None
        assert [w["attribute_value"] for w in station.writes] == [
            "false",
            "OCPP",
            "true",
        ]
        assert set(station.operations) <= {
            "GetBaseReport",
            "GetVariables",
            "SetVariables",
        }
        await control.request("ControlAuthority", "OCPP")
        await control.request("ChargingEnabled", "true")
        assert len(station.writes) == 3


@pytest.mark.parametrize("name", ["ControlAuthority", "ChargingEnabled"])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("Rejected", "rejected"),
        ("UnknownVariable", "unsupported"),
        ("RebootRequired", "rejected"),
    ],
)
async def test_rejected_and_unsupported_writes(ha, name, status, expected):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        if name == "ChargingEnabled":
            await control.request("ControlAuthority", "OCPP")
        station.result = status
        before = observed(entry, name)
        assert (
            await control.request(
                name, "OCPP" if name == "ControlAuthority" else "false"
            )
            == expected
        )
        assert observed(entry, name) == before
        assert control.attributes(name)["command_reason"] == status


@pytest.mark.parametrize("name", ["ControlAuthority", "ChargingEnabled"])
@pytest.mark.parametrize("failed_read", [False, True])
async def test_accepted_unconfirmed_preserves_observation(ha, name, failed_read):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        if name == "ChargingEnabled":
            await control.request("ControlAuthority", "OCPP")
        station.apply = False
        if failed_read:
            station.fail_reads.add(name)
        before = observed(entry, name)
        assert (
            await control.request(
                name, "OCPP" if name == "ControlAuthority" else "false"
            )
            == "accepted_unconfirmed"
        )
        assert observed(entry, name) == before
        event(entry, name, "OCPP" if name == "ControlAuthority" else "false")
        assert control.attributes(name)["command_status"] == "confirmed"


async def test_mismatched_ack_and_strict_notification_scope(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.wrong_result = True
        assert (
            await control.request("ControlAuthority", "OCPP") == "communication_failure"
        )
        assert observed(entry, "ControlAuthority") == "local"
        event(
            entry,
            "ControlAuthority",
            "OCPP",
            component={"name": "WallboxController", "instance": "other"},
        )
        event(
            entry,
            "ChargingEnabled",
            "false",
            component={"name": "WallboxController", "evse": {"id": 1}},
        )
        assert observed(entry, "ControlAuthority") == "local"
        assert observed(entry, "ChargingEnabled") is True


async def test_duplicate_takeover_coalesces_and_pending_never_erases_local(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        one = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        two = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await asyncio.sleep(0)
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_status"] == "pending"
        station.release.set()
        assert await asyncio.gather(one, two) == ["confirmed", "confirmed"]
        assert len(station.writes) == 1


async def test_newer_permission_intent_corrects_inflight_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        station.started.clear()
        station.release = asyncio.Event()
        off = asyncio.create_task(control.request("ChargingEnabled", "false"))
        await station.started.wait()
        on = asyncio.create_task(control.request("ChargingEnabled", "true"))
        await asyncio.sleep(0)
        assert control.attributes("ChargingEnabled")["requested_value"] == "true"
        station.release.set()
        assert await asyncio.gather(off, on) == ["superseded", "confirmed"]
        assert [w["attribute_value"] for w in station.writes] == [
            "OCPP",
            "false",
            "true",
        ]
        assert observed(entry, "ChargingEnabled") is True


async def test_obsolete_queued_command_is_never_dispatched(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        adapter = entry.runtime_data.server.adapter
        async with adapter._call_lock:
            old = asyncio.create_task(control.request("ChargingEnabled", "false"))
            await eventually(lambda: "ChargingEnabled" in control.intents)
            new = asyncio.create_task(control.request("ChargingEnabled", "true"))
            await eventually(lambda: control.intents["ChargingEnabled"].value == "true")
        assert await asyncio.gather(old, new) == ["superseded", "confirmed"]
        assert [w["attribute_value"] for w in station.writes] == ["OCPP", "true"]


async def test_authority_transition_preserves_independent_permission_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        station.started.clear()
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("ChargingEnabled", "false"))
        await station.started.wait()
        event(entry, "ControlAuthority", "Local")
        assert control.blocked("ChargingEnabled") is None
        station.release.set()
        assert await pending == "confirmed"
        assert observed(entry, "ChargingEnabled") is False
        assert observed(entry, "ControlAuthority") == "local"


async def test_boot_fences_pending_command_without_replay(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        adapter = entry.runtime_data.server.adapter
        adapter.boot({"vendor_name": "Test", "model": "reboot"}, "PowerUp")
        station.release.set()
        assert await pending == "superseded"
        assert observed(entry, "ControlAuthority") is None
        assert not adapter.writable
        assert len(station.writes) == 1


async def test_disconnect_cancels_inflight_call_and_reconnect_never_replays(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        await entry.runtime_data.server.adapter.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        station.release.set()
        assert control.attributes("ControlAuthority")["command_status"] == "superseded"
    async with peer(entry, station_type=Controllable) as station:
        await ready(entry, station)
        assert not station.writes
        assert observed(entry, "ControlAuthority") == "local"


async def test_write_timeout_and_late_response_cannot_confirm(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        entry.runtime_data.server.adapter._response_timeout = 0.2
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        assert await pending == "timeout"
        station.release.set()
        await station.call(call.Heartbeat())
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_status"] == "timeout"


async def test_external_permission_changes_and_unchanged_reads_do_not_publish(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        state = entry.runtime_data.state
        publications = []
        state.watch(control.key("ChargingEnabled"), lambda: publications.append(True))
        adapter = entry.runtime_data.server.adapter
        poll = adapter.poll
        for _ in range(5):
            await adapter.read(state.generation, endpoint("ChargingEnabled"))
        assert not publications
        station.values["ChargingEnabled"] = "false"
        await adapter.read(state.generation, endpoint("ChargingEnabled"))
        assert publications == [True]
        assert observed(entry, "ChargingEnabled") is False
        assert adapter.poll is poll
        assert not station.writes


async def test_two_entries_and_entity_identity_diagnostics(ha):
    one, two = await ha.install(config()), await ha.install(config())
    async with (
        peer(one, station_type=Controllable) as a,
        peer(two, station_type=Controllable) as b,
    ):
        ca, cb = await ready(one, a), await ready(two, b)
        button = TakeControl(one, "station", ("Wallbox01", None, None), "take_control")
        switch = Permission(
            one, "control", one.runtime_data.state.binding, "charging_enabled"
        )
        unique = button.unique_id, switch.unique_id
        await button.async_press()
        await switch.async_turn_off()
        assert unique == (button.unique_id, switch.unique_id)
        assert switch.is_on is False
        assert switch.extra_state_attributes["command_status"] == "confirmed"
        assert "command_timestamp" not in switch.extra_state_attributes
        assert ca is not cb
        assert observed(two, "ControlAuthority") == "local"
        assert not b.writes


@pytest.mark.parametrize(
    "payload",
    [
        call.Reset(type="Immediate"),
        call.SetVariables(
            set_variable_data=[
                {
                    **endpoint("ControlAuthority"),
                    "attribute_type": "Actual",
                    "attribute_value": "Local",
                }
            ]
        ),
        call.SetVariables(
            set_variable_data=[
                {
                    **endpoint("ChargingEnabled"),
                    "attribute_type": "Actual",
                    "attribute_value": "false",
                }
            ]
        ),
    ],
)
async def test_direct_outbound_physical_commands_stay_blocked(ha, payload):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        await ready(entry, station)
        with pytest.raises(RuntimeError, match="boundary"):
            await entry.runtime_data.server.adapter.request(payload)
        assert not station.writes


async def test_concurrent_takeover_and_permission_serialize_independently(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        takeover = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        permission = asyncio.create_task(control.request("ChargingEnabled", "false"))
        await asyncio.sleep(0)
        station.release.set()
        assert await asyncio.gather(takeover, permission) == ["confirmed", "confirmed"]
        assert len(station.writes) == 2
        assert observed(entry, "ChargingEnabled") is False


async def test_duplicate_unconfirmed_takeover_does_not_repeat_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.apply = False
        assert (
            await control.request("ControlAuthority", "OCPP") == "accepted_unconfirmed"
        )
        assert (
            await control.request("ControlAuthority", "OCPP") == "accepted_unconfirmed"
        )
        assert len(station.writes) == 1


@pytest.mark.parametrize("failure", ["timeout", "unknown", "malformed"])
async def test_unsuccessful_readback_retains_accepted_write(ha, monkeypatch, failure):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        adapter = entry.runtime_data.server.adapter
        request = adapter.request

        async def failed(payload):
            if isinstance(payload, call.GetVariables):
                if failure == "timeout":
                    raise TimeoutError
                return SimpleNamespace(
                    get_variable_result=[
                        {**endpoint("ChargingEnabled"), "attribute_status": "Rejected"}
                    ]
                    if failure == "unknown"
                    else []
                )
            return await request(payload)

        monkeypatch.setattr(adapter, "request", failed)
        assert (
            await control.request("ChargingEnabled", "false") == "accepted_unconfirmed"
        )
        assert observed(entry, "ChargingEnabled") is True
        assert control.attributes("ChargingEnabled")["command_reason"] == (
            "readback_timeout" if failure == "timeout" else "readback_unknown"
        )


async def test_queued_command_refused_after_authority_loss_and_reacquisition(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        async with entry.runtime_data.server.adapter._call_lock:
            pending = asyncio.create_task(control.request("ChargingEnabled", "false"))
            await eventually(lambda: "ChargingEnabled" in control.intents)
            event(entry, "ControlAuthority", "Local")
            event(entry, "ControlAuthority", "OCPP")
        assert await pending == "confirmed"
        assert len(station.writes) == 2


async def test_read_only_inventory_never_authorizes_control(ha):
    entry = await ha.install()
    async with peer(entry) as station:
        control = await ready(entry, station)
        assert control.blocked("ControlAuthority") == "unsupported"
        assert await control.request("ControlAuthority", "OCPP") == "unsupported"
        assert "SetVariables" not in station.operations


async def test_newer_notification_wins_over_delayed_command_readback(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        adapter = entry.runtime_data.server.adapter
        request = adapter.request
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(payload):
            response = await request(payload)
            if isinstance(payload, call.GetVariables):
                started.set()
                await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        pending = asyncio.create_task(control.request("ChargingEnabled", "false"))
        await started.wait()
        event(entry, "ChargingEnabled", "true")
        release.set()
        assert await pending == "accepted_unconfirmed"
        assert observed(entry, "ChargingEnabled") is True


async def test_superseded_readback_cannot_overwrite_newer_intent(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        adapter = entry.runtime_data.server.adapter
        request = adapter.request
        started, release = asyncio.Event(), asyncio.Event()
        first = True

        async def delayed(payload):
            nonlocal first
            response = await request(payload)
            if isinstance(payload, call.GetVariables) and first:
                first = False
                started.set()
                await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        off = asyncio.create_task(control.request("ChargingEnabled", "false"))
        await started.wait()
        assert await control.request("ChargingEnabled", "true") == "confirmed"
        release.set()
        assert await off == "superseded"
        assert observed(entry, "ChargingEnabled") is True
        assert control.attributes("ChargingEnabled")["command_status"] == "confirmed"


async def test_caller_cancellation_does_not_abandon_serial_response_consumer(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        caller = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await station.started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        task = control.intents["ControlAuthority"].task
        assert not task.done()
        station.release.set()
        assert await task == "confirmed"
        assert await control.request("ChargingEnabled", "false") == "confirmed"


async def test_permission_busy_reason_is_retained_without_state_change(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        adapter = entry.runtime_data.server.adapter

        async def busy(payload):
            return SimpleNamespace(
                set_variable_result=[
                    {
                        **endpoint("ChargingEnabled"),
                        "attribute_type": "Actual",
                        "attribute_status": "Rejected",
                        "status_info": {"reason_code": "Busy"},
                    }
                ]
            )

        monkeypatch.setattr(adapter, "request", busy)
        assert await control.request("ChargingEnabled", "false") == "rejected"
        assert control.attributes("ChargingEnabled")["command_reason"] == "Busy"
        assert observed(entry, "ChargingEnabled") is True


async def test_set_communication_error_is_explicit(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await ready(entry, station)

        async def broken(payload):
            raise OSError("simulated transport loss")

        monkeypatch.setattr(entry.runtime_data.server.adapter, "request", broken)
        assert (
            await control.request("ControlAuthority", "OCPP") == "communication_failure"
        )
        assert observed(entry, "ControlAuthority") == "local"


async def unconfirmed_takeover(entry, station):
    control = await ready(entry, station)
    station.fail_reads.add("ControlAuthority")
    assert await control.request("ControlAuthority", "OCPP") == "accepted_unconfirmed"
    assert observed(entry, "ControlAuthority") == "local"
    assert station.values["ControlAuthority"] == "OCPP"
    return control


async def test_explicit_takeover_recovery_reads_without_another_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        intent = control.intents["ControlAuthority"]
        poll = entry.runtime_data.server.adapter.poll
        reads = len(station.reads)
        button = TakeControl(
            entry, "station", ("Wallbox01", None, None), "take_control"
        )
        await button.async_press()
        assert control.intents["ControlAuthority"] is intent
        assert intent.status == "confirmed" and intent.reason is None
        assert observed(entry, "ControlAuthority") == "remote"
        assert len(station.reads) == reads + 1
        assert len(station.writes) == 1
        assert entry.runtime_data.server.adapter.poll is poll
        await button.async_press()
        assert len(station.reads) == reads + 1
        assert len(station.writes) == 1


async def test_recovery_local_readback_is_unchanged_and_not_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        station.values["ControlAuthority"] = "Local"
        publications = []
        state = entry.runtime_data.state
        key = control.key("ControlAuthority")
        state.watch(key, lambda: publications.append(state.value(key)))
        reads = len(station.reads)
        for _ in range(2):
            assert (
                await control.request("ControlAuthority", "OCPP")
                == "accepted_unconfirmed"
            )
        assert len(station.reads) == reads + 2
        assert not publications
        assert len(station.writes) == 1
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_reason"] is None


@pytest.mark.parametrize("failure", ["timeout", "rejected", "mismatched", "unknown"])
async def test_failed_recovery_read_never_changes_canonical_authority(
    ha, monkeypatch, failure
):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        state = entry.runtime_data.state
        key = control.key("ControlAuthority")
        observation = state.observations[key]
        reads = []

        async def failed(payload):
            assert isinstance(payload, call.GetVariables)
            reads.append(payload)
            if failure == "timeout":
                raise TimeoutError
            return SimpleNamespace(
                get_variable_result=[
                    {
                        **endpoint(
                            "ChargingEnabled"
                            if failure == "mismatched"
                            else "ControlAuthority"
                        ),
                        "attribute_status": "Rejected"
                        if failure == "rejected"
                        else "Accepted",
                        "attribute_value": "invalid"
                        if failure == "unknown"
                        else "OCPP",
                    }
                ]
            )

        monkeypatch.setattr(entry.runtime_data.server.adapter, "request", failed)
        assert (
            await control.request("ControlAuthority", "OCPP") == "accepted_unconfirmed"
        )
        assert len(reads) == 1 and len(station.writes) == 1
        assert state.observations[key] is observation
        assert control.attributes("ControlAuthority")["command_reason"] == (
            "readback_timeout" if failure == "timeout" else "readback_unknown"
        )


async def test_concurrent_explicit_recovery_requests_share_one_read(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        adapter = entry.runtime_data.server.adapter
        read = adapter.read
        started, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def delayed(*args, **kwargs):
            nonlocal count
            count += 1
            started.set()
            await release.wait()
            return await read(*args, **kwargs)

        monkeypatch.setattr(adapter, "read", delayed)
        one = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await started.wait()
        task = control.intents["ControlAuthority"].task
        two = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await asyncio.sleep(0)
        assert control.intents["ControlAuthority"].task is task
        release.set()
        assert await asyncio.gather(one, two) == ["confirmed", "confirmed"]
        assert count == 1 and len(station.writes) == 1


async def test_newer_local_notification_wins_over_recovery_read(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        adapter = entry.runtime_data.server.adapter
        request = adapter.request
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(payload):
            assert isinstance(payload, call.GetVariables)
            response = await request(payload)
            started.set()
            await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        recovery = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await started.wait()
        event(entry, "ControlAuthority", "Local")
        release.set()
        assert await recovery == "accepted_unconfirmed"
        assert observed(entry, "ControlAuthority") == "local"
        assert control.blocked("ChargingEnabled") is None
        assert len(station.writes) == 1


async def test_boot_fences_delayed_recovery_read(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        adapter = entry.runtime_data.server.adapter
        request = adapter.request
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(payload):
            response = await request(payload)
            started.set()
            await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        recovery = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await started.wait()
        adapter.boot({"vendor_name": "Test", "model": "reboot"}, "PowerUp")
        release.set()
        assert await recovery == "superseded"
        assert observed(entry, "ControlAuthority") is None
        assert len(station.writes) == 1


async def test_reconnect_cancels_recovery_and_never_replays_it(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        adapter = entry.runtime_data.server.adapter
        started = asyncio.Event()

        async def delayed(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(adapter, "read", delayed)
        recovery = asyncio.create_task(control.request("ControlAuthority", "OCPP"))
        await started.wait()
        await adapter.close()
        with pytest.raises(asyncio.CancelledError):
            await recovery
        assert control.attributes("ControlAuthority")["command_status"] == "superseded"
        assert len(station.writes) == 1
    async with peer(entry, station_type=Controllable) as station:
        await ready(entry, station)
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_status"] == "superseded"
        assert not station.writes
        assert station.reads.count("ControlAuthority") == 1  # Bootstrap only.


async def test_confirmed_command_is_history_after_external_loss_and_reconnect(ha):
    entry = await ha.install()
    async with peer(entry, station_type=Controllable) as station:
        control = await unconfirmed_takeover(entry, station)
        assert await control.request("ControlAuthority", "OCPP") == "confirmed"
        event(entry, "ControlAuthority", "Local")
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_status"] == "confirmed"
        assert control.blocked("ChargingEnabled") is None
    async with peer(entry, station_type=Controllable) as station:
        await ready(entry, station)
        assert observed(entry, "ControlAuthority") == "local"
        assert control.attributes("ControlAuthority")["command_status"] == "confirmed"
        assert not station.writes
