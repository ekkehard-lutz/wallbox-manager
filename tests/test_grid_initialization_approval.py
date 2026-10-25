"""Initialization provenance survives both capability/reconciliation task orders."""

import asyncio
from copy import deepcopy

import pytest
from test_grid_profile import FakeClock, GridStation
from test_grid_set_reevaluation import approved_1400
from test_protocol import peer


async def minimum_in_order(grid, monkeypatch, order, station):
    """Barrier the actual initializer, durable save and OCPP reconciliation."""
    settings = grid.settings
    initialize = settings.initialize_power
    reconcile = grid.reconcile
    persist = settings.persist
    init_entered, init_done = asyncio.Event(), asyncio.Event()
    reconcile_entered, reconcile_done = asyncio.Event(), asyncio.Event()
    save_entered = asyncio.Event()
    allow_init, allow_reconcile, allow_save = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def initializing():
        init_entered.set()
        await allow_init.wait()
        await initialize()
        init_done.set()

    async def reconciling(*args, **kwargs):
        reconcile_entered.set()
        await allow_reconcile.wait()
        result = await reconcile(*args, **kwargs)
        reconcile_done.set()
        return result

    async def saving(profiles):
        if profiles[settings.key]["power_kw"] == 1.6:
            save_entered.set()
            await allow_save.wait()
        await persist(profiles)

    with monkeypatch.context() as patch:
        patch.setattr(settings, "initialize_power", initializing)
        patch.setattr(settings, "persist", saving)
        patch.setattr(grid, "reconcile", reconciling)
        before = len(station.profiles)
        state = grid.state
        state.observe(
            state.generation,
            ("capability", ("Wallbox01", "1", None), "minimum_current"),
            7,
            "NotifyReport",
        )
        try:
            async with asyncio.timeout(5):
                await init_entered.wait()
                await reconcile_entered.wait()
                allow_init.set()
                if order == "initialization_first":
                    allow_save.set()
                    await init_done.wait()
                    assert settings.get("soll_power") == 1.6
                    assert settings.approved("soll_power") == 1.4
                    assert (
                        len(station.profiles) == before
                    )  # initializer never dispatches
                    allow_reconcile.set()
                    await reconcile_done.wait()
                    await grid.task
                else:
                    await save_entered.wait()
                    # Initialization is in-flight under the Settings lock, but
                    # neither the new draft nor its provenance is committed yet.
                    assert settings.lock.locked()
                    assert settings.get("soll_power") == 1.5
                    allow_reconcile.set()
                    await reconcile_done.wait()
                    await grid.task
                    count = len(station.profiles)
                    allow_save.set()
                    await init_done.wait()
                    assert len(station.profiles) == count
                # Exercise both the settle callback and a later duplicate after
                # initialization; the lock serializes them against persistence.
                await settings.approve_equivalent_grid()
                await settings.approve_equivalent_grid()
        finally:
            allow_init.set()
            allow_reconcile.set()
            allow_save.set()


@pytest.mark.parametrize("order", ["initialization_first", "reconciliation_first"])
@pytest.mark.parametrize("released", [False, True])
async def test_initialized_minimum_requires_explicit_action_in_both_orders(
    ha, monkeypatch, order, released
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await grid.stop_release()
        await settings.set("grid_duration", "00:30")
        if released:
            await grid.action()
        else:
            await settings._approve_release()
        await settings.set("soll_power", 1.5)
        approved = deepcopy(settings.values["grid_approved"])
        request = deepcopy(settings.values["grid_request"])
        next_timing = {
            key: settings.approved(key) for key in ("grid_start_delay", "grid_duration")
        }
        revision = settings.values["grid_draft_revision"]
        count, writes = len(station.profiles), list(station.writes)

        await minimum_in_order(grid, monkeypatch, order, station)

        assert settings.get("soll_power") == 1.6
        assert settings.get("power_phases") == 1
        assert settings.values["grid_draft_revision"] == revision + 1
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_request"] == request
        assert {
            key: settings.approved(key) for key in ("grid_start_delay", "grid_duration")
        } == next_timing
        assert settings.get("charge_release") is released
        assert settings.approval_attributes()["grid_set_required"]
        assert grid.action_state()["charge_action"] == (
            "apply" if released else "start"
        )
        assert grid.action_state()["charge_action_ready"]
        stored = (await settings.store.async_load())[settings.key]
        assert stored["power_kw"] == 1.6 and stored["power_phases"] == 1
        assert stored["grid_approved"] == approved
        # Capability reconciliation still executes the old approved 1.4-kW
        # request at the new legal minimum when released; initialization adds
        # no positive dispatch, phase write, release or schedule operation.
        assert len(station.profiles) == count + int(released)
        assert grid.confirmed.amps == (7 if released else 0)
        assert station.writes == writes

        await grid.action()
        assert settings.approved("soll_power") == 1.6
        assert settings.approved("power_phases") == 1
        assert settings.values["grid_approved"]["revision"] == approved["revision"] + 1
        assert not settings.approval_attributes()["grid_set_required"]
        assert settings.get("charge_release") is True
        assert grid.confirmed.amps == 7
        assert len(station.profiles) == count + 1  # Apply needs no duplicate 7 A write
        assert grid.action_state()["charge_action"] == "stop"
        if released:
            assert settings.values["grid_request"] == request
        else:
            assert settings.values["grid_request"] == {
                "epoch_at": clock.now,
                "revision": settings.values["grid_approved"]["revision"],
                "start_at": clock.now,
                "end_at": clock.now + 1800,
            }


@pytest.fixture
async def initialized_grid(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        await minimum_in_order(grid, monkeypatch, "initialization_first", station)
        assert settings.approved("soll_power") == 1.4
        yield entry, station, grid, settings


async def test_genuine_equivalent_user_edit_clears_initialization_origin(
    initialized_grid,
):
    _, station, grid, settings = initialized_grid
    revision = settings.values["grid_approved"]["revision"]
    count = len(station.profiles)
    assert settings.values["grid_power_initialized"] is True
    # A genuine explicit numeric edit at the same displayed value clears the
    # technical hint and permits ordinary equivalent-point adoption again.
    await settings.set("soll_power", 1.6)
    assert settings.values["grid_power_initialized"] is False
    assert settings.get("power_phases") is None
    assert settings.approved("soll_power") == 1.6
    assert settings.values["grid_approved"]["revision"] == revision + 1
    assert not settings.approval_attributes()["grid_set_required"]
    assert grid.action_state()["charge_action"] == "stop"
    await settings.approve_equivalent_grid()
    assert settings.values["grid_approved"]["revision"] == revision + 1
    assert len(station.profiles) == count


async def test_timing_edits_and_delayed_callbacks_cannot_adopt_initialized_power(
    initialized_grid,
):
    _, station, grid, settings = initialized_grid
    approved = deepcopy(settings.values["grid_approved"])
    count = len(station.profiles)
    await settings.set("grid_duration", "00:05")
    await settings.set("grid_duration", "")
    await settings.set("power_approximation", "down")
    await grid.task
    await settings.approve_equivalent_grid()
    assert settings.values["grid_power_initialized"] is True
    assert settings.values["grid_approved"] == approved
    assert settings.approval_attributes()["grid_set_required"]
    assert grid.action_state()["charge_action"] == "apply"
    # DOWN recalculates the old approved 1.4 kW to zero below the new
    # minimum; the initialized 1.6-kW draft must not enter that decision.
    assert grid.confirmed.amps == 0
    assert len(station.profiles) == count + 1
    assert station.profiles[-1][1]["charging_schedule"][0][
        "charging_schedule_period"
    ] == [{"start_period": 0, "limit": 0}]


@pytest.mark.parametrize("recovery", ["reload", "reconnect"])
async def test_initialization_origin_survives_reload_and_reconnect(
    ha, monkeypatch, recovery
):
    from test_active_control import ready
    from test_release_recovery import session

    from custom_components.wallbox_manager import async_setup_entry, async_unload_entry

    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        await minimum_in_order(grid, monkeypatch, "initialization_first", station)
        approved = deepcopy(settings.values["grid_approved"])
        old_callback = settings.approve_equivalent_grid
        # This recovery test exercises release-OFF reconciliation explicitly.
        await grid.stop_release()
    if recovery == "reload":
        assert await async_unload_entry(ha, entry)
        assert await async_setup_entry(ha, entry)
    settings = entry.runtime_data.settings
    assert settings.values["grid_power_initialized"] is True
    assert settings.get("soll_power") == 1.6 and settings.get("power_phases") == 1
    assert settings.values["grid_approved"] == approved
    async with peer(entry, station_type=GridStation) as station:
        station.rows[3]["variable_attribute"][0]["value"] = "7"
        station.values.update(ControlAuthority="OCPP", PhaseRotation="Rxx")
        station.amps, station.phases = 7, 1
        await ready(entry, station)
        await session(station, resumed=True)
        grid = entry.runtime_data.grid
        await grid.task
        await old_callback()
        await settings.approve_equivalent_grid()
        assert settings.values["grid_approved"] == approved
        assert settings.values["grid_power_initialized"] is True
        assert settings.approval_attributes()["grid_set_required"]
        assert settings.get("charge_release") is False
        assert grid.confirmed.amps == station.amps == 0
        assert not station.writes
        assert [
            p[1]["charging_schedule"][0]["charging_schedule_period"]
            for p in station.profiles
        ] == [[{"start_period": 0, "limit": 0}]]
        stored = (await settings.store.async_load())[settings.key]
        assert stored["grid_power_initialized"] is True
        assert stored["grid_approved"] == approved and stored["power_phases"] == 1
        await grid.action()
        assert settings.approved("soll_power") == 1.6
        assert settings.approved("power_phases") == 1
        assert settings.values["grid_power_initialized"] is False
        assert settings.values["grid_approved"]["revision"] == approved["revision"] + 1
        assert settings.get("charge_release") is True and station.amps == 7


@pytest.mark.parametrize(
    "role", ["minimum_current", "current_step", "supported_phases"]
)
async def test_missing_capabilities_do_not_initialize_or_approve(ha, role):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        before = deepcopy(settings.values)
        state = entry.runtime_data.state
        key = ("capability", ("Wallbox01", "1", None), role)
        state.observe(state.generation, key, None, "NotifyReport")
        await settings.initialize_power()
        await grid.task
        await settings.approve_equivalent_grid()
        assert settings.values == before
        assert settings.get("soll_power") == 1.5
        assert settings.approval_attributes()["grid_set_required"]


async def test_failed_initialization_preserves_draft_and_origin_until_durable_retry(
    ha, monkeypatch
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid, settings = await approved_1400(entry, station)
        await settings.set("soll_power", 1.5)
        before = deepcopy(settings.values)
        persist = settings.persist

        async def fail(profiles):
            if profiles[settings.key]["power_kw"] == 1.6:
                raise OSError("Initialization write failed")
            await persist(profiles)

        monkeypatch.setattr(settings, "persist", fail)
        state = grid.state
        state.observe(
            state.generation,
            ("capability", ("Wallbox01", "1", None), "minimum_current"),
            7,
            "NotifyReport",
        )
        await settings.initialize_power()
        await grid.task
        await settings.approve_equivalent_grid()
        assert settings.values == before
        assert "Initialization write failed" in settings.approval_error
        assert (await settings.store.async_load())[settings.key]["power_kw"] == 1.5
        monkeypatch.setattr(settings, "persist", persist)
        await settings.initialize_power()
        await settings.approve_equivalent_grid()
        assert settings.get("soll_power") == 1.6
        assert settings.values["grid_power_initialized"] is True
        assert settings.values["grid_approved"] == before["grid_approved"]
        assert settings.approval_error is None


@pytest.mark.parametrize("failure", ["storage", "superseded"])
async def test_failed_explicit_approval_cannot_clear_initialization_origin(
    initialized_grid, monkeypatch, failure
):
    _, station, _, settings = initialized_grid
    approved = deepcopy(settings.values["grid_approved"])
    persist = settings.persist
    count = len(station.profiles)
    if failure == "storage":

        async def fail(_profiles):
            raise OSError("Approval write failed")

        monkeypatch.setattr(settings, "persist", fail)
        with pytest.raises(OSError, match="Approval write failed"):
            await settings._approve_release()
        monkeypatch.setattr(settings, "persist", persist)
    else:
        current = iter((True, False))
        with pytest.raises(ValueError, match="Superseded"):
            await settings._approve_release(current=lambda: next(current))
    await settings.approve_equivalent_grid()
    assert settings.values["grid_power_initialized"] is True
    assert settings.values["grid_approved"] == approved
    assert settings.approval_attributes()["grid_set_required"]
    stored = (await settings.store.async_load())[settings.key]
    assert stored["grid_power_initialized"] is True
    assert stored["grid_approved"] == approved
    assert len(station.profiles) == count
