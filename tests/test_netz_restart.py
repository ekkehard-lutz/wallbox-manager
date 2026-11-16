"""Approved NETZ schedule restart and current-generation Stationary reconciliation."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import eventually
from ocpp.v21 import call
from test_active_control import event, ready
from test_grid_battery_restart import connector, transaction
from test_grid_profile import FakeClock, GridStation, start_grid
from test_protocol import peer
from test_release_recovery import periods, session

from custom_components.wallbox_manager import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.wallbox_manager import (
    grid as grid_module,
)


def station_offer(station, amps, *, phase="RST", enabled="true", authority="OCPP"):
    station.amps, station.phases = amps, 3
    station.values.update(
        ControlAuthority=authority, ChargingEnabled=enabled, PhaseRotation=phase
    )


@pytest.mark.parametrize("duration", ["00:03", ""])
@pytest.mark.parametrize("actual", [16, 10])
async def test_ha_restart_recovers_only_durable_approval_and_absolute_deadline(
    ha, monkeypatch, duration, actual
):
    clock = FakeClock()
    monkeypatch.setattr(grid_module, "time", SimpleNamespace(time=lambda: clock.now))
    entry = await ha.install()
    first = entry.runtime_data
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await first.settings.set("grid_duration", duration)
        if duration:
            await grid.action()
        request = dict(first.settings.get("grid_request"))
        assert request["start_at"] == clock.now
        assert request["end_at"] == (clock.now + 180 if duration else None)
        await first.settings.set("soll_power", 7)
        # The Home Assistant stop callback uses close() without release retirement.
        await first.close()

    await clock.advance(60)
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    runtime = entry.runtime_data
    grid, settings = runtime.grid, runtime.settings
    grid.call_later = clock.later
    grid.schedule()
    assert settings.get("grid_request") == request
    assert settings.approved("soll_power") == 11 and settings.get("soll_power") == 7

    async with peer(entry, station_type=GridStation) as station:
        station_offer(station, actual)
        await ready(entry, station)
        assert not station.profiles
        await session(station, resumed=True)
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        assert grid.current_point().amps == 16 and not grid.recovering
        assert [p["limit"] for p in periods(station)] == ([16] if actual != 16 else [])
        assert not station.writes
        await clock.advance(120)
        if duration:
            await eventually(
                lambda: not settings.get("charge_release") and station.amps == 0
            )
            assert settings.get("grid_request") is None
        else:
            assert settings.get("charge_release") and station.amps == 16


@pytest.mark.parametrize(
    "plugged_at_restart,downtime", [(False, 60), (True, 60), (True, 121)]
)
async def test_ha_restart_during_delay_reuses_start_and_end(
    ha, monkeypatch, plugged_at_restart, downtime
):
    clock = FakeClock()
    monkeypatch.setattr(grid_module, "time", SimpleNamespace(time=lambda: clock.now))
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        if plugged_at_restart:
            await session(station)
        runtime = entry.runtime_data
        await runtime.settings.set("soll_power", 11)
        await runtime.settings.set("grid_start_delay", "00:02")
        await runtime.settings.set("grid_duration", "00:03")
        await runtime.grid.action()
        request = dict(runtime.settings.get("grid_request"))
        assert request["start_at"] == 1120 and request["end_at"] == 1300
        assert not station.profiles
        await runtime.close()

    await clock.advance(downtime)
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    grid = entry.runtime_data.grid
    grid.call_later = clock.later
    grid.schedule()
    async with peer(entry, station_type=GridStation) as station:
        station_offer(station, 0)
        await ready(entry, station)
        if plugged_at_restart:
            await session(station, resumed=True)
        else:
            await connector(station, "Available")
        if downtime < 120:
            assert grid.phase() == ("waiting", 1120)
            await clock.advance(59)
            assert not station.profiles
            if not plugged_at_restart:
                await connector(station, "Occupied")
                await session(station)
            await clock.advance(1)
        await eventually(lambda: station.amps == 16)
        assert grid.settings.get("grid_request") == request
        assert [p["limit"] for p in periods(station)] == [16]


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "manual_zero",
        "attempted_delay",
        "manual_end",
        "local",
        "disabled",
        "stopped",
        "damaged",
        "tentative",
        "unknown",
        "new_transaction",
    ],
)
async def test_ha_restart_never_replays_ambiguous_or_retired_positive_intent(
    ha, monkeypatch, case
):
    clock = FakeClock()
    monkeypatch.setattr(grid_module, "time", SimpleNamespace(time=lambda: clock.now))
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        runtime = entry.runtime_data
        if case == "expired":
            await runtime.settings.set("grid_duration", "00:01")
            await grid.action()
        if case == "attempted_delay":
            await runtime.settings.set("grid_start_delay", "00:01")
            await grid.action()
            grid.call_later = clock.later
            grid.schedule()
            await clock.advance(60)
            await eventually(lambda: station.amps == 16 and grid.task.done())
            assert runtime.settings.get("grid_started_window") == [1000, 1060]
        if case == "stopped":
            await grid.stop_release()
        await runtime.close()
    if case in ("damaged", "tentative"):
        settings = runtime.settings
        saved = await settings.store.async_load()
        if case == "damaged":
            saved[settings.key]["grid_request"]["start_at"] = "bad"
        else:
            saved[settings.key]["grid_recoverable"] = False
        await settings.store.async_save(saved)
    await clock.advance(61)
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)

    async with peer(entry, station_type=GridStation) as station:
        station_offer(
            station,
            0 if case in {"manual_zero", "attempted_delay"} else 16,
            enabled="false" if case == "disabled" else "true",
            authority="Local" if case == "local" else "OCPP",
        )
        station.readback = case != "unknown"
        control = await ready(entry, station)
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        if case == "new_transaction":
            await station.call(
                call.TransactionEvent(
                    event_type="Started",
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason="CablePluggedIn",
                    seq_no=0,
                    transaction_info={
                        "transaction_id": "different",
                        "charging_state": "EVConnected",
                    },
                    evse={"id": 1, "connector_id": 1},
                )
            )
        elif case == "manual_end":
            await station.call(
                call.TransactionEvent(
                    event_type="Ended",
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason="RemoteStop",
                    seq_no=1,
                    transaction_info={
                        "transaction_id": "net-test",
                        "charging_state": "Idle",
                        "stopped_reason": "Remote",
                    },
                    evse={"id": 1, "connector_id": 1},
                )
            )
        else:
            await session(station, resumed=True)
        if case == "local":
            assert not settings.get("charge_release") and not station.profiles
            await control.request("ControlAuthority", "OCPP")
        if case != "manual_end":
            await eventually(lambda: grid.task and grid.task.done())
        await ha.async_block_till_done()
        assert all(p["limit"] == 0 for p in periods(station))
        if case != "local":
            assert not station.writes
        assert settings.get("charge_release") == (
            case in {"unknown", "manual_zero", "attempted_delay", "new_transaction"}
        )
        if case in {"expired", "stopped", "damaged", "tentative"}:
            assert periods(station) == [{"start_period": 0, "limit": 0}]


@pytest.mark.parametrize("transition", ["reconnect", "boot"])
@pytest.mark.parametrize("actual", [16, 10, None])
async def test_station_recovery_rechecks_actual_on_new_evidence(ha, transition, actual):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        old_generation = grid.state.generation
        if transition == "boot":
            await recover_station(entry, station, grid, old_generation, actual)
            return

    async with peer(entry, station_type=GridStation) as station:
        await recover_station(entry, station, grid, old_generation, actual)


async def recover_station(entry, station, grid, old_generation, actual):
    # Reproduce a temporarily unavailable phase Actual during inventory and
    # a transient StateUnavailable offer read before physical startup settles.
    station_offer(station, actual or 0, phase="")
    station.readback = False
    count = len(station.profiles)
    await ready(entry, station)
    assert grid.state.generation != old_generation
    assert grid.attributes()["applied_current_a"] is None
    await session(station, resumed=True)
    await eventually(lambda: grid.status == "unconfirmed" and grid.task.done())
    assert grid.attributes()["applied_current_a"] is None
    station.readback = actual is not None
    station.apply_profile = False
    station.values["PhaseRotation"] = "RST"
    await transaction(station, "Updated", power=6400, sequence=2)
    await eventually(lambda: grid.task.done())
    assert grid.attributes()["applied_current_a"] == actual
    assert grid.attributes()["requested_current_a"] == 16
    assert len(station.profiles) == count + (actual == 10)


async def test_late_composite_response_cannot_recover_prior_boot(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        adapter = entry.runtime_data.server.adapter
        wait, release = asyncio.Event(), asyncio.Event()
        request = adapter.request

        async def delayed(payload):
            response = await request(payload)
            if isinstance(payload, call.GetCompositeSchedule) and not wait.is_set():
                wait.set()
                await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        grid.launch()
        await asyncio.wait_for(wait.wait(), 5)
        previous = grid.task
        station_offer(station, 0)
        station.readback = False
        await ready(entry, station)
        await session(station, resumed=True)
        await eventually(lambda: grid.task is not previous and grid.task.done())
        release.set()
        await previous
        assert grid.attributes()["applied_current_a"] is None
        assert [p["limit"] for p in periods(station)] == [16]


@pytest.mark.parametrize("change", ["same_generation", "boot"])
async def test_owned_reserve_survives_missing_point_then_restores_on_permission_loss(
    ha, change
):
    from test_grid_battery import battery

    _, writes, entry = await battery(ha)
    assert await async_setup_entry(ha, entry)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.settings.set("min_soc", 40)
        await transaction(station, "Updated", power=6400)
        await eventually(lambda: writes == [40])
        if change == "boot":
            station_offer(station, 16)
            station.readback = False
            await ready(entry, station)
            await transaction(station, "Updated", power=6400, sequence=2)
        else:
            grid.confirmed = None
            grid.publish()
        await ha.async_block_till_done()
        assert grid.current_point() is None and writes == [40]
        assert not grid.recovering
        event(entry, "ChargingEnabled", "false")
        await eventually(lambda: writes == [40, 20])


async def test_reconnected_confirmed_phase_takes_precedence_over_old_approved_hint(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = grid.settings
        await settings.set("soll_power", 3.9)
        await grid.action()
        await settings.step_power(1, settings.values["grid_draft_revision"])
        await grid.action()
        assert settings.approved("power_phases") == station.phases == 1
        assert settings.approved("soll_power") == 4.1

    async with peer(entry, station_type=GridStation) as station:
        station_offer(station, 6)
        await ready(entry, station)
        await session(station, resumed=True)
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        assert grid.current_point().phases == grid.requested.phases == 3
        assert settings.approved("power_phases") == 1
        assert not station.profiles


async def test_reconnect_permission_gap_preserves_physically_running_offer(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)

    async with peer(entry, station_type=GridStation) as station:
        station_offer(station, 16)
        station.fail_reads.add("ChargingEnabled")
        await ready(entry, station)
        assert grid.hardware_enabled() is None
        await session(station, resumed=True)
        await eventually(lambda: grid.task and grid.task.done())
        assert grid.current_point().amps == station.amps == 16
        assert grid.reason == "hardware_unavailable"
        assert not station.profiles
        event(entry, "ChargingEnabled", "true")
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        assert grid.current_point().amps == station.amps == 16
        assert not station.profiles
