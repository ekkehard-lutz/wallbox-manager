"""TIME-01–13/23–24: one business clock independent of electrical execution."""

from copy import deepcopy

import pytest
from test_active_control import ready
from test_grid_profile import FakeClock, GridStation
from test_protocol import peer
from test_release_recovery import session


def clock_grid(entry):
    grid = entry.runtime_data.grid
    clock = FakeClock()
    grid.wall_time, grid.call_later = lambda: clock.now, clock.later
    return grid, entry.runtime_data.settings, clock


async def configure(settings, delay="00:10", duration="00:20"):
    await settings.set("grid_start_delay", delay)
    await settings.set("grid_duration", duration)


@pytest.mark.parametrize("authority", ["Local", "OCPP"])
@pytest.mark.parametrize("hardware", ["false", "true"])
async def test_start_without_vehicle_enforces_planned_deadlines(
    ha, authority, hardware
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority=authority, ChargingEnabled=hardware)
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings)
        assert grid.transaction() is None
        assert grid.action_state()["charge_action_ready"]
        await grid.action()
        request = deepcopy(settings.values["grid_request"])
        assert request == {
            "epoch_at": 1000,
            "start_at": 1600,
            "end_at": 2800,
            "revision": settings.values["grid_approved"]["revision"],
        }
        assert settings.get("charge_release") is True
        assert grid.phase() == ("waiting", 1600)
        assert not station.profiles and not station.writes
        await clock.advance(600)
        await ha.async_block_till_done()
        assert grid.phase() == ("active", 2800)
        assert settings.values["grid_request"] == request
        await clock.advance(1200)
        await ha.async_block_till_done()
        assert settings.get("charge_release") is False
        assert settings.values["grid_request"] is None
        assert entry.runtime_data.state.session is None
        assert station.values["ChargingEnabled"] == hardware
        assert station.values["ControlAuthority"] == authority
        assert not station.profiles and not station.writes


async def test_transport_disconnect_keeps_business_clock_but_expiry_prevents_replay(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:01")
        await grid.action()
        request = deepcopy(settings.values["grid_request"])
    assert settings.get("charge_release") is True
    assert settings.values["grid_request"] == request
    await clock.advance(120)
    await ha.async_block_till_done()
    assert settings.get("charge_release") is False
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        station.amps = 6
        await ready(entry, station)
        await session(station, resumed=True)
        await grid.task
        assert station.amps == 0 and settings.get("charge_release") is False
        assert [
            p[1]["charging_schedule"][0]["charging_schedule_period"]
            for p in station.profiles
        ] == [[{"start_period": 0, "limit": 0}]]


async def running(entry, station):
    station.values["ControlAuthority"] = "OCPP"
    await ready(entry, station)
    grid, settings, clock = clock_grid(entry)
    await settings.initialize_power()
    await configure(settings, "", "00:20")
    await session(station)
    await grid.action()
    return grid, settings, clock


async def test_duration_only_apply_preserves_start_and_expires_past_endpoint(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        request = deepcopy(settings.values["grid_request"])
        assert request["start_at"] == 1000 and station.amps == 6
        clock.now += 12 * 60
        await settings.set("grid_duration", "00:30")
        count = len(station.profiles)
        await grid.action()
        assert settings.values["grid_request"]["start_at"] == 1000
        assert settings.values["grid_request"]["end_at"] == 2800
        assert len(station.profiles) == count
        await settings.set("grid_duration", "")
        await grid.action()
        assert settings.values["grid_request"]["start_at"] == 1000
        assert settings.values["grid_request"]["end_at"] is None
        await settings.set("grid_duration", "00:10")
        await grid.action()
        assert settings.get("charge_release") is False
        assert settings.values["grid_request"] is None
        assert station.amps == 0 and grid.transaction() == "net-test"
        assert entry.runtime_data.state.session["ended_at"] is None
        assert station.values["ChargingEnabled"] == "true"


async def test_delay_apply_pauses_without_extending_duration_for_late_vehicle(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        clock.now += 180
        await configure(settings, "00:05", "00:02")
        await grid.action()
        request = settings.values["grid_request"]
        assert request["epoch_at"] == 1180
        assert request["start_at"] == 1480 and request["end_at"] == 1600
        assert station.amps == 0 and grid.transaction() == "net-test"
        await clock.advance(300)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert station.amps == 6
        await clock.advance(120)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert station.amps == 0 and settings.get("charge_release") is False
        assert entry.runtime_data.state.session["ended_at"] is None


@pytest.mark.parametrize(
    "duration,release,end", [("", True, None), ("00:00", False, None)]
)
async def test_immediate_zero_duration_is_distinct_from_unset(
    ha, duration, release, end
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        grid, settings, _ = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "", duration)
        await grid.action()
        assert settings.get("charge_release") is release
        if release:
            assert settings.values["grid_request"]["end_at"] is end
        else:
            assert settings.values["grid_request"] is None
        assert not station.profiles and not station.writes


async def editing(
    settings, action, *, owner="card-a", role="grid_duration", value=None
):
    await settings.edit_timing(
        role, action, owner, settings.values["grid_timing_revision"], value
    )


async def test_edit_window_starts_after_both_fields_end_and_keeps_power_draft(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        approved = deepcopy(settings.values["grid_approved"])
        request = deepcopy(settings.values["grid_request"])
        count = len(station.profiles)
        await editing(settings, "begin")
        await editing(settings, "update", value="00:30")
        await editing(settings, "update", role="grid_start_delay", value="00:05")
        assert settings.values["grid_timing_edit"]["active"] is True
        await clock.advance(20)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1800  # focus still owns both fields
        assert settings.values["grid_request"] == request
        await editing(settings, "end")
        assert settings.values["grid_timing_edit"]["expires_at"] == clock.now + 15
        await settings.set("soll_power", 3)
        power_revision = settings.values["grid_draft_revision"]
        await clock.advance(14)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1800
        await clock.advance(1)
        await ha.async_block_till_done()
        assert (
            settings.get("grid_duration") == 1200
            and settings.get("grid_start_delay") is None
        )
        assert settings.get("soll_power") == 3 and settings.get("power_phases") is None
        assert settings.values["grid_draft_revision"] == power_revision + 1
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_request"] == request
        assert settings.values["grid_timing_edit"] is None
        assert grid.action_state()["charge_action"] == "apply"  # power remains pending
        assert settings.get("charge_release") is True and len(station.profiles) == count


async def test_expiry_during_focus_or_grace_retains_unapproved_times_for_new_start(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        approved = deepcopy(settings.values["grid_approved"])
        await editing(settings, "begin")
        await editing(settings, "update", value="00:10")
        clock.now = 1050
        await editing(settings, "end")  # grace would end at 1065, old schedule at 1060
        old = deepcopy(settings.values["grid_timing_edit"])
        await clock.advance(10)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert settings.get("charge_release") is False and station.amps == 0
        assert settings.values["grid_timing_edit"] is None
        assert settings.get("grid_duration") == 600
        assert settings.values["grid_approved"] == approved
        assert grid.action_state()["charge_action"] == "start"
        await clock.advance(10)
        await settings.expire_timing_edit(old["revision"], old["schedule_revision"])
        assert settings.get("grid_duration") == 600
        await grid.action()
        assert settings.get("charge_release") is True and station.amps == 6
        assert settings.values["grid_request"]["start_at"] == 1070
        assert settings.values["grid_request"]["end_at"] == 1670


async def test_new_editor_and_direct_text_revision_fence_stale_end_and_rollback(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await editing(settings, "begin")
        await editing(settings, "update", value="00:30")
        await editing(settings, "end")
        old = deepcopy(settings.values["grid_timing_edit"])
        await clock.advance(10)
        await editing(settings, "begin", owner="card-b")
        with pytest.raises(ValueError, match="revision"):
            await settings.edit_timing(
                "grid_duration", "end", "card-a", old["revision"]
            )
        await settings.set(
            "grid_duration", "00:40"
        )  # direct HA Text-style edit owns new revision
        new = deepcopy(settings.values["grid_timing_edit"])
        assert new["owner"] is None and new["active"] is False
        assert new["expires_at"] == clock.now + 15
        await settings.expire_timing_edit(old["revision"], old["schedule_revision"])
        assert settings.get("grid_duration") == 2400
        await clock.advance(5)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 2400
        await clock.advance(10)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1200
        assert grid.action_state()["charge_action"] == "stop"


async def test_failed_timing_rollback_does_not_retry_loop_or_lose_business_expiry(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        await settings.set("grid_duration", "00:02")
        persist = settings.persist
        failures = []

        async def fail_rollback(profiles):
            values = profiles[settings.key]
            if values["grid_duration"] == 60 and values["grid_timing_edit"] is None:
                failures.append(values)
                raise OSError("Timing rollback failed")
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", fail_rollback)
        await clock.advance(15)
        await ha.async_block_till_done()
        assert len(failures) == 1 and settings.get("grid_duration") == 120
        assert "Timing rollback failed" in settings.approval_error
        assert settings.get("charge_release") is True
        await clock.advance(45)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert settings.get("charge_release") is False and station.amps == 0
        assert len(failures) == 1  # actual expiry retains new timing drafts


@pytest.mark.parametrize("arrival", [0, 30, 90, 121])
async def test_vehicle_arrival_never_rearms_business_deadlines(ha, arrival):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:01")
        await grid.action()
        request = deepcopy(settings.values["grid_request"])
        await clock.advance(arrival)
        await ha.async_block_till_done()
        await session(station)
        if grid.task:
            await grid.task
        if arrival < 120:
            assert settings.values["grid_request"] == request
            assert station.amps == (0 if arrival < 60 else 6)
            await clock.advance(120 - arrival)
            await ha.async_block_till_done()
        assert settings.get("charge_release") is False
        if grid.task:
            await grid.task
        assert station.amps == 0
        assert station.values["ChargingEnabled"] == "true"


@pytest.mark.parametrize("reason", ["PhaseSwitchLockout", "ReenableLockout"])
async def test_duration_expires_before_lockout_can_restart(ha, reason):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await configure(settings, "00:01", "00:01")
        await grid.action()
        assert station.amps == 0
        station.profile_result = "Rejected"
        station.profile_info = {
            "reason_code": reason,
            "additional_info": "remainingSeconds=180",
        }
        await clock.advance(60)
        await ha.async_block_till_done()
        await grid.task
        assert grid.timer is not None and grid.retry_deadline == 1245
        assert settings.values["grid_request"]["end_at"] == 1120
        count = len(station.profiles)
        await clock.advance(60)
        await ha.async_block_till_done()
        assert settings.get("charge_release") is False and grid.timer is None
        station.profile_result = "Accepted"
        await clock.advance(180)
        await ha.async_block_till_done()
        assert station.amps == 0 and len(station.profiles) == count


@pytest.mark.parametrize("focused", [True, False])
async def test_expiry_retains_drafts_even_when_edit_callbacks_finish_later(ha, focused):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        await editing(settings, "begin")
        await editing(settings, "update", value="00:10")
        if not focused:
            clock.now = 1050
            await editing(settings, "end")
        revision = settings.values["grid_timing_revision"]
        await clock.advance(1060 - clock.now)
        await ha.async_block_till_done()
        assert settings.get("charge_release") is False
        assert settings.values["grid_timing_edit"] is None
        assert settings.get("grid_duration") == 600
        assert settings.values["grid_timing_revision"] == revision
        # A legitimate in-flight edit can finish as a draft after expiry. A new
        # explicit Start subsequently fences that old editor via timing revision.
        await settings.edit_timing(
            "grid_duration", "update", "card-a", revision, "00:12"
        )
        assert settings.get("grid_duration") == 720
        assert settings.get("charge_release") is False
        await grid.action()
        with pytest.raises(ValueError, match="revision"):
            await settings.edit_timing("grid_duration", "end", "card-a", revision + 1)


async def test_failed_apply_keeps_old_business_deadline_enforced(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        old = deepcopy(settings.values["grid_request"])
        approved = deepcopy(settings.values["grid_approved"])
        await settings.set("grid_duration", "00:20")
        persist = settings.persist

        async def fail(profiles):
            if profiles[settings.key]["grid_approved"]["grid_duration"] == 1200:
                raise OSError("Apply unavailable")
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", fail)
        with pytest.raises(OSError, match="Apply unavailable"):
            await grid.action()
        assert settings.values["grid_request"] == old
        assert settings.values["grid_approved"] == approved
        assert grid.schedule_timer is not None
        await clock.advance(60)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert settings.get("charge_release") is False and station.amps == 0
        assert settings.get("grid_duration") == 1200


async def test_duration_expiry_fences_apply_while_durable_write_is_delayed(
    ha, monkeypatch
):
    import asyncio

    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        approved = deepcopy(settings.values["grid_approved"])
        await settings.set("grid_duration", "00:20")
        persist = settings.persist
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            if profiles[settings.key].get("charge_release"):
                entered.set()
                await release.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        apply = asyncio.create_task(grid.action())
        await entered.wait()
        await clock.advance(60)
        # Deadline callback retires release before waiting on the Settings lock.
        assert settings.get("charge_release") is False
        release.set()
        with pytest.raises(ValueError, match="Superseded"):
            await apply
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert settings.values["grid_approved"] == approved
        assert settings.get("grid_duration") == 1200
        assert settings.get("charge_release") is False and station.amps == 0


async def test_entity_edit_service_runtime_revision_and_direct_text_ownership(ha):
    from conftest import entities
    from homeassistant.components import text
    from homeassistant.exceptions import HomeAssistantError

    entry = await ha.install()
    assert await text.async_setup(ha, {})
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        entity = next(e for e in entities(ha, entry) if e.role == "grid_duration")
        request = dict(
            entity_id=entity.entity_id,
            owner="browser-a",
            incarnation=entity.observed.runtime_id,
        )

        async def call(action, revision, value="", **extra):
            await ha.services.async_call(
                "wallbox_manager",
                "edit_timing",
                {
                    **request,
                    "action": action,
                    "revision": revision,
                    "value": value,
                    **extra,
                },
                blocking=True,
            )

        await call("begin", settings.values["grid_timing_revision"])
        await call("update", settings.values["grid_timing_revision"], "00:30")
        revision = settings.values["grid_timing_revision"]
        with pytest.raises(HomeAssistantError, match="runtime"):
            await call("end", revision, incarnation="previous-runtime")
        await ha.services.async_call(
            "text",
            "set_value",
            {"entity_id": entity.entity_id, "value": "00:40"},
            blocking=True,
        )
        with pytest.raises(HomeAssistantError, match="revision"):
            await call("end", revision)
        await clock.advance(15)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1200


async def test_countdown_projection_does_not_publish_each_second_or_poll(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        writes, reads = len(station.profiles), list(station.operations)
        changes = []
        remove = grid.state.watch(
            ("grid", grid.state.binding, "grid_duration"), lambda: changes.append(1)
        )
        for _ in range(10):
            await clock.advance(1)
            grid.publish()
        assert changes == []
        assert len(station.profiles) == writes and station.operations == reads
        remove()
        assert settings.values["grid_request"]["start_at"] == 1000


async def test_vehicle_starts_while_editing_delay_apply_still_pauses_same_transaction(
    ha,
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:10")
        await grid.action()
        await editing(settings, "begin")
        await editing(settings, "update", role="grid_start_delay", value="00:05")
        await session(station)
        await clock.advance(30)
        await editing(settings, "renew")
        await clock.advance(30)
        await ha.async_block_till_done()
        await grid.task
        from conftest import eventually

        await eventually(lambda: station.amps == 6)
        # Old approved schedule remains authoritative while editing.
        await editing(settings, "end")
        await grid.action()
        assert station.amps == 0 and grid.transaction() == "net-test"
        assert settings.values["grid_request"]["start_at"] == 1360
        assert settings.values["grid_request"]["end_at"] == 1960
        assert settings.values["grid_timing_edit"] is None
        assert grid.action_state()["charge_action"] == "stop"


async def test_planned_start_is_not_lost_behind_inflight_zero_decision(ha, monkeypatch):
    import asyncio

    from conftest import eventually

    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:01")
        await grid.action()
        read = grid.read_point
        entered, release = asyncio.Event(), asyncio.Event()
        first = True

        async def blocked(*args):
            nonlocal first
            if first:
                first = False
                entered.set()
                await release.wait()
            return await read(*args)

        monkeypatch.setattr(grid, "read_point", blocked)
        await session(station)
        await entered.wait()
        old_task = grid.task
        await clock.advance(60)
        assert grid.task is old_task and not old_task.done()
        release.set()
        await old_task
        await eventually(lambda: grid.task is not old_task)
        await grid.task
        assert station.amps == 6
        assert settings.values["grid_request"]["start_at"] == 1060
        assert settings.values["grid_request"]["end_at"] == 1120
        await clock.advance(60)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert station.amps == 0 and settings.get("charge_release") is False


async def test_duration_expiry_wins_over_inflight_timing_rollback_write(
    ha, monkeypatch
):
    import asyncio

    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:01")
        await grid.action()
        approved = deepcopy(settings.values["grid_approved"])
        await settings.set("grid_duration", "00:20")
        persist = settings.persist
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            values = profiles[settings.key]
            if values.get("charge_release") and values.get("grid_timing_edit") is None:
                entered.set()
                await release.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        await clock.advance(15)
        await entered.wait()  # Due rollback is now awaiting storage under its lock.
        await clock.advance(45)  # Real business timer fires despite blocked rollback.
        assert settings.get("charge_release") is False
        release.set()
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert settings.get("charge_release") is False and station.amps == 0
        assert settings.get("grid_duration") == 1200
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_timing_edit"] is None
        stored = (await settings.store.async_load())[settings.key]
        assert stored["grid_duration"] == 1200 and stored["charge_release"] is False
