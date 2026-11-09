"""Entry teardown drains the simulated transport even if persistence fails."""

import asyncio
import logging

import pytest
from conftest import config
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from ocpp.v21 import call
from test_critical_fault_intent import pause_request, reached
from test_grid_profile import GridStation
from test_live_netz_timing import running
from test_protocol import peer
from websockets.exceptions import ConnectionClosed

from custom_components.wallbox_manager import async_unload_entry, pv_journal

WRITE_FAILURE = "injected PV journal save failure"


async def fail_save(_record):
    raise OSError(WRITE_FAILURE)


async def release_baseline_resources(runtime):
    """Leave no sockets or repeated listener removals when reproducing on baseline."""
    if runtime.server.server is not None:
        await runtime.grid.close()
        await runtime.battery.close()
        await runtime.server.stop()
    if runtime.pv.closed:
        runtime.pv.input_remove = lambda: None
        runtime.pv.removers.clear()


async def test_unload_storage_failure_releases_port_and_drains_old_adapter(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, _ = await running(entry, station)
        runtime = entry.runtime_data
        port = runtime.server.port
        adapter = runtime.server.adapter
        schedule_timer = grid.schedule_timer
        await runtime.pv.persist()
        await settings.set("soll_power", 4.14)
        async with pause_request(monkeypatch, grid, "post-ack") as (paused, _):
            await settings._approve_release()
            await reached(paused)
            work = grid.task
            runtime.pv.budget.wh += 1
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(runtime.pv.store, "async_save", fail_save)
                    with pytest.raises(OSError, match=WRITE_FAILURE):
                        await async_unload_entry(ha, entry)
                assert runtime.server.server is None and runtime.server.adapter is None
                assert adapter.closed and not adapter.tasks and work.cancelled()
                assert grid.closed and runtime.battery.closed and runtime.pv.closed
                assert schedule_timer.cancelled and grid.schedule_timer is None
                assert not runtime.state.listeners
                assert all(not listeners for listeners in settings.listeners.values())
                with pytest.raises(ConnectionClosed):
                    await station.call(call.Heartbeat(), suppress=False)
                assert runtime.pv.last_saved["economics"]["wh"] != runtime.pv.budget.wh
            finally:
                await release_baseline_resources(runtime)
                ha.config_entries._entries.pop(entry.entry_id, None)

    replacement = await ha.install(config(port=port))
    assert replacement.runtime_data.server.port == port
    async with peer(replacement, station_type=GridStation) as station:
        assert (await station.call(call.Heartbeat(), suppress=False)).current_time


async def test_ha_stop_reports_journal_failure_and_attempts_session_save(
    ha, monkeypatch, caplog
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await running(entry, station)
        runtime = entry.runtime_data
        runtime.pv.budget.wh += 1
        session_saved = asyncio.Event()
        save_session = runtime.session_store.async_save

        async def save(record):
            await save_session(record)
            session_saved.set()

        monkeypatch.setattr(runtime.pv.store, "async_save", fail_save)
        monkeypatch.setattr(runtime.session_store, "async_save", save)
        try:
            with caplog.at_level(logging.ERROR):
                ha.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
                await ha.async_block_till_done()
            assert runtime.server.server is None and runtime.server.adapter is None
            assert runtime.grid.closed and runtime.battery.closed and runtime.pv.closed
            assert session_saved.is_set()
            assert WRITE_FAILURE in caplog.text
        finally:
            await release_baseline_resources(runtime)
            await ha.config_entries.async_unload_platforms(entry, [])
            ha.config_entries._entries.pop(entry.entry_id, None)


async def test_partial_setup_unloads_platforms_and_closes_listener_after_save_error(
    ha, monkeypatch
):
    entry = config()
    forward = ha.config_entries.async_forward_entry_setups

    async def fail_after_forward(entry, platforms):
        await forward(entry, platforms)
        runtime = entry.runtime_data
        runtime.pv.budget.wh += 1
        monkeypatch.setattr(runtime.pv.store, "async_save", fail_save)
        raise RuntimeError("injected setup failure")

    monkeypatch.setattr(
        ha.config_entries, "async_forward_entry_setups", fail_after_forward
    )
    try:
        with pytest.raises(RuntimeError, match="injected setup failure") as exc:
            await ha.install(entry)
        assert isinstance(exc.value.__cause__, OSError)
        assert str(exc.value.__cause__) == WRITE_FAILURE
        runtime = entry.runtime_data
        assert runtime.server.server is None
        assert runtime.grid.closed and runtime.battery.closed and runtime.pv.closed
        assert entry.entry_id not in ha.test_platforms and not runtime.state.listeners
    finally:
        await release_baseline_resources(entry.runtime_data)
        if entry.entry_id in ha.test_platforms:
            await ha.config_entries.async_unload_platforms(entry, [])
        ha.config_entries._entries.pop(entry.entry_id, None)


async def test_cancelled_unload_joins_cleanup_and_final_save(ha, monkeypatch):
    entry = await ha.install()
    runtime = entry.runtime_data
    runtime.pv.budget.wh += 1
    reached_save, finish_save = asyncio.Event(), asyncio.Event()
    original = runtime.pv.store.async_save

    async def delayed_save(record):
        reached_save.set()
        await finish_save.wait()
        await original(record)

    monkeypatch.setattr(runtime.pv.store, "async_save", delayed_save)
    task = asyncio.create_task(async_unload_entry(ha, entry))
    try:
        await reached(reached_save)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish_save.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.server.server is None
        assert runtime.grid.closed and runtime.battery.closed
        assert runtime.pv.last_saved["economics"]["wh"] == runtime.pv.budget.wh
    finally:
        finish_save.set()
        await asyncio.gather(task, return_exceptions=True)
        await release_baseline_resources(runtime)
        ha.config_entries._entries.pop(entry.entry_id, None)


async def test_successful_unload_still_returns_true(ha):
    entry = await ha.install()
    runtime = entry.runtime_data
    assert await async_unload_entry(ha, entry)
    assert runtime.server.server is None
    assert runtime.grid.closed and runtime.battery.closed and runtime.pv.closed
    # HA removes the entry after a successful unload.
    ha.config_entries._entries.pop(entry.entry_id, None)


async def test_cancelled_setup_does_not_overwrite_unloaded_pv_journal(ha, monkeypatch):
    entry = await ha.install()
    previous = entry.runtime_data
    budget = previous.pv.budget
    assert budget.bind([*previous.state.binding, "remote", "old-transaction"])
    budget.wh, budget.stopped, budget.cause = 50, True, "import_budget"
    assert await async_unload_entry(ha, entry)
    record = await previous.pv.store.async_load()
    assert record["economics"]["stopped"]

    reading = asyncio.Event()

    async def blocked_load(*_args):
        reading.set()
        await asyncio.Future()

    monkeypatch.setattr(pv_journal, "load_journal", blocked_load)
    setup = asyncio.create_task(ha.install(entry))
    try:
        await reached(reading)
        setup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setup
        runtime = entry.runtime_data
        assert runtime.grid.closed and runtime.battery.closed and runtime.pv.closed
        assert runtime.server.server is None
        assert await previous.pv.store.async_load() == record
    finally:
        setup.cancel()
        await asyncio.gather(setup, return_exceptions=True)
        ha.config_entries._entries.pop(entry.entry_id, None)
