"""MSG-01–07: newest applicable condition, resolution and generation fences."""

import pytest
from test_active_control import ready
from test_grid_profile import GridStation, ReviewedLockoutStation, pending_interim
from test_live_netz_timing import running
from test_protocol import eventually, peer

from custom_components.wallbox_manager.store import State


def warning(messages, identity, *, code="grid_failed", generation=None):
    messages.set(
        identity, source="test", severity="warning", code=code, generation=generation
    )


def test_newest_active_falls_back_without_history_or_duplicate_publication():
    state = State(("A", "1", "1"))
    generation = state.reset(connected=True)
    messages = state.messages
    updates = []
    state.watch(messages.key, lambda: updates.append(messages.latest))
    warning(messages, "old", generation=generation)
    first = messages.latest
    warning(messages, "new", code="phase_lockout", generation=generation)
    assert messages.latest["id"] == "new"
    for _ in range(5):
        warning(messages, "new", code="phase_lockout", generation=generation)
    assert len(updates) == 2
    messages.clear("new", generation=generation)
    assert messages.latest == first
    messages.clear("old", generation=generation)
    assert messages.latest is None and not messages.conditions
    assert len(updates) == 4


def test_runtime_local_fault_survives_transport_but_old_callbacks_cannot_resurrect():
    state = State(("A", "1", "1"))
    old = state.reset(connected=True)
    warning(state.messages, "network", generation=old)
    warning(state.messages, "storage", code="settings_save")
    state.reset(connected=False)
    new = state.reset(connected=True)
    assert state.messages.latest["id"] == "storage"
    warning(state.messages, "network", generation=old)
    state.messages.clear("storage", generation=old)
    assert state.messages.latest["id"] == "storage"
    warning(state.messages, "fresh", generation=new)
    assert state.messages.latest["id"] == "fresh"
    other = State(("B", "1", "1"))
    assert other.messages.latest is None


async def test_rejected_takeover_resolves_on_confirmed_success(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        station.result = "Rejected"
        assert await control.request("ControlAuthority", "OCPP") == "rejected"
        message = entry.runtime_data.state.messages.latest
        assert (
            message["id"] == "control:ControlAuthority"
            and message["severity"] == "error"
        )
        station.result = "Accepted"
        assert await control.request("ControlAuthority", "OCPP") == "confirmed"
        assert entry.runtime_data.state.messages.latest is None


async def test_temporarily_unconfirmed_command_is_not_itself_warning(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        station.apply = False
        assert (
            await control.request("ControlAuthority", "OCPP") == "accepted_unconfirmed"
        )
        assert entry.runtime_data.state.messages.latest is None


async def test_transport_warning_clears_after_verified_reconnect(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
    state = entry.runtime_data.state
    await eventually(lambda: state.messages.latest is not None)
    assert state.messages.latest["code"] == "connection_lost"
    async with peer(entry, station_type=GridStation) as station:
        assert state.messages.latest["code"] == "connection_lost"
        await ready(entry, station)
        assert state.messages.latest is None


async def test_current_and_phase_rejection_clears_when_superseded_and_confirmed(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings, _ = await running(entry, station)
        await settings.set("soll_power", 1.6)
        station.profile_result = "Rejected"
        with pytest.raises(ValueError):
            await grid.action()
        assert grid.state.messages.latest["code"] == "grid_failed"
        station.profile_result = "Accepted"
        await settings.set("soll_power", 1.8)
        await grid.action()
        assert grid.status == "confirmed" and grid.state.messages.latest is None


async def test_lockout_warning_is_active_until_retry_or_cancellation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, clock = await pending_interim(entry, station)
        assert grid.state.messages.latest["code"] == "phase_lockout"
        station.locked = False
        await clock.advance(6)
        await grid.task
        assert grid.status == "confirmed" and grid.state.messages.latest is None
        station.locked = True
        await entry.runtime_data.settings.set("soll_power", 11)
        await grid.action()
        assert grid.state.messages.latest["code"] == "phase_lockout"
        await grid.stop_release()
        if grid.task:
            await grid.task
        assert grid.state.messages.latest is None


async def test_persistence_warning_and_direct_setting_failure_have_resolution(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        _, settings, _ = await running(entry, station)
        persist = settings.persist

        async def fail(_profiles):
            raise OSError("storage unavailable")

        monkeypatch.setattr(settings, "persist", fail)
        with pytest.raises(OSError):
            await settings.set("grid_duration", "00:30")
        assert settings.state.messages.latest["code"] == "settings_save"
        monkeypatch.setattr(settings, "persist", persist)
        with pytest.raises(ValueError):
            await settings.set("min_soc", -1)
        assert settings.state.messages.latest["code"] == "settings_value"
        await settings.set(
            "min_soc", 20
        )  # same valid value resolves that failed operation
        assert settings.state.messages.latest["code"] == "settings_save"
        await settings.set("grid_duration", "00:30")
        assert settings.state.messages.latest is None


async def test_fault_resolution_falls_back_to_older_active_warning(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        state = entry.runtime_data.state
        warning(state.messages, "older", code="settings_save")
        key = ("observation", state.binding, "connector_state")
        state.observe(state.generation, key, "faulted", "test")
        assert state.messages.latest["code"] == "wallbox_fault"
        state.observe(state.generation, key, "occupied", "test")
        assert state.messages.latest["id"] == "older"


async def test_invalid_capabilities_warn_until_current_context_recovers(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        state = entry.runtime_data.state
        key = ("capability", ("Wallbox01", "1", None), "current_step")
        state.observe(state.generation, key, None, "test")
        assert state.messages.latest["code"] == "capabilities"
        state.observe(state.generation, key, 1, "test")
        assert state.messages.latest is None
        settings = entry.runtime_data.settings
        for phase in (1, 3):
            await settings.set(f"allowed_current_{phase}p", 0)
        assert state.messages.latest["code"] == "capabilities"
        await settings.set("allowed_current_1p", 6)
        assert state.messages.latest is None
