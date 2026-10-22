"""Confirmed persistent station enable switch; WBM never detects or times wake-up."""

import asyncio
import json

import pytest
from conftest import config, entities, eventually
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from ocpp.v21 import call
from test_active_control import Controllable, event, observed, ready
from test_protocol import peer, row

from custom_components.wallbox_manager import async_unload_entry
from custom_components.wallbox_manager.control import endpoint


class AutomaticStation(Controllable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.values["ControlAuthority"] = "OCPP"
        self.values["AutoWakeUpEnabled"] = "false"
        automatic = row("WallboxController", "AutoWakeUpEnabled", "false")
        automatic["variable_attribute"][0].update(
            mutability="ReadWrite", persistent=True
        )
        self.rows.append(automatic)


class PersistedOnStation(AutomaticStation):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.values["AutoWakeUpEnabled"] = "true"


def switch(ha, entry):
    return next(e for e in entities(ha, entry) if e.role == "auto_wakeup_enabled")


async def test_switch_identity_device_names_and_no_manual_button(ha):
    entry = await ha.install()
    automatic = switch(ha, entry)
    assert automatic.unique_id == f"{entry.entry_id}:Wallbox01:auto_wakeup_enabled"
    assert automatic.translation_key == "auto_wakeup_enabled"
    assert automatic.should_poll is False
    assert not automatic.available
    assert automatic.is_on is None
    assert (
        automatic.extra_state_attributes["state_represents"]
        == "confirmed_station_setting"
    )
    assert not any(e.role == "wake_up_vehicle" for e in entities(ha, entry))
    registry = er.async_get(ha)
    item = registry.async_get(automatic.entity_id)
    takeover = next(e for e in entities(ha, entry) if e.role == "take_control")
    assert item.device_id == registry.async_get(takeover.entity_id).device_id
    assert item.disabled_by is None and item.hidden_by is None
    for filename, label in [
        ("en.json", "Automatic vehicle wake-up"),
        ("de.json", "Automatisches Aufwecken"),
    ]:
        from pathlib import Path

        labels = json.loads(
            await asyncio.to_thread(
                Path(
                    "custom_components/wallbox_manager/translations", filename
                ).read_text
            )
        )
        assert labels["entity"]["switch"]["auto_wakeup_enabled"]["name"] == label
        assert "wake_up_vehicle" not in labels["entity"]["button"]
    async with peer(entry, station_type=AutomaticStation) as station:
        await ready(entry, station)
        assert automatic.available and automatic.is_on is False
        await automatic.async_turn_on()
        assert automatic.is_on is True
        assert station.writes == [
            {
                **endpoint("AutoWakeUpEnabled"),
                "attribute_type": "Actual",
                "attribute_value": "true",
            }
        ]
        await automatic.async_turn_off()
        assert automatic.is_on is False
        assert observed(entry, "ChargingEnabled") is True
        assert observed(entry, "ControlAuthority") == "remote"


async def test_ack_without_confirmed_setting_is_not_optimistic(ha):
    entry = await ha.install()
    async with peer(entry, station_type=AutomaticStation) as station:
        control = await ready(entry, station)
        station.apply = False
        with pytest.raises(HomeAssistantError, match="accepted_unconfirmed"):
            await switch(ha, entry).async_turn_on()
        assert switch(ha, entry).is_on is False
        assert (
            control.attributes("AutoWakeUpEnabled")["command_status"]
            == "accepted_unconfirmed"
        )
        event(entry, "AutoWakeUpEnabled", "true")
        assert switch(ha, entry).is_on is True
        assert control.attributes("AutoWakeUpEnabled")["command_status"] == "confirmed"


@pytest.mark.parametrize(
    "result,status", [("Rejected", "rejected"), ("UnknownVariable", "unsupported")]
)
async def test_persistence_or_unsupported_rejection_preserves_observed_setting(
    ha, result, status
):
    entry = await ha.install()
    async with peer(entry, station_type=AutomaticStation) as station:
        control = await ready(entry, station)
        station.result = result
        with pytest.raises(HomeAssistantError):
            await switch(ha, entry).async_turn_on()
        assert switch(ha, entry).is_on is False
        assert control.attributes("AutoWakeUpEnabled")["command_status"] == status


async def test_authority_changes_preserve_setting_and_disable_control(ha):
    entry = await ha.install()
    async with peer(entry, station_type=PersistedOnStation) as station:
        control = await ready(entry, station)
        automatic = switch(ha, entry)
        assert automatic.is_on is True and automatic.available
        event(entry, "ControlAuthority", "Local")
        assert automatic.is_on is True and not automatic.available
        assert await control.request("AutoWakeUpEnabled", "false") == "rejected"
        assert not station.writes
        event(entry, "ControlAuthority", "OCPP")
        assert automatic.is_on is True and automatic.available
        assert not station.writes


async def test_bootstrap_and_reconnect_read_persisted_on_without_default_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=PersistedOnStation) as station:
        await ready(entry, station)
        assert switch(ha, entry).is_on is True
        assert "AutoWakeUpEnabled" in station.reads
        assert not station.writes
    await eventually(lambda: not entry.runtime_data.state.connected)
    assert switch(ha, entry).is_on is None
    assert not switch(ha, entry).available
    async with peer(entry, station_type=PersistedOnStation) as station:
        await ready(entry, station)
        assert switch(ha, entry).is_on is True
        assert not station.writes
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    async with peer(entry, station_type=PersistedOnStation) as station:
        await ready(entry, station)
        assert switch(ha, entry).is_on is True
        assert not station.writes


async def test_concurrent_writes_coalesce_and_authority_loss_fences_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=AutomaticStation) as station:
        control = await ready(entry, station)
        station.release = asyncio.Event()
        first = asyncio.create_task(control.request("AutoWakeUpEnabled", "true"))
        await station.started.wait()
        second = asyncio.create_task(control.request("AutoWakeUpEnabled", "true"))
        await asyncio.sleep(0)
        event(entry, "ControlAuthority", "Local")
        station.release.set()
        assert await first == await second == "superseded"
        assert len(station.writes) == 1
        assert not switch(ha, entry).available


async def test_notifications_are_scoped_and_unchanged_values_do_not_publish(ha):
    one, two = await ha.install(), await ha.install(config())
    async with (
        peer(one, station_type=AutomaticStation) as a,
        peer(two, station_type=AutomaticStation) as b,
    ):
        ca, cb = await ready(one, a), await ready(two, b)
        writes = []
        remove = one.runtime_data.state.watch(
            ca.key("AutoWakeUpEnabled"), lambda: writes.append(True)
        )
        event(one, "AutoWakeUpEnabled", "true")
        assert switch(ha, one).is_on is True and switch(ha, two).is_on is False
        event(one, "AutoWakeUpEnabled", "true")
        assert len(writes) == 1
        event(
            one,
            "AutoWakeUpEnabled",
            "false",
            component={"name": "WallboxController", "evse": {"id": 1}},
        )
        assert switch(ha, one).is_on is True
        assert cb.intents == {} and not b.writes
        remove()


async def test_legacy_registry_button_is_retired_without_foreign_removal(ha):
    entry = await ha.install()
    registry = er.async_get(ha)
    legacy = registry.async_get_or_create(
        "button",
        "wallbox_manager",
        f"{entry.entry_id}:Wallbox01:wake_up_vehicle",
        config_entry=entry,
    )
    foreign = registry.async_get_or_create(
        "button",
        "wallbox_manager",
        "foreign:Wallbox01:wake_up_vehicle",
        config_entry=entry,
    )
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(foreign.entity_id) is not None
    assert not any(e.role == "wake_up_vehicle" for e in entities(ha, entry))


@pytest.mark.parametrize("writable", [False, True])
async def test_exact_authorization_and_legacy_datatransfer_stays_blocked(ha, writable):
    entry = await ha.install()
    async with peer(entry, station_type=AutomaticStation) as station:
        control = await ready(entry, station)
        adapter = entry.runtime_data.server.adapter
        if not writable:
            adapter.writable.discard("AutoWakeUpEnabled")
            assert await control.request("AutoWakeUpEnabled", "true") == "unsupported"
        for payload in [
            call.DataTransfer(
                vendor_id="wallbox-stationary",
                message_id="WakeUpVehicle",
                data={"evse_id": 1},
            ),
            call.SetVariables(
                set_variable_data=[
                    {**endpoint("AutoWakeUpEnabled"), "attribute_value": "true"}
                ]
            ),
        ]:
            with pytest.raises(RuntimeError, match="boundary"):
                await adapter.request(payload)
        assert not station.writes


async def test_pending_write_disconnect_is_superseded_and_never_replayed(ha):
    entry = await ha.install()
    async with peer(entry, station_type=AutomaticStation) as station:
        control = await ready(entry, station)
        adapter = entry.runtime_data.server.adapter
        station.release = asyncio.Event()
        pending = asyncio.create_task(control.request("AutoWakeUpEnabled", "true"))
        await station.started.wait()
        await station._connection.close()
        await eventually(lambda: adapter.closed)
        station.release.set()
        await asyncio.gather(pending, return_exceptions=True)
        assert control.intents["AutoWakeUpEnabled"].status == "superseded"
    async with peer(entry, station_type=PersistedOnStation) as station:
        await ready(entry, station)
        assert switch(ha, entry).is_on is True
        assert not station.writes
        assert control.intents["AutoWakeUpEnabled"].status == "superseded"
