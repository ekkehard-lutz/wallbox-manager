"""WBM-ACTION-HW, WBM-TIME-25–28 and WBM-LOG-NETZ."""

import asyncio
import json
from copy import deepcopy

import pytest
from conftest import entities
from homeassistant.components import button as ha_button
from homeassistant.exceptions import HomeAssistantError
from test_active_control import ready
from test_grid_profile import GridStation
from test_live_netz_timing import clock_grid, configure, editing, running
from test_protocol import peer
from test_release_recovery import session
from test_safe_charge_actions import context, invoke

from custom_components.wallbox_manager import async_unload_entry

PROFILES = ("NETZ", "PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")


def permission(grid, value):
    grid.state.observe(
        grid.state.generation,
        grid.listener.control.key("ChargingEnabled"),
        value,
        "test_permission",
        status="unknown" if value is None else "ready",
    )
    assert grid.hardware_enabled() is value


def offered(station):
    return [
        p[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"]
        for p in station.profiles
    ]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("hardware", [False, None])
async def test_hardware_blocks_explicit_and_legacy_start_but_drafts_remain_editable(
    ha, profile, hardware
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, _ = clock_grid(entry)
        await settings.initialize_power()
        await settings.set("charging_profile", profile)
        await session(station)
        await ha.async_block_till_done()
        permission(grid, hardware)
        assert grid.eligible()
        assert (
            grid.action_state()["charge_action_blocked_reason"] == "hardware_disabled"
        )
        assert await ha_button.async_setup(ha, {})
        button = next(e for e in entities(ha, entry) if e.role == "charge_action")
        with pytest.raises(HomeAssistantError, match="hardware_disabled"):
            await invoke(ha, entry, context(grid))
        with pytest.raises(HomeAssistantError, match="hardware_disabled"):
            await ha.services.async_call(
                "button", "press", {"entity_id": button.entity_id}, blocking=True
            )
        await settings.set("grid_duration", "00:03")
        assert settings.get("grid_duration") == 180
        assert not settings.get("charge_release") and not settings.get("grid_request")
        assert not any(offered(station)) and not station.writes
        permission(grid, True)
        assert grid.action_state()["charge_action_ready"]


@pytest.mark.parametrize(
    "profile,action", [(p, "start") for p in PROFILES] + [("NETZ", "apply")]
)
async def test_permission_loss_supersedes_a_durable_action_in_flight(
    ha, monkeypatch, profile, action
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, _ = clock_grid(entry)
        await settings.set("charging_profile", profile)
        await settings.initialize_power()
        await configure(settings, "00:02", "00:03")
        if action == "apply":
            await invoke(ha, entry, context(grid))
            await settings.set("grid_duration", "00:04")
        previous = deepcopy(settings.values)
        persist = settings.persist
        entered, complete = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            if not entered.is_set() and profiles[settings.key]["charge_release"]:
                entered.set()
                await complete.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        work = asyncio.create_task(invoke(ha, entry, context(grid)))
        async with asyncio.timeout(5):
            await entered.wait()
        permission(grid, False)
        assert settings.values == previous
        complete.set()
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await work
        assert settings.values == previous
        stored = (await settings.store.async_load())[settings.key]
        assert stored["charge_release"] is previous["charge_release"]
        assert stored["grid_request"] == previous["grid_request"]
        assert stored["grid_approved"] == previous["grid_approved"]
        assert not any(offered(station))


async def test_pending_netz_apply_yields_to_stop_when_permission_is_lost(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, _ = await running(entry, station)
        approved_power = settings.approved("soll_power")
        await settings.set("soll_power", 7.2)
        await settings.set("grid_duration", "00:30")
        assert grid.action_state()["charge_action"] == "apply"
        permission(grid, False)
        state = grid.action_state()
        assert state["charge_action"] == "stop" and state["charge_action_ready"]
        with pytest.raises(HomeAssistantError, match="hardware_disabled"):
            await invoke(ha, entry, context(grid, "apply"))
        await invoke(ha, entry, context(grid))
        if grid.task:
            await grid.task
        assert not settings.get("charge_release") and not settings.get("grid_request")
        assert settings.approved("soll_power") == approved_power
        assert settings.get("soll_power") == 7.2
        assert settings.get("grid_duration") == 1800 and not station.writes
        assert station.amps == 0


@pytest.mark.parametrize("retire", ["expiry", "stop", "profile", "reload", "authority"])
async def test_three_successive_starts_only_use_timing_once(ha, retire):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:02", "00:03")
        await grid.action()
        assert (
            settings.get("grid_request")["start_at"],
            settings.get("grid_request")["end_at"],
        ) == (1120, 1300)
        if retire == "expiry":
            await clock.advance(300)
        elif retire == "stop":
            await grid.stop_release()
        elif retire == "profile":
            await settings.set("charging_profile", "PV_OPTIMUM")
            await settings.set("charging_profile", "NETZ")
        elif retire == "authority":
            grid.state.observe(
                grid.state.generation,
                grid.listener.control.key("ControlAuthority"),
                "local",
                "test_external_authority",
            )
            assert not settings.get("charge_release")
    if retire == "reload":
        await async_unload_entry(ha, entry)
        await ha.install(entry)
        grid, settings, clock = clock_grid(entry)
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        for _ in range(2):
            assert not settings.get("charge_release")
            assert (
                settings.get("grid_start_delay")
                is settings.get("grid_duration")
                is None
            )
            await grid.action()
            request = settings.get("grid_request")
            assert request["start_at"] == clock.now and request["end_at"] is None
            await grid.stop_release()


@pytest.mark.parametrize("value,change", [("00:16", 60), ("00:14", -60), ("", None)])
async def test_projected_duration_apply_uses_old_endpoint_and_counts_edit_time(
    ha, value, change
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        clock.now = 1255  # 15:45 remains, displayed as 00:15.
        request = deepcopy(settings.get("grid_request"))
        count = len(station.profiles)
        await editing(settings, "begin")
        await settings.edit_timing(
            "grid_duration",
            "update",
            "card-a",
            settings.get("grid_timing_revision"),
            value,
            basis="00:15",
        )
        assert settings.get("grid_request") == request
        assert settings.get("grid_start_delay") is None
        await editing(settings, "end")
        await clock.advance(10)
        await grid.action()
        assert settings.get("grid_request")["start_at"] == request["start_at"]
        expected = None if change is None else request["end_at"] + change
        assert settings.get("grid_request")["end_at"] == expected
        assert len(station.profiles) == count


async def test_projected_delay_equal_to_original_still_rearms_on_apply(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:05", "00:02")
        await grid.action()
        await clock.advance(60)
        await editing(settings, "begin")
        await settings.edit_timing(
            "grid_start_delay",
            "update",
            "card-a",
            settings.get("grid_timing_revision"),
            "00:05",
            basis="00:04",
        )
        assert settings.get("grid_request")["start_at"] == 1300
        assert grid.action_state()["charge_action"] == "apply"
        await editing(settings, "end")
        await clock.advance(10)
        await grid.action()
        assert settings.get("grid_request")["start_at"] == 1370
        assert settings.get("grid_request")["end_at"] == 1490


@pytest.mark.parametrize("level", [0, 1, 2])
async def test_november_recorder_sequence_and_bounded_diagnostics(ha, caplog, level):
    entry = await ha.install()
    ha.config_entries.async_update_entry(entry, options={"diagnostic_level": level})
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", ChargingEnabled="false")
        await ready(entry, station)
        await session(station)
        await ha.async_block_till_done()
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        for delay, duration in (
            ("00:01", "00:02"),
            ("00:02", "00:03"),
            ("00:02", "00:03"),
        ):
            await configure(settings, delay, duration)
            with pytest.raises(HomeAssistantError, match="hardware_disabled"):
                await invoke(ha, entry, context(grid))
            assert not settings.get("grid_request")
        assert not any(offered(station))
        assert (
            await grid.listener.control.request("ChargingEnabled", "true")
            == "confirmed"
        )
        await invoke(ha, entry, context(grid))
        await clock.advance(119)
        assert not any(offered(station))
        await clock.advance(1)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert station.amps == 6
        permission(grid, False)
        for _ in range(300):
            grid.changed()
            grid.publish()
        await clock.advance(180)
        await ha.async_block_till_done()
        assert not settings.get("charge_release")
        assert [a for a in offered(station) if a > 0] == [6]
    records = [
        json.loads(r.getMessage().split("subsystem=netz ", 1)[1])
        for r in caplog.records
        if "WBMGR subsystem=netz " in r.getMessage()
    ]
    events = [record["event"] for record in records]
    if level == 0:
        assert not events
        return
    assert events.count("action_rejected") == events.count("execution_blocked") == 1
    assert records[0]["reason"] == "hardware_disabled"
    if level == 1:
        assert events == ["action_rejected", "execution_blocked"]
        return
    ordered = [
        "start_accepted",
        "schedule_armed",
        "delay_elapsed",
        "window_active",
        "positive_requested",
        "positive_accepted",
        "positive_outcome",
        "permission_changed",
        "execution_blocked",
        "duration_expired",
        "timing_retired",
    ]
    assert [event for event in events if event in ordered] == ordered
    active = next(record for record in records if record["event"] == "window_active")
    assert (active["start_at"], active["end_at"], active["transaction"]) == (
        1120,
        1300,
        True,
    )


async def test_delayed_editor_write_crossing_expiry_saves_only_the_new_draft(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        await editing(settings, "begin")
        persist = settings.persist
        entered, complete = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            if not entered.is_set() and profiles[settings.key]["grid_duration"] == 120:
                entered.set()
                await complete.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        update = asyncio.create_task(editing(settings, "update", value="00:02"))
        async with asyncio.timeout(5):
            await entered.wait()
        await clock.advance(60)
        assert not settings.get("charge_release")
        complete.set()
        await update
        await ha.async_block_till_done()
        stored = (await settings.store.async_load())[settings.key]
        assert stored["grid_duration"] == 120 and stored["grid_start_delay"] is None
        assert stored["grid_approved"]["grid_duration"] is None
        assert not stored["charge_release"] and not stored["grid_request"]
        assert not stored["grid_timing_edit"] and station.amps == 0


async def test_runtime_close_drains_and_rejects_inflight_explicit_start(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, _ = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:03")
        persist = settings.persist
        entered, complete = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            if profiles[settings.key]["charge_release"]:
                entered.set()
                await complete.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        start = asyncio.create_task(invoke(ha, entry, context(grid)))
        async with asyncio.timeout(5):
            await entered.wait()
        close = asyncio.create_task(async_unload_entry(ha, entry))
        async with asyncio.timeout(5):
            from conftest import eventually

            await eventually(lambda: grid.closed)
        assert not close.done()
        complete.set()
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await start
        await close
    await ha.install(entry)
    settings = entry.runtime_data.settings
    assert not settings.get("charge_release") and not settings.get("grid_request")
    assert (
        settings.get("grid_start_delay") == 60 and settings.get("grid_duration") == 180
    )


async def test_direct_total_duration_equal_to_last_remaining_edit_is_meaningful(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        clock.now = 1255
        await editing(settings, "begin")
        await settings.edit_timing(
            "grid_duration",
            "update",
            "card-a",
            settings.get("grid_timing_revision"),
            "00:16",
            basis="00:15",
        )
        await grid.action()
        assert settings.get("grid_request")["end_at"] == 2260
        assert settings.approval_attributes()["grid_approved_duration"] == 1260
        await settings.set("grid_duration", "00:16")
        assert grid.action_state()["charge_action"] == "apply"
        await grid.action()
        assert settings.get("grid_request")["end_at"] == 1960
        assert settings.approval_attributes()["grid_approved_duration"] == 960


async def test_reenable_during_active_window_uses_original_end(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        control = await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:02")
        await session(station)
        await grid.action()
        original = deepcopy(settings.get("grid_request"))
        assert await control.request("ChargingEnabled", "false") == "confirmed"
        await clock.advance(80)
        await ha.async_block_till_done()
        assert not any(offered(station)) and grid.phase()[0] == "active"
        assert await control.request("ChargingEnabled", "true") == "confirmed"
        if grid.task:
            await grid.task
        assert station.amps == 6 and settings.get("grid_request") == original
        await clock.advance(100)
        await ha.async_block_till_done()
        assert not settings.get("charge_release")
        assert station.amps == 0
