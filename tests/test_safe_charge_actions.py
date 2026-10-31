"""Direction/context fencing and pure preview against real HA/OCPP state."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from conftest import entities, eventually
from homeassistant.exceptions import HomeAssistantError
from ocpp.v21 import call
from test_active_control import ready
from test_grid_battery import battery
from test_grid_profile import GridStation, start_grid
from test_protocol import peer
from test_pv_runtime import install, power, release, tick


def context(grid, action=None):
    return dict(
        action=action or grid.action_state()["charge_action"],
        profile=grid.settings.get("charging_profile"),
        revision=grid.action_revision(),
        incarnation=grid.state.runtime_id,
    )


async def invoke(ha, entry, data):
    button = next(e for e in entities(ha, entry) if e.role == "charge_action")
    await ha.services.async_call(
        "wallbox_manager",
        "charge_action",
        {"entity_id": button.entity_id, **data},
        blocking=True,
    )


async def test_explicit_service_start_under_soc_stop_publishes_durable_intent(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid, pv = await release(entry, station, profile="PV_OPTIMUM")
        await grid.stop_release()
        await pv.settings.set("optimum_upper_soc", 95)
        # A before-surplus planner target of 95, independently of release.
        ha.states.async_set(entry.options["soc_speicher_aktuell"], "42.5")
        await asyncio.sleep(0)
        pv.values["soc_speicher_aktuell"] = 42.5
        pv.planner.phase = "BEFORE_SURPLUS"
        pv.values["leistung_pv"] = 0
        pv.values["leistung_verbraucher"] = 300
        await tick(pv)
        count = len(station.profiles)
        await invoke(ha, entry, context(grid))
        button = next(e for e in entities(ha, entry) if e.role == "charge_action")
        assert ha.states.get(button.entity_id).attributes["charge_release"] is True
        stored = (await pv.settings.store.async_load())[pv.settings.key]
        assert stored["charge_release"] is True
        await tick(pv)
        assert pv.core.mode == "STOP" and pv.target == 95
        assert pv.settings.get("charge_release") is True
        assert all(
            p[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
            for p in station.profiles[count:]
        )


async def test_two_cards_stale_start_stop_apply_and_revision(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.stop_release()
        old_start = context(grid)
        results = await asyncio.gather(
            invoke(ha, entry, old_start),
            invoke(ha, entry, old_start),
            return_exceptions=True,
        )
        assert sum(isinstance(r, HomeAssistantError) for r in results) == 1
        assert grid.settings.get("charge_release") is True
        count = len(station.profiles)
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old_start)
        assert len(station.profiles) == count
        old_stop = context(grid)
        await invoke(ha, entry, old_stop)
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old_stop)
        assert grid.settings.get("charge_release") is False
        await invoke(ha, entry, context(grid))
        await grid.settings.set("soll_power", 5)
        old_apply = context(grid)
        await grid.settings.set("soll_power", 6)
        draft = deepcopy(grid.settings.values)
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old_apply)
        assert grid.settings.values == draft
        for field, value in (("incarnation", "obsolete"), ("profile", "PV_OPTIMUM")):
            bad = {**context(grid), field: value}
            with pytest.raises(HomeAssistantError, match="Superseded"):
                await invoke(ha, entry, bad)
        # Explicit Stop is available even with unapproved NETZ drafts.
        await invoke(ha, entry, context(grid, "stop"))
        assert grid.settings.get("charge_release") is False
        assert grid.settings.get("soll_power") == 6


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"])
async def test_pv_preparation_without_transaction_then_normal_cycle(ha, profile):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        await settings.set("charging_profile", profile)
        assert not grid.transaction() and grid.action_state()["charge_action_ready"]
        count = len(station.profiles)
        await invoke(ha, entry, context(grid))
        assert settings.get("charge_release") is True
        assert len(station.profiles) == count
        await invoke(ha, entry, context(grid, "stop"))
        assert not settings.get("charge_release")
        await invoke(ha, entry, context(grid))
        # Existing helper establishes a transaction without another Start.
        await station.call(
            call.TransactionEvent(
                event_type="Started",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="CablePluggedIn",
                seq_no=0,
                transaction_info={
                    "transaction_id": "prepare",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        await eventually(lambda: grid.transaction() == "prepare")
        await power(station, 0)
        await tick(entry.runtime_data.pv)
        assert settings.get("charge_release") is True
        assert station.amps == 0 if profile == "PV_SURPLUS" else station.amps > 0


async def test_preview_pure_rounding_ownership_and_invalid_inputs(ha):
    b, writes, _ = await battery(ha)
    for original, requested, soc, expected in [
        (20, 40, 65, 40),
        (20, 40, 37.8, 37),
        (30, 20, 65, 30),
        (20, 80, 55, 55),
    ]:
        ha.states.async_set(
            "number.reserve", str(original), {"min": 0, "max": 100, "step": 1}
        )
        ha.states.async_set("sensor.soc", str(soc))
        before = deepcopy((b.record, b.diagnostics, b.status))
        assert b.preview(requested) == expected
        assert (b.record, b.diagnostics, b.status) == before
        assert await b.store.async_load() is None
    ha.states.async_set("number.reserve", "20", {"min": 5, "max": 90, "step": 0.5})
    ha.states.async_set("sensor.soc", "37.8")
    assert b.preview(40) == 37.5
    await b.update(40)
    ha.states.async_set("number.reserve", "37.5", {"min": 5, "max": 90, "step": 0.5})
    assert b.preview(20) == 20  # durable original, never elevated actual
    record, diagnostics = deepcopy(b.record), deepcopy(b.diagnostics)
    for _ in range(300):
        assert b.preview(40) == 37.5
    assert b.record == record and b.diagnostics == diagnostics
    assert await b.store.async_load() == record
    assert writes == [37.5]
    b.record["pending"] = 40
    assert b.preview(40) is None
    b.record = record
    b.recovering = True
    assert b.preview(40) is None
    b.recovering = False
    ha.states.async_set("number.reserve", "45", {"min": 0, "max": 100, "step": 1})
    assert b.preview(40) is None
    await b.update(None)
    assert b.preview(40) == 45  # external change now owns the baseline
    for attrs in ({"step": 0}, {"step": -1}, {"min": 90, "max": 5}, {"step": "nan"}):
        ha.states.async_set("number.reserve", "20", attrs)
        assert b.preview(40) is None
    ha.states.async_set("sensor.soc", "unavailable")
    assert b.preview(40) is None


async def test_preview_projection_change_only_no_write_before_charging(ha):
    entry = await install(ha)
    battery_runtime = entry.runtime_data.battery
    reserve = battery_runtime.reserve
    # Use a writable Number as configured source, without changing any hardware.
    reserve.reserve = entry.options["min_soc_speicher"]
    await entry.runtime_data.settings.set("min_soc", 40)
    await eventually(lambda: battery_runtime.preview == 40)
    profile = next(e for e in entities(ha, entry) if e.role == "charging_profile")
    assert ha.states.get(profile.entity_id).attributes["planned_grid_reserve"] == 40
    writes = Mock(wraps=profile.async_write_ha_state)
    profile.async_write_ha_state = writes
    for _ in range(300):
        battery_runtime.publish_preview()
    assert writes.call_count == 0
    assert reserve.record is None and await reserve.store.async_load() is None


async def test_target_transition_during_stop_and_300_identical_cycles(ha, monkeypatch):
    from test_pv_target_sensor import sensor

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid, pv = await release(entry, station, profile="PV_OPTIMUM")
        await pv.settings.set("optimum_upper_soc", 95)
        await pv.settings.set("estimated_daily_house_consumption_kwh", 0)
        pv.now = lambda: datetime(2026, 10, 30, 12, tzinfo=UTC)
        pv.values.update(
            soc_speicher_aktuell=42.5,
            leistung_pv=5000,
            leistung_verbraucher=300,
            storage_capacity=10000,
        )
        target_sensor = sensor(ha, entry)
        select = next(e for e in entities(ha, entry) if e.role == "charging_profile")
        for forecast, target in ((2744.1, 67.559), (2138.1, 73.619)):
            pv.values["remaining_pv_energy"] = forecast
            await tick(pv)
            assert pv.target == pytest.approx(target)
            assert float(ha.states.get(target_sensor.entity_id).state) == pytest.approx(
                target
            )
            assert ha.states.get(select.entity_id).attributes[
                "effective_target_soc"
            ] == pytest.approx(target)
            assert pv.core.mode == "STOP" and pv.settings.get("charge_release")
        target_write = Mock(wraps=target_sensor.async_write_ha_state)
        select_write = Mock(wraps=select.async_write_ha_state)
        button = next(e for e in entities(ha, entry) if e.role == "charge_action")
        action_write = Mock(wraps=button.async_write_ha_state)
        monkeypatch.setattr(button, "async_write_ha_state", action_write)
        monkeypatch.setattr(target_sensor, "async_write_ha_state", target_write)
        monkeypatch.setattr(select, "async_write_ha_state", select_write)
        calculate = Mock(wraps=pv.planner.calculate)
        monkeypatch.setattr(pv.planner, "calculate", calculate)
        for _ in range(300):
            await tick(pv)
        assert (
            target_write.call_count
            == select_write.call_count
            == action_write.call_count
            == 0
        )
        assert calculate.call_count == 300


@pytest.mark.parametrize("profile", ["NETZ", "PV_SURPLUS"])
async def test_stop_supersedes_inflight_start_persistence(ha, monkeypatch, profile):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.stop_release()
        await grid.settings.set("charging_profile", profile)
        entered, finish = asyncio.Event(), asyncio.Event()
        rollback, finish_rollback = asyncio.Event(), asyncio.Event()
        persist = grid.settings.persist

        async def slow(profiles):
            if profiles[grid.settings.key]["charge_release"]:
                entered.set()
                await finish.wait()
            elif entered.is_set():
                rollback.set()
                await finish_rollback.wait()
            await persist(profiles)

        monkeypatch.setattr(grid.settings, "persist", slow)
        start = asyncio.create_task(invoke(ha, entry, context(grid)))
        await entered.wait()
        await invoke(ha, entry, context(grid, "stop"))
        finish.set()
        await rollback.wait()
        assert not grid.settings.get("charge_release")
        count = len(station.profiles)
        await tick(entry.runtime_data.pv)
        assert len(station.profiles) == count
        finish_rollback.set()
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await start
        assert not grid.settings.get("charge_release")
        stored = (await grid.settings.store.async_load())[grid.settings.key]
        assert stored["charge_release"] is False


async def test_pv_start_revision_cannot_replay_after_start_stop_aba(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        grid = entry.runtime_data.grid
        await grid.settings.set("charging_profile", "PV_SURPLUS")
        old = context(grid)
        await invoke(ha, entry, old)
        await invoke(ha, entry, context(grid, "stop"))
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old)
        assert not grid.settings.get("charge_release")


@pytest.mark.parametrize("blocked", ["local", "offline", "hardware_disabled"])
async def test_pv_preparation_preserves_control_restrictions(ha, blocked):
    from test_active_control import event

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        if blocked != "local":
            await control.request("ControlAuthority", "OCPP")
        grid = entry.runtime_data.grid
        await grid.settings.set("charging_profile", "PV_SURPLUS")
        if blocked == "hardware_disabled":
            event(entry, "ChargingEnabled", "false")
        elif blocked == "offline":
            await station._connection.close()
            await eventually(lambda: not grid.state.connected)
        assert not grid.action_state()["charge_action_ready"]
        with pytest.raises(HomeAssistantError):
            await invoke(ha, entry, context(grid))
        assert not grid.settings.get("charge_release")


@pytest.mark.parametrize("profile", ["NETZ", "PV_SURPLUS"])
async def test_profile_switch_during_start_persistence_fences_old_context(
    ha, monkeypatch, profile
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.stop_release()
        await grid.settings.set("charging_profile", profile)
        entered, finish = asyncio.Event(), asyncio.Event()
        persist = grid.settings.persist

        async def slow(profiles):
            if profiles[grid.settings.key]["charge_release"]:
                entered.set()
                await finish.wait()
            await persist(profiles)

        monkeypatch.setattr(grid.settings, "persist", slow)
        old = context(grid)
        start = asyncio.create_task(invoke(ha, entry, old))
        await entered.wait()
        switch = asyncio.create_task(
            grid.settings.set("charging_profile", "PV_MAXIMUM")
        )
        # set() revokes intent before acquiring the persistence lock.
        await asyncio.sleep(0)
        finish.set()
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await start
        await switch
        assert grid.settings.get("charging_profile") == "PV_MAXIMUM"
        assert not grid.settings.get("charge_release")
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old)


@pytest.mark.parametrize(
    "minimum,maximum,step,soc,original,requested,expected",
    [
        (5, 90, 0.5, 37.8, 20, 40, 37.5),
        (5, 90, 0.5, 100, 20, 100, 90),
        (5, 90, 0.5, 0, 20, 40, 20),
        (5, 90, 0.5, 65, 4, 40, 40),
        (5, 90, 0.5, 65, 95, 40, None),
        (90, 5, 1, 65, 20, 40, None),
    ],
)
async def test_preview_number_bounds(
    ha, minimum, maximum, step, soc, original, requested, expected
):
    b, writes, _ = await battery(ha)
    ha.states.async_set(
        "number.reserve", str(original), {"min": minimum, "max": maximum, "step": step}
    )
    ha.states.async_set("sensor.soc", str(soc))
    assert b.preview(requested) == expected
    assert not writes and b.record is None
    assert await b.store.async_load() is None


async def test_preview_unavailable_mapping_reserve_and_restoration(ha):
    b, writes, _ = await battery(ha)
    b.configured = False
    assert b.preview(40) is None
    b.configured = True
    b.failed_restore = True
    assert b.preview(40) is None
    b.failed_restore = False
    for value in ("unknown", "unavailable", "nan", "101"):
        ha.states.async_set("number.reserve", value)
        assert b.preview(40) is None
    ha.states.async_set("number.reserve", "20", {"restored": True})
    assert b.preview(40) is None
    assert not writes and b.record is None


async def test_expired_explicit_apply_does_not_become_stop_or_approve_drafts(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.settings.set("grid_duration", "00:01")
        await invoke(ha, entry, context(grid))
        await grid.settings.set("soll_power", 5)
        old = context(grid)
        # Let wall time pass without invoking the independent timer callback yet.
        grid.wall_time = lambda: grid.settings.values["grid_request"]["end_at"] + 1
        before = deepcopy(grid.settings.values)
        with pytest.raises(HomeAssistantError, match="Superseded"):
            await invoke(ha, entry, old)
        assert grid.settings.values == before
        # Ordinary deadline handling, not Apply, now retires the schedule.
        await grid.stop_release()
        assert not grid.settings.get("charge_release")
        assert grid.settings.get("soll_power") == 5
