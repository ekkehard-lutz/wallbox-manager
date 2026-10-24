"""TIME-15/16/20/21: renewable focus, bounded abandonment, no policy changes."""

import asyncio
from copy import deepcopy

import pytest
from conftest import entities
from homeassistant.exceptions import HomeAssistantError
from test_grid_profile import GridStation
from test_live_netz_timing import editing, running
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


async def draft(settings):
    await editing(settings, "begin")
    await editing(settings, "update", value="00:30")
    return settings.values["grid_timing_revision"]


async def test_focused_lease_can_renew_indefinitely_without_writes_or_publications(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        revision = await draft(settings)
        before = deepcopy(settings.values)
        public = {e.entity_id: ha.states.get(e.entity_id) for e in entities(ha, entry)}
        request = deepcopy(settings.values["grid_request"])
        point_writes, operations = len(station.profiles), list(station.operations)
        publications = []
        remove = settings.watch("grid_duration", lambda: publications.append(1))

        async def forbidden(_):
            pytest.fail("Renewal must not persist")

        monkeypatch.setattr(settings, "persist", forbidden)
        for _ in range(15):
            await clock.advance(20)
            await ha.async_block_till_done()
            await settings.edit_timing("grid_duration", "renew", "card-a", revision)
        assert settings.values == before and publications == []
        assert all(ha.states.get(key) is value for key, value in public.items())
        assert settings.values["grid_request"] == request
        assert (
            len(station.profiles) == point_writes and station.operations == operations
        )
        assert settings.values["grid_timing_edit"]["active"]
        remove()


@pytest.mark.parametrize("ended", [True, False])
async def test_normal_end_or_abandonment_rolls_back_only_time_drafts(ha, ended):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        approved = deepcopy(settings.values["grid_approved"])
        request = deepcopy(settings.values["grid_request"])
        revision = await draft(settings)
        await settings.set("soll_power", 3)
        await settings.step_power(1, settings.values["grid_draft_revision"])
        power_draft = settings.get("soll_power")
        power_hint = settings.get("power_phases")
        assert power_hint == 1
        writes = len(station.profiles)
        if ended:
            await editing(settings, "end")
        else:
            await clock.advance(60)
            await ha.async_block_till_done()
            edit = settings.values["grid_timing_edit"]
            assert not edit["active"] and edit["owner"] is None
            assert edit["expires_at"] == 1075
            assert settings.values["grid_timing_revision"] == revision + 1
        await clock.advance(14)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1800
        await clock.advance(1)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1200
        assert (
            settings.get("soll_power") == power_draft
            and settings.get("power_phases") == power_hint
        )
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_request"] == request
        assert settings.get("charge_release") and len(station.profiles) == writes
        assert grid.action_state()["charge_action"] == "apply"  # Power still pending.


async def test_reconnect_renews_live_ownership_but_old_callbacks_cannot_revive_it(
    ha,
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        revision = await draft(settings)
        await clock.advance(40)  # Temporary browser network interruption.
        await settings.edit_timing("grid_duration", "renew", "card-a", revision)
        assert settings.timing_edit_deadline() == 1100
        clock.now = 1100  # Service beats the timer callback at the exact deadline.
        for action in ("renew", "end", "update"):
            with pytest.raises(ValueError, match="revision|editor"):
                await settings.edit_timing(
                    "grid_duration", action, "card-a", revision, "00:40"
                )
        assert settings.values["grid_timing_edit"]["expires_at"] == 1115
        await clock.advance(15)
        await ha.async_block_till_done()
        assert settings.values["grid_timing_edit"] is None
        with pytest.raises(ValueError, match="revision"):
            await settings.edit_timing("grid_duration", "renew", "card-a", revision)
        # A fresh deliberate Begin may claim the latest revision, including by
        # the same browser; it is distinct from replaying its expired heartbeat.
        await editing(settings, "begin")
        assert settings.values["grid_timing_edit"]["active"]


@pytest.mark.parametrize("replacement", ["card-b", "text"])
async def test_other_card_or_direct_text_supersedes_abandoned_owner(ha, replacement):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        old_revision = await draft(settings)
        await clock.advance(60)
        await ha.async_block_till_done()
        if replacement == "text":
            await settings.set("grid_duration", "00:40")
        else:
            await editing(settings, "begin", owner=replacement)
            await editing(settings, "update", owner=replacement, value="00:40")
        current = deepcopy(settings.values)
        for action in ("renew", "end", "update"):
            with pytest.raises(ValueError, match="revision"):
                await settings.edit_timing(
                    "grid_duration", action, "card-a", old_revision, "00:50"
                )
        assert settings.values == current
        if replacement == "text":
            await clock.advance(15)
            await ha.async_block_till_done()
            assert settings.get("grid_duration") == 1200
        else:
            await clock.advance(20)
            await settings.edit_timing(
                "grid_duration",
                "renew",
                replacement,
                settings.values["grid_timing_revision"],
            )
            assert settings.get("grid_duration") == 2400


@pytest.mark.parametrize("duration", ["00:01", "00:02"])
async def test_duration_expiry_beats_abandonment_or_rollback_grace(ha, duration):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", duration)
        await grid.action()
        approved = deepcopy(settings.values["grid_approved"])
        if duration == "00:02":
            await clock.advance(50)
        await draft(settings)
        # End at 1060 (same instant as lease) or 1120 (within grace 1110–1125).
        await clock.advance(60 if duration == "00:01" else 1120 - clock.now)
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert not settings.get("charge_release") and station.amps == 0
        assert settings.values["grid_timing_edit"] is None
        assert settings.get("grid_duration") == 1800
        assert settings.values["grid_approved"] == approved
        assert grid.action_state()["charge_action"] == "start"


async def test_failed_abandoned_rollback_does_not_loop_and_old_owner_stays_retired(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        revision = await draft(settings)
        persist = settings.persist
        failures = []

        async def fail(_):
            failures.append(1)
            raise OSError("Storage unavailable")

        monkeypatch.setattr(settings, "persist", fail)
        await clock.advance(60)
        await ha.async_block_till_done()
        await clock.advance(15)
        await ha.async_block_till_done()
        assert len(failures) == 1 and settings.get("grid_duration") == 1800
        assert not settings.values["grid_timing_edit"]["active"]
        with pytest.raises(ValueError):
            await settings.edit_timing("grid_duration", "renew", "card-a", revision)
        await clock.advance(100)
        await ha.async_block_till_done()
        assert len(failures) == 1
        monkeypatch.setattr(settings, "persist", persist)
        await editing(settings, "begin", owner="card-b")
        assert settings.values["grid_timing_edit"]["owner"] == "card-b"


async def test_duration_expires_while_abandoned_rollback_persistence_is_blocked(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await settings.set("grid_duration", "00:02")
        await grid.action()
        await draft(settings)
        entered, release = asyncio.Event(), asyncio.Event()
        persist = settings.persist

        async def delayed(profiles):
            values = profiles[settings.key]
            if values.get("charge_release") and values.get("grid_timing_edit") is None:
                entered.set()
                await release.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        await clock.advance(60)
        await ha.async_block_till_done()
        await clock.advance(15)
        await entered.wait()
        await clock.advance(45)
        assert not settings.get("charge_release")
        release.set()
        await ha.async_block_till_done()
        if grid.task:
            await grid.task
        assert station.amps == 0 and settings.get("grid_duration") == 1800
        assert not settings.get("charge_release")
        assert (await settings.store.async_load())[settings.key][
            "grid_duration"
        ] == 1800


async def test_reload_retires_lease_and_runtime_fences_old_services(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, _ = await running(entry, station)
        revision = await draft(settings)
        runtime = entry.runtime_data.state.runtime_id
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    settings = entry.runtime_data.settings
    assert settings.timing_lease is None and settings.values["grid_timing_edit"] is None
    assert not settings.get("charge_release") and settings.get("grid_duration") == 1800
    entity = next(e for e in entities(ha, entry) if e.role == "grid_duration")
    with pytest.raises(HomeAssistantError, match="runtime"):
        await entity.async_edit_timing("renew", "card-a", revision, runtime, "")


async def test_heartbeat_queued_before_deadline_cannot_renew_after_lock_delay(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        revision = await draft(settings)
        entered = asyncio.Event()

        async def delayed_renewal():
            entered.set()
            await settings.edit_timing("grid_duration", "renew", "card-a", revision)

        async with settings.lock:
            renewal = asyncio.create_task(delayed_renewal())
            await entered.wait()
            await clock.advance(60)
        with pytest.raises(ValueError, match="revision"):
            await renewal
        await ha.async_block_till_done()
        assert settings.values["grid_timing_edit"]["expires_at"] == 1075
        assert not settings.values["grid_timing_edit"]["active"]
        with pytest.raises(ValueError, match="editor"):
            await settings.edit_timing(
                "grid_duration",
                "end",
                "card-a",
                settings.values["grid_timing_revision"],
            )
        await clock.advance(15)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1200


async def test_abandoned_focus_without_pending_time_edits_needs_no_rollback_save(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, clock = await running(entry, station)
        await editing(settings, "begin")
        approved = deepcopy(settings.values["grid_approved"])
        revision = settings.values["grid_draft_revision"]

        async def forbidden(_):
            pytest.fail("No changed time draft needs a durable rollback")

        monkeypatch.setattr(settings, "persist", forbidden)
        await clock.advance(60)
        await ha.async_block_till_done()
        assert settings.values["grid_timing_edit"] is None
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_draft_revision"] == revision
        assert grid.action_state()["charge_action"] == "stop"


async def test_delayed_draft_write_cannot_extend_lease_at_storage_completion(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, clock = await running(entry, station)
        await editing(settings, "begin")
        persist = settings.persist
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(profiles):
            entered.set()
            await release.wait()
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", delayed)
        clock.now = 1050
        update = asyncio.create_task(editing(settings, "update", value="00:30"))
        await entered.wait()
        await clock.advance(61)
        release.set()
        await update
        # The update was accepted at 1050, not at storage completion at 1111.
        assert settings.timing_edit_deadline() == 1110
        await clock.advance(0)
        await ha.async_block_till_done()
        edit = settings.values["grid_timing_edit"]
        assert not edit["active"] and edit["expires_at"] == 1125
        await clock.advance(14)
        await ha.async_block_till_done()
        assert settings.get("grid_duration") == 1200
