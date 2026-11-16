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


async def test_netz_local_drafts_and_both_start_paths(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        await settings.set("soll_power", 3)
        await settings.set("grid_start_delay", "00:02")
        approved = dict(settings.values["grid_approved"])
        assert (
            grid.action_state()["charge_action_blocked_reason"] == "control_unavailable"
        )
        with pytest.raises(HomeAssistantError, match="control_unavailable"):
            await action_button(ha, entry).async_press()
        with pytest.raises(HomeAssistantError, match="control_unavailable"):
            await ha.services.async_call(
                "wallbox_manager",
                "charge_action",
                {
                    "entity_id": action_button(ha, entry).entity_id,
                    "action": "start",
                    "profile": "NETZ",
                    "revision": grid.action_revision(),
                    "incarnation": grid.state.runtime_id,
                },
                blocking=True,
            )
        assert settings.values["grid_approved"] == approved
        assert not settings.get("charge_release") and not station.writes
        assert not (await settings.store.async_load())[settings.key].get(
            "charge_release"
        )
        await control.request("ControlAuthority", "OCPP")
        assert grid.action_state()["charge_action_ready"]
        assert not settings.get("charge_release")
        await grid.explicit_action(
            "start", "NETZ", grid.action_revision(), grid.state.runtime_id
        )
        assert settings.get("charge_release") and grid.phase()[0] == "waiting"
        assert not station.profiles


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
        assert (
            grid.action_state()["charge_action_blocked_reason"] == "control_unavailable"
        )
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
        assert station.amps == 0
        assert request["epoch_at"] == 1000
        assert request["start_at"] == 1060 and request["end_at"] == 1180
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
    "modes,caps,minimum,step,expected",
    [
        ((1, 3), {1: 20, 3: 27}, 6, 1, (1.4, 18.6)),
        ((1, 3), {1: 20, 3: 0}, 6, 1, (1.4, 4.6)),
        ((1, 2, 3), {1: 0, 2: 12, 3: 0}, 7, 2, (3.2, 5.1)),
        ((1, 2, 3), {1: 0, 2: 0, 3: 0}, 6, 1, None),
    ],
)
def test_editor_bounds_use_solver_envelopes(modes, caps, minimum, step, expected):
    from test_grid_profile import ranges

    assert (
        ranges(modes=modes, caps=caps, minimum=minimum, step=step).power_bounds()
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


async def test_capability_change_can_pause_at_zero_without_withdrawing_release(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 1.4)
        await grid.action()
        await settings.set("allowed_current_1p", 0)
        await eventually(lambda: grid.task.done() and station.amps == 0)
        assert settings.get("charge_release") is True
        await grid.stop_release()
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
