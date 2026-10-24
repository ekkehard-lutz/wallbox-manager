"""Reevaluate pending electrical equivalence only at affected stable events."""

import asyncio
from datetime import UTC, datetime

import pytest
from conftest import eventually
from ocpp.v21 import call
from test_active_control import ready
from test_grid_profile import (
    GridStation,
    ReviewedLockoutStation,
    pending_interim,
    start_grid,
)
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


async def approved_1400(entry, station):
    grid = await start_grid(entry, station)
    await eventually(lambda: grid.status == "confirmed")
    settings = entry.runtime_data.settings
    await settings.set("soll_power", 1.4)
    await settings._approve_release()
    await eventually(lambda: grid.status == "confirmed" and grid.requested.amps == 6)
    return grid, settings


async def test_pending_draft_becomes_equivalent_after_approximation_change(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 1.4)
        await settings._approve_release()
        await eventually(
            lambda: grid.status == "confirmed" and grid.requested.amps == 6
        )
        await settings.set("soll_power", 1.5)
        assert settings.approved("soll_power") == 1.4
        assert settings.approval_attributes()["grid_set_required"]
        count = len(station.profiles)
        operations = list(station.operations)
        await settings.set("power_approximation", "down")
        await eventually(lambda: grid.task.done())
        assert grid.requested.phases == 1 and grid.requested.amps == 6
        await eventually(lambda: settings.approved("soll_power") == 1.5)
        assert not settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count
        # The immediate approximation decision already reads the schedule;
        # adopting the equivalent numeric power adds no further OCPP request.
        assert station.operations == operations + ["GetCompositeSchedule"]


async def test_approximation_executes_only_approved_power_when_draft_remains_different(
    ha,
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("power_approximation", "down")
        await settings.set("soll_power", 1.5)
        await settings._approve_release()
        await eventually(lambda: grid.task.done())
        await settings.set("soll_power", 3)
        count = len(station.profiles)
        await settings.set("power_approximation", "up")
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        await asyncio.sleep(0)
        assert grid.requested.amps == 7 and grid.requested.phases == 1
        assert settings.approved("soll_power") == 1.5
        assert settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count + 1
        assert (
            station.profiles[-1][1]["charging_schedule"][0]["charging_schedule_period"][
                0
            ]["limit"]
            == 7
        )


@pytest.mark.parametrize(
    "source", ["user_cap", "station_max", "minimum", "phase_modes"]
)
async def test_capabilities_can_make_existing_draft_equivalent(ha, source):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        count = len(station.profiles)
        state = entry.runtime_data.state
        if source == "user_cap":
            await settings.set("allowed_current_1p", 6)
        else:
            scope, role, value = {
                "station_max": (state.binding, "maximum_asymmetry_current", 6),
                "minimum": (("Wallbox01", "1", None), "minimum_current", 7),
                "phase_modes": (("Wallbox01", "1", None), "supported_phases", "3"),
            }[source]
            state.observe(
                state.generation, ("capability", scope, role), value, "NotifyReport"
            )
        if source in ("minimum", "phase_modes"):
            await eventually(lambda: grid.task.done())
            await settings.initialize_power()
            await settings.approve_equivalent_grid()
            # The replacement is valid, but automatic initialization must not
            # turn it into an approved user edit, even at an equivalent point.
            assert settings.get("soll_power") == (1.6 if source == "minimum" else 4.1)
            assert settings.values["grid_power_initialized"] is True
            assert settings.approved("soll_power") == 1.4
            assert settings.approval_attributes()["grid_set_required"]
        else:
            await eventually(lambda: settings.approved("soll_power") == 1.5)
            assert not settings.approval_attributes()["grid_set_required"]
        # The immediate capability decision uses 1.4 kW; adoption adds no command.
        expected = 1 if source in ("minimum", "phase_modes") else 0
        assert len(station.profiles) == count + expected
        assert grid.confirmed.amps == (
            7 if source == "minimum" else 0 if source == "phase_modes" else 6
        )
        assert station.phases == 1


async def test_missing_capability_keeps_set_until_reliable_context_returns(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        state = entry.runtime_data.state
        key = ("capability", ("Wallbox01", "1", None), "supported_phases")
        state.observe(state.generation, key, None, "NotifyReport")
        await settings.set("power_approximation", "down")
        await eventually(lambda: grid.status == "unsupported" and grid.task.done())
        await asyncio.sleep(0)
        assert settings.approved("soll_power") == 1.4
        assert settings.approval_attributes()["grid_set_required"]
        count = len(station.profiles)
        state.observe(state.generation, key, "1,3", "NotifyReport")
        await eventually(lambda: settings.approved("soll_power") == 1.5)
        assert len(station.profiles) == count


async def test_timing_draft_blocks_automatic_adoption_after_context_change(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        await settings.set("grid_duration", "00:00")
        count = len(station.profiles)
        await settings.set("power_approximation", "down")
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        await asyncio.sleep(0)
        assert settings.approved("soll_power") == 1.4
        assert settings.approved("grid_duration") is None
        assert settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count


async def test_lockout_adoption_preserves_desired_and_original_retry_deadline(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, clock = await pending_interim(entry, station)
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 1.6)
        assert settings.approval_attributes()["grid_set_required"]
        count = len(station.profiles)
        await settings.set("power_approximation", "nearest")
        await eventually(lambda: settings.approved("soll_power") == 1.6)
        assert grid.requested.phases == 1 and grid.requested.amps == 7
        assert grid.confirmed.phases == 3 and grid.confirmed.amps == 0
        assert grid.status == "phase_lockout_pending"
        assert grid.retry_deadline == clock.now + 6
        assert not settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count + 1
        station.locked, station.reenable = False, True
        await clock.advance(6)
        await eventually(lambda: grid.reason == "ReenableLockout")
        assert (
            grid.retry_at == 14
            and not settings.approval_attributes()["grid_set_required"]
        )


async def test_uncertain_point_waits_for_existing_supported_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        await settings.set("power_approximation", "down")
        await settings._approve_release()
        await eventually(lambda: grid.task.done())
        await settings.set("soll_power", 1.6)
        station.readback = False
        await settings.set("power_approximation", "up")
        await eventually(
            lambda: grid.status == "accepted_unconfirmed" and grid.task.done()
        )
        assert grid.point_uncertain
        assert settings.approved("soll_power") == 1.5
        assert settings.approval_attributes()["grid_set_required"]
        count = len(station.profiles)
        station.readback = True
        grid.launch(confirm_only=True)
        await eventually(lambda: settings.approved("soll_power") == 1.6)
        assert grid.confirmed.amps == 7 and len(station.profiles) == count
        assert not grid.point_uncertain


@pytest.mark.parametrize("fail", [False, True])
async def test_reevaluation_queued_behind_set_keeps_atomic_timing_approval(
    ha, monkeypatch, fail
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        await settings.set("grid_duration", "00:05")
        waiting, release = asyncio.Event(), asyncio.Event()
        save = settings.store.async_save
        writes = []

        async def delayed(data):
            writes.append(data)
            waiting.set()
            await release.wait()
            if fail:
                raise OSError("SET write failed")
            await save(data)

        monkeypatch.setattr(settings.store, "async_save", delayed)
        approval = asyncio.create_task(settings._approve_release())
        await waiting.wait()
        state = entry.runtime_data.state
        state.observe(
            state.generation,
            ("capability", state.binding, "maximum_asymmetry_current"),
            6,
            "NotifyReport",
        )
        await eventually(lambda: grid.task.done())
        await asyncio.sleep(0)
        assert settings.approved("soll_power") == 1.4
        assert settings.approved("grid_duration") is None
        release.set()
        if fail:
            with pytest.raises(OSError, match="SET write failed"):
                await approval
        else:
            await approval
        await asyncio.sleep(0)
        async with settings.lock:
            assert len(writes) == 1
            assert settings.approved("soll_power") == (1.4 if fail else 1.5)
            assert settings.approved("grid_duration") == (None if fail else 300)
            assert settings.approval_attributes()["grid_set_required"] is fail
        assert settings.values.get("grid_request") is None


async def test_restart_retains_draft_until_current_generation_point_is_confirmed(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
    await settings.set("power_approximation", "down")
    assert settings.approved("soll_power") == 1.4
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    settings, grid = entry.runtime_data.settings, entry.runtime_data.grid
    assert settings.approved("soll_power") == 1.4
    assert settings.approval_attributes()["grid_set_required"]
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="Rxx")
        station.amps, station.phases = 6, 1
        await ready(entry, station)
        assert settings.approved("soll_power") == 1.4
        await station.call(
            call.TransactionEvent(
                event_type="Updated",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="TxResumed",
                seq_no=1,
                transaction_info={
                    "transaction_id": "net-test",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        await eventually(lambda: grid.task and grid.task.done())
        assert settings.get("charge_release") is False
        assert settings.approved("soll_power") == 1.4
        assert grid.confirmed.amps == 0 and len(station.profiles) == 1
        assert station.profiles[0][1]["charging_schedule"][0][
            "charging_schedule_period"
        ] == [{"start_period": 0, "limit": 0}]
        await grid.action()
        assert settings.approved("soll_power") == 1.5
        assert settings.get("charge_release") is True


async def test_repeated_notifications_do_not_reapprove_or_resend(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        save = settings.store.async_save
        writes = []

        async def record(data):
            writes.append(data)
            await save(data)

        monkeypatch.setattr(settings.store, "async_save", record)
        count = len(station.profiles)
        await settings.set("power_approximation", "down")
        await eventually(lambda: settings.approved("soll_power") == 1.5)
        revision = settings.approval_attributes()["grid_approved_revision"]
        assert len(writes) == 2  # immediate direction + one numeric approval
        operations = list(station.operations)
        for _ in range(5):
            grid.state.publish(
                ("capability", ("Wallbox01", "1", None), "supported_phases")
            )
            grid.phase_changed()
        await asyncio.sleep(0)
        async with settings.lock:
            assert settings.approval_attributes()["grid_approved_revision"] == revision
            assert len(writes) == 2
        assert len(station.profiles) == count
        assert station.operations == operations


@pytest.mark.parametrize("fail", [False, True])
async def test_automatic_adoption_waits_for_durable_save_and_recovers_on_event(
    ha, monkeypatch, fail
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        waiting, release = asyncio.Event(), asyncio.Event()
        save = settings.store.async_save
        writes = []

        async def delayed(data):
            if data[settings.key]["grid_approved"]["power_kw"] == 1.5:
                writes.append(data)
                waiting.set()
                await release.wait()
                if fail:
                    raise OSError("Equivalent approval write failed")
            await save(data)

        monkeypatch.setattr(settings.store, "async_save", delayed)
        count = len(station.profiles)
        await settings.set("power_approximation", "down")
        await waiting.wait()
        assert settings.approved("soll_power") == 1.4
        assert settings.approval_attributes()["grid_set_required"]
        release.set()
        if fail:
            await eventually(lambda: settings.approval_error is not None)
            assert settings.approved("soll_power") == 1.4
            assert settings.approval_attributes()["grid_set_required"]
            assert "Equivalent approval write failed" in settings.approval_error
            # A failed save does not schedule its own retry or send a command.
            await asyncio.sleep(0)
            assert len(writes) == 1 and len(station.profiles) == count
            monkeypatch.setattr(settings.store, "async_save", save)
            grid.phase_changed()
        await eventually(lambda: settings.approved("soll_power") == 1.5)
        assert not settings.approval_attributes()["grid_set_required"]
        assert settings.approval_error is None
        assert len(station.profiles) == count
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    settings = entry.runtime_data.settings
    assert settings.approved("soll_power") == 1.5
    assert not settings.approval_attributes()["grid_set_required"]
