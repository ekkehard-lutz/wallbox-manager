"""Cross-profile fences for the two independently reviewed beta.22 features."""

import asyncio
import logging
from copy import deepcopy

import pytest
from homeassistant.exceptions import HomeAssistantError
from ocpp.v21 import call
from test_active_control import ready
from test_live_netz_timing import configure, editing
from test_protocol import peer
from test_pv_phase_lockout import (
    PhaseStation,
    establish,
    pending,
    periods,
    records,
    setup,
)
from test_pv_runtime import install, tick
from test_release_recovery import session
from test_safe_charge_actions import context, invoke


async def settled(grid):
    if grid.task:
        await grid.task


@pytest.mark.parametrize("phase", ["waiting", "active"])
async def test_netz_profile_switch_retires_schedule_but_preserves_unapproved_edit(
    ha, phase
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        grid.wall_time, grid.call_later = lambda: station.clock.now, station.clock.later
        await settings.initialize_power()
        await session(station)
        await configure(settings, "00:01", "00:02")
        await invoke(ha, entry, context(grid))
        if phase == "active":
            await station.clock.advance(60)
            await ha.async_block_till_done()
            await settled(grid)
        assert grid.phase()[0] == phase
        await editing(settings, "begin")
        await editing(settings, "update", value="00:04")
        stale_apply = context(grid)
        assert stale_apply["action"] == "apply"
        stale_timer = grid.schedule_timer
        assert stale_timer
        await settings.set("charging_profile", "PV_OPTIMUM")
        await settled(grid)
        assert grid.action_state()["charge_action"] == "start"
        assert not settings.get("charge_release") and not settings.get("grid_request")
        assert settings.get("grid_start_delay") is None
        assert settings.get("grid_duration") == 240
        assert settings.get("grid_timing_edit") is None
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, stale_apply)
        count = len(station.profiles)
        stale_timer.callback()  # Deliver even though retirement canceled it.
        await station.clock.advance(180)
        await ha.async_block_till_done()
        await tick(entry.runtime_data.pv)
        assert len(station.profiles) == count and station.amps == 0
        await invoke(ha, entry, context(grid))
        assert settings.get("charge_release") and not settings.get("grid_request")
        assert len(station.profiles) == count  # PV authorization is preparatory.


@pytest.mark.parametrize("lose_permission", [False, True])
async def test_pv_phase_wait_cannot_follow_a_stop_into_netz(ha, lose_permission):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, _ = await pending(entry, station)
        settings = grid.settings
        old_deadline = grid.pv_phase_deadline
        before = len(station.profiles)
        control = grid.listener.control
        if lose_permission:
            assert await control.request("ChargingEnabled", "false") == "confirmed"
            assert grid.pv_phase_deadline is None and grid.pv_ack is None
            assert len(station.profiles) == before
        assert grid.action_state()["charge_action"] == "stop"
        assert grid.action_state()["charge_action_ready"]
        await invoke(ha, entry, context(grid))
        await settled(grid)
        assert periods(station, before) == [(None, 0)]
        assert grid.pv_phase_deadline is None and not settings.get("charge_release")
        await settings.set("charging_profile", "NETZ")
        await settled(grid)
        await settings.set("soll_power", 1.4)
        await configure(settings, "00:01", "00:02")
        if lose_permission:
            assert (
                grid.action_state()["charge_action_blocked_reason"]
                == "hardware_disabled"
            )
            with pytest.raises(HomeAssistantError, match="hardware_disabled"):
                await invoke(ha, entry, context(grid))
            assert not settings.get("grid_request")
            assert await control.request("ChargingEnabled", "true") == "confirmed"
        await invoke(ha, entry, context(grid))
        schedule = deepcopy(settings.get("grid_request"))
        count = len(station.profiles)
        await station.clock.advance(old_deadline - station.clock.now)
        assert grid.phase()[0] == "waiting"
        assert len(station.profiles) == count
        await station.clock.advance(schedule["start_at"] - station.clock.now)
        await ha.async_block_till_done()
        await settled(grid)
        assert periods(station, count) == [(1, 6)]
        assert settings.get("grid_request") == schedule and grid.pv_ack is None


async def test_late_pv_rejection_does_not_change_new_netz_schedule(ha, monkeypatch):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        settings = grid.settings
        await establish(grid, pv, station, 1, 20)
        station.track, station.until = True, 1033
        entered, resume = asyncio.Event(), asyncio.Event()
        request = grid.listener.adapter.request

        async def delay_rejection(payload):
            response = await request(payload)
            if (
                isinstance(payload, call.SetChargingProfile)
                and response.status == "Rejected"
            ):
                entered.set()
                await resume.wait()
            return response

        monkeypatch.setattr(grid.listener.adapter, "request", delay_rejection)
        old = asyncio.create_task(tick(pv))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            await settings.set("charging_profile", "NETZ")
            await settled(grid)
            await configure(settings, "00:02", "00:01")
            await invoke(ha, entry, context(grid))
            schedule = deepcopy(settings.get("grid_request"))
            count = len(station.profiles)
        finally:
            resume.set()
            await old
        assert grid.pv_phase_deadline is None
        assert settings.get("grid_request") == schedule and grid.phase()[0] == "waiting"
        await station.clock.advance(60)
        assert len(station.profiles) == count


async def test_pv_readback_timeout_and_late_confirmation_do_not_cross_into_netz(
    ha, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        settings = grid.settings
        await establish(grid, pv, station, 1, 20)
        request = grid.listener.adapter.request
        reads = 0

        async def timeout_after_ack(payload):
            nonlocal reads
            if isinstance(payload, call.GetCompositeSchedule):
                reads += 1
                if reads == 2:
                    raise TimeoutError
            return await request(payload)

        caplog.clear()
        with monkeypatch.context() as patch:
            patch.setattr(grid.listener.adapter, "request", timeout_after_ack)
            await tick(pv)
        assert grid.status == "accepted_unconfirmed" and grid.point_uncertain
        assert grid.pv_ack is not None
        trace = [r for r in records(caplog) if r["event"] == "pv_execution"]
        assert trace[-1]["command_reason"] == "readback_failure"
        assert trace[-1]["acknowledged_point"] == [3, 7]
        assert trace[-1]["confirmed_point"] is None
        await settings.set("charging_profile", "NETZ")
        await settled(grid)
        assert grid.pv_ack is None and station.amps == 0
        await configure(settings, "00:01", "00:01")
        await invoke(ha, entry, context(grid))
        schedule = deepcopy(settings.get("grid_request"))
        grid.launch(confirm_only=True)
        await settled(grid)
        assert not any(
            r["event"] == "pv_execution" and r["command_status"] == "confirmed"
            for r in records(caplog)
        )
        assert settings.get("grid_request") == schedule
        assert grid.phase()[0] == "waiting"
