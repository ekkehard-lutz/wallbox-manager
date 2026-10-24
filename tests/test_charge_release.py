"""Operator release, shared action state and stale-positive fencing over OCPP."""

import asyncio

import pytest
from conftest import entities, eventually
from homeassistant.exceptions import HomeAssistantError
from test_active_control import event, ready
from test_grid_profile import FakeClock, GridStation, start_grid
from test_protocol import peer


def action_button(ha, entry):
    return next(e for e in entities(ha, entry) if e.role == "charge_action")


async def test_start_apply_second_press_stop_and_hardware_independence(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        button = action_button(ha, entry)
        assert grid.action_state()["charge_action"] == "stop"
        await settings.set("soll_power", 5)
        assert grid.action_state()["charge_action"] == "apply"
        count = len(station.profiles)
        await button.async_press()
        assert settings.get("charge_release") is True
        assert settings.approved("soll_power") == 5
        assert grid.action_state()["charge_action"] == "stop"
        assert len(station.profiles) == count + 1
        await button.async_press()
        assert settings.get("charge_release") is False
        assert station.amps == 0
        assert station.values["ChargingEnabled"] == "true"
        assert not any(
            w["variable"]["name"] == "ChargingEnabled" for w in station.writes
        )


async def test_revert_equivalent_pending_parameters_restores_stop(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 5)
        assert grid.action_state()["charge_action"] == "apply"
        await settings.set("soll_power", 11.04)
        assert grid.action_state()["charge_action"] == "stop"
        await settings.set("grid_duration", "00:01")
        assert grid.action_state()["charge_action"] == "apply"
        await settings.set("grid_duration", "")
        assert grid.action_state()["charge_action"] == "stop"


async def test_profile_switch_resets_release_but_same_selection_does_not(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        await settings.set("charging_profile", "NETZ")
        assert settings.get("charge_release") is True
        await settings.set("charging_profile", "PV_SURPLUS")
        await eventually(lambda: station.amps == 0)
        assert settings.get("charge_release") is False
        assert station.values["ChargingEnabled"] == "true"
        # Foundation only: PV can be released at zero without a regulator.
        await grid.action()
        assert settings.get("charge_release") is True and station.amps == 0
        assert grid.action_state()["charge_action"] == "stop"
        await settings.set("charging_profile", "PV_OPTIMUM")
        assert settings.get("charge_release") is False
        await settings.set("charging_profile", "NETZ")
        assert settings.get("charge_release") is False


async def test_positive_slider_point_can_quantize_to_zero_and_cannot_start(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        # Establish transaction then stop. A gap between eligible phase ranges
        # makes a positive 2.8 kW resolve to 0 A on the selected 3p DOWN mode.
        # Use the common helper on a fresh connection below instead.
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.action()
        settings = entry.runtime_data.settings
        await settings.set("allowed_current_1p", 6)
        await settings.set("power_approximation", "down")
        await settings.set("soll_power", 2.8)
        assert grid.draft_point().amps == 0
        assert grid.action_state()["charge_action_ready"] is False
        with pytest.raises(HomeAssistantError, match="no_positive_current"):
            await action_button(ha, entry).async_press()
        assert settings.get("charge_release") is False


async def test_local_loss_blocks_release_without_unauthorized_zero_or_takeover(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        count = len(station.profiles)
        event(entry, "ControlAuthority", "Local")
        await asyncio.sleep(0)
        assert entry.runtime_data.settings.get("charge_release") is False
        await grid.stop_release()
        assert len(station.profiles) == count
        assert grid.action_state()["charge_action_ready"] is False
        assert station.amps > 0  # Never claim local charging stopped.
        assert [w["variable"]["name"] for w in station.writes] == ["ControlAuthority"]


async def test_queued_positive_is_fenced_by_stop_and_multiple_cards_coalesce(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        adapter = entry.runtime_data.server.adapter
        await settings.set("soll_power", 5)
        count = len(station.profiles)
        async with adapter._call_lock:
            apply = asyncio.create_task(grid.action())
            await eventually(lambda: grid.status == "pending")
            stop = asyncio.create_task(grid.stop_release())
            await eventually(lambda: settings.get("charge_release") is False)
        await asyncio.gather(apply, stop)
        await eventually(lambda: station.amps == 0)
        assert all(
            p[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
            for p in station.profiles[count:]
        )
        # One concurrent explicit action, one durable start, no accidental stop.
        await asyncio.gather(grid.action(), grid.action())
        assert settings.get("charge_release") is True


async def test_start_delay_approval_is_atomic_and_apply_preserves_deadlines(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.action()
        settings = entry.runtime_data.settings
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await settings.set("grid_start_delay", "00:01")
        await settings.set("grid_duration", "00:02")
        await grid.action()
        request = dict(settings.values["grid_request"])
        assert station.amps == 0 and request == {"start_at": 1060, "end_at": 1180}
        stored = (await settings.store.async_load())[settings.key]
        assert stored["charge_release"] is True and stored["grid_request"] == request
        await settings.set("soll_power", 5)
        await grid.action()
        assert settings.values["grid_request"] == request
        await clock.advance(60)
        await eventually(lambda: station.amps > 0)
        await clock.advance(120)
        await eventually(lambda: settings.get("charge_release") is False)
        assert station.values["ChargingEnabled"] == "true"


async def test_failed_start_approval_never_releases_or_dispatches(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.action()
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 5)
        count = len(station.profiles)
        original = settings.approved("soll_power")

        async def fail(_):
            raise OSError("Disk unavailable")

        monkeypatch.setattr(settings, "persist", fail)
        with pytest.raises(HomeAssistantError, match="Disk unavailable"):
            await action_button(ha, entry).async_press()
        assert settings.get("charge_release") is False
        assert settings.approved("soll_power") == original
        assert len(station.profiles) == count


@pytest.mark.parametrize(
    "modes,caps,minimum,step,volts,expected",
    [
        ((1, 3), {1: 20, 3: 27}, 6, 1, 230, (1.38, 18.63)),
        ((1, 3), {1: 20, 3: 0}, 6, 1, 240, (1.44, 4.8)),
        ((1, 2, 3), {1: 0, 2: 12, 3: 0}, 7, 2, 220, (3.08, 4.84)),
        ((1, 2, 3), {1: 0, 2: 0, 3: 0}, 6, 1, 230, None),
    ],
)
def test_editor_bounds_use_solver_envelopes(
    modes, caps, minimum, step, volts, expected
):
    from test_grid_profile import ranges

    assert (
        ranges(
            modes=modes, caps=caps, minimum=minimum, step=step, volts=volts
        ).power_bounds()
        == expected
    )


async def test_hardware_permission_never_dispatches_or_changes_manager_release(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        count = len(station.profiles)
        point = station.amps, station.phases
        for value in ("false", "true"):
            assert (
                await entry.runtime_data.server.control.request(
                    "ChargingEnabled", value
                )
                == "confirmed"
            )
            await asyncio.sleep(0)
            assert settings.get("charge_release") is True
            assert (station.amps, station.phases) == point
            assert len(station.profiles) == count
        await grid.action()


async def test_authority_loss_during_durable_start_cannot_publish_positive_intent(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.action()
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 5)
        approved = dict(settings.values["grid_approved"])
        original = settings.persist
        entered, release = asyncio.Event(), asyncio.Event()
        count = len(station.profiles)

        async def delayed(data):
            if data[settings.key].get("charge_release"):
                entered.set()
                await release.wait()
            await original(data)

        monkeypatch.setattr(settings, "persist", delayed)
        start = asyncio.create_task(grid.action())
        await entered.wait()
        event(entry, "ControlAuthority", "Local")
        release.set()
        with pytest.raises(ValueError, match="Superseded"):
            await start
        assert settings.get("charge_release") is False
        assert settings.values["grid_approved"] == approved
        assert len(station.profiles) == count


async def test_apply_can_pause_at_zero_without_withdrawing_release(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        await settings.set("allowed_current_1p", 6)
        await settings.set("power_approximation", "down")
        await settings.set("soll_power", 2.8)
        assert grid.action_state()["charge_action"] == "apply"
        assert grid.action_state()["charge_action_ready"] is True
        await grid.action()
        assert station.amps == 0 and settings.get("charge_release") is True
        assert grid.action_state()["charge_action"] == "stop"
        await grid.action()
        assert settings.get("charge_release") is False


async def test_stop_still_requests_zero_when_positive_capability_is_missing(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        state = entry.runtime_data.state
        state.observe(
            state.generation,
            ("capability", ("Wallbox01", "1", None), "minimum_current"),
            None,
            "test",
        )
        await grid.stop_release()
        await eventually(lambda: station.amps == 0)
        assert entry.runtime_data.settings.get("charge_release") is False
        assert station.values["ChargingEnabled"] == "true"
