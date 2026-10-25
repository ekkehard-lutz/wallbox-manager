"""Release-independent observational PV planning on the single existing clock."""

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from conftest import config
from homeassistant.helpers.storage import Store
from test_grid_profile import FakeClock
from test_pv_runtime import sources

from custom_components.wallbox_manager.pv import PV


async def selected_pv(ha, profile="PV_OPTIMUM", *, production=5000, load=1000):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    mappings = sources(ha)
    for role, value in (("leistung_pv", production), ("leistung_verbraucher", load)):
        ha.states.async_set(mappings[role], str(value), {"unit_of_measurement": "W"})
    ha.config_entries.async_update_entry(
        entry, options={**mappings, "diagnostic_level": 2}
    )
    await Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles").async_save(
        {
            json.dumps(["Wallbox01", "1", "1"]): dict(
                profile=profile,
                soll_soc_speicher=50,
                optimum_lower_soc=22,
                optimum_upper_soc=80,
                estimated_daily_house_consumption_kwh=12,
            )
        }
    )
    initialize = PV.__init__

    def initialize_at_noon(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self.now = lambda: datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))

    with patch.object(PV, "__init__", initialize_at_noon):
        await ha.install(entry)
    pv = entry.runtime_data.pv
    if pv.timer:
        pv.timer.cancel()
        pv.timer = None
    clock = FakeClock()
    pv.monotonic = lambda: clock.now
    if pv.planner.planned_at is not None:
        pv.planner.planned_at = clock.now
    pv.grid.wall_time, pv.grid.call_later = lambda: clock.now, clock.later
    pv.now = lambda: datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    pv.next_due = None
    pv.lifecycle()
    return entry, pv, clock


async def test_optimum_forecast_and_phase_advance_without_start(ha):
    entry, pv, _ = await selected_pv(ha)
    assert not pv.settings.get("charge_release")
    await pv.tick()
    pv.values["remaining_pv_energy"] = 8000
    await pv.tick()
    assert pv.planner.phase == "DYNAMIC"
    assert pv.target == 60
    assert pv.grid.requested is None
    assert entry.runtime_data.server.adapter is None


async def cycle(pv, clock, seconds=5):
    await clock.advance(seconds)
    if pv.task:
        await pv.task


async def set_source(ha, entry, role, value):
    import asyncio

    entity = entry.options[role]
    unit = (
        "%"
        if "soc" in role
        else "Wh"
        if role in ("storage_capacity", "remaining_pv_energy")
        else "W"
    )
    ha.states.async_set(entity, str(value), {"unit_of_measurement": unit})
    await asyncio.sleep(0)


async def test_initial_targets_available_before_start_without_ocpp(ha):
    for profile, expected in (
        ("PV_SURPLUS", 50),
        ("PV_MAXIMUM", 22),
        ("PV_OPTIMUM", 80),
    ):
        entry, pv, _ = await selected_pv(ha, profile, production=0)
        assert pv.target == expected
        assert pv.attributes()["effective_target_soc"] == expected
        assert pv.timer is not None
        assert not pv.settings.get("charge_release")
        assert pv.grid.transaction() is None and pv.grid.listener.adapter is None
        assert pv.grid.requested is None and pv.core.up_cycles == 0
        assert entry.runtime_data.battery.reserve.record is None


async def test_maximum_and_surplus_reserve_updates_while_off(ha):
    for profile, first, second in (("PV_MAXIMUM", 22, 32.25), ("PV_SURPLUS", 50, 50)):
        entry, pv, clock = await selected_pv(ha, profile)
        assert pv.target == first
        await set_source(ha, entry, "min_soc_speicher", 30.25)
        await cycle(pv, clock)
        assert pv.target == second
        assert pv.grid.requested is None and not pv.regulation_active()
    await set_source(ha, entry, "min_soc_speicher", 48.001)
    assert pv.target is None
    await cycle(pv, clock)
    assert pv.target is None and pv.settings.get("soll_soc_speicher") == 50
    await set_source(ha, entry, "min_soc_speicher", 48)
    await cycle(pv, clock)
    assert pv.target == 50


async def test_full_optimum_day_and_forecast_changes_while_off(ha):
    entry, pv, clock = await selected_pv(ha, production=0)
    assert pv.planner.phase == "BEFORE_SURPLUS" and pv.target == 80
    await set_source(ha, entry, "leistung_pv", 2000)
    await cycle(pv, clock)
    assert pv.planner.phase == "DYNAMIC" and pv.target == 40
    await set_source(ha, entry, "remaining_pv_energy", 8000)
    await cycle(pv, clock)
    assert pv.target == 60
    await set_source(ha, entry, "remaining_pv_energy", 10000)
    await cycle(pv, clock)
    assert pv.target == 40
    await set_source(ha, entry, "leistung_pv", 0)  # a cloud cannot revoke DYNAMIC
    await set_source(ha, entry, "remaining_pv_energy", 6000)
    await cycle(pv, clock)
    assert pv.planner.phase == "FINISHED" and pv.target == 80
    await set_source(ha, entry, "remaining_pv_energy", 20000)
    await cycle(pv, clock)
    assert pv.planner.phase == "FINISHED" and pv.target == 80
    assert not pv.regulation_active() and pv.grid.requested is None
    assert pv.budget.scope is None and pv.budget.wh == 0


async def test_first_surplus_proof_never_assumes_off_means_zero_ev(ha):
    entry, pv, clock = await selected_pv(ha, production=1000, load=1500)
    assert pv.ev_power() is None
    assert pv.planner.phase == "BEFORE_SURPLUS"
    for _ in range(4):
        await cycle(pv, clock)
    assert pv.planner.phase == "BEFORE_SURPLUS"
    await set_source(ha, entry, "leistung_pv", 1500)  # equality is not strict proof
    await cycle(pv, clock)
    assert pv.planner.phase == "BEFORE_SURPLUS"
    await set_source(ha, entry, "leistung_pv", 1500.1)
    await cycle(pv, clock)
    assert pv.planner.phase == "DYNAMIC"
    assert pv.ev_power() is None and not pv.settings.get("charge_release")


async def test_missing_measurement_does_not_retroactively_create_surplus(ha):
    entry, pv, clock = await selected_pv(ha, production=0)
    await set_source(ha, entry, "leistung_verbraucher", "unavailable")
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    assert pv.planner.phase == "BEFORE_SURPLUS"
    await set_source(ha, entry, "leistung_pv", 0)
    await set_source(ha, entry, "leistung_verbraucher", 1000)
    await cycle(pv, clock)
    assert pv.planner.phase == "BEFORE_SURPLUS"


async def test_phase_persistence_and_target_restore_without_manual_start(
    ha, monkeypatch
):
    from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
    from custom_components.wallbox_manager.pv import PV

    original = PV.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.now = lambda: datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))

    monkeypatch.setattr(PV, "__init__", initialize)
    entry, pv, clock = await selected_pv(ha, production=0)
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    # Force FINISHED independently of the wall clock used during the subsequent setup.
    await set_source(ha, entry, "remaining_pv_energy", 0)
    await cycle(pv, clock)
    saved_day = pv.planner.saved()
    assert saved_day["phase"] == "FINISHED"
    pv.budget.scope = [*pv.state.binding, "remote", "historic-tx"]
    pv.budget.wh, pv.budget.stopped, pv.budget.cause = 51, True, "import_budget"
    await pv.persist()
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    restored = entry.runtime_data.pv
    assert restored.planner.saved() == saved_day
    assert restored.target == 80 and restored.timer is not None
    assert restored.budget.wh == 51 and restored.budget.stopped
    assert restored.grid.transaction() is None and restored.grid.requested is None
    assert not restored.settings.get("charge_release")


async def test_forecast_and_capacity_invalidity_recovery_while_off(ha):
    entry, pv, clock = await selected_pv(ha)
    for role in ("remaining_pv_energy", "storage_capacity", "min_soc_speicher"):
        await set_source(ha, entry, role, "unavailable")
        assert pv.target is None
        await cycle(pv, clock)
        assert pv.target is None
        value = 20 if role == "min_soc_speicher" else 10000
        await set_source(ha, entry, role, value)
        await cycle(pv, clock)
        assert pv.target is not None
        assert pv.timer is not None and not pv.settings.get("charge_release")
    await set_source(ha, entry, "soc_speicher_aktuell", "unavailable")
    await cycle(pv, clock)
    assert pv.target is not None  # actual SoC is not a target-formula dependency
    assert pv.grid.requested is None


async def test_midnight_and_dst_while_off_and_invalid_forecast(ha):
    from datetime import timedelta

    for month, day, hours in ((3, 29, 23), (10, 25, 25)):
        entry, pv, clock = await selected_pv(ha, production=0)
        midnight = datetime(2026, month, day, tzinfo=ZoneInfo("Europe/Vienna"))
        pv.now = lambda midnight=midnight: midnight
        await set_source(ha, entry, "leistung_pv", 5000)
        await set_source(ha, entry, "remaining_pv_energy", 16000)
        await cycle(pv, clock)
        # Daily 12 kWh house demand, actual 23/25-hour local day, 10 kWh battery.
        assert pv.target == max(22, 80 - (16000 - hours * 500) / 100)
        await set_source(ha, entry, "remaining_pv_energy", 0)
        await cycle(pv, clock)
        assert pv.planner.phase == "FINISHED"
        pv.now = lambda midnight=midnight: midnight + timedelta(days=1)
        await set_source(ha, entry, "leistung_pv", 0)
        await set_source(ha, entry, "remaining_pv_energy", "unavailable")
        await cycle(pv, clock)
        assert pv.planner.phase == "BEFORE_SURPLUS" and pv.target is None
        assert pv.planner.date == (midnight + timedelta(days=1)).date().isoformat()
        await set_source(ha, entry, "remaining_pv_energy", 16000)
        await cycle(pv, clock)
        assert pv.planner.phase == "BEFORE_SURPLUS" and pv.target == 80


async def test_planner_300_second_cadence_stays_on_single_scheduler(ha):
    from datetime import timedelta

    entry, pv, clock = await selected_pv(ha, production=0)
    initial = pv.now()
    pv.now = lambda: initial + timedelta(seconds=clock.now - 1000)
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    target, calculated = pv.target, pv.planner.planned_at
    await cycle(pv, clock, 295)
    assert pv.planner.planned_at == calculated and pv.target == target
    await cycle(pv, clock, 5)
    assert pv.planner.planned_at == clock.now and pv.target < target
    assert len([h for h in clock.calls if not h.cancelled]) == 1
    assert entry.runtime_data.server.adapter is None


async def transaction_ready(entry, station):
    from datetime import UTC

    from conftest import eventually
    from ocpp.v21 import call
    from test_active_control import ready
    from test_pv_runtime import power

    control = await ready(entry, station)
    await control.request("ControlAuthority", "OCPP")
    await station.call(
        call.TransactionEvent(
            event_type="Started",
            timestamp=datetime.now(UTC).isoformat(),
            trigger_reason="CablePluggedIn",
            seq_no=0,
            transaction_info={
                "transaction_id": "planning-test",
                "charging_state": "EVConnected",
            },
            evse={"id": 1, "connector_id": 1},
        )
    )
    await eventually(lambda: entry.runtime_data.grid.current_point() is not None)
    await power(station, 0)


async def test_release_transitions_preserve_dynamic_and_finished_planning(ha):
    from test_grid_profile import GridStation
    from test_protocol import peer
    from test_pv_runtime import tick

    entry, pv, clock = await selected_pv(ha, production=0)
    async with peer(entry, station_type=GridStation) as station:
        await transaction_ready(entry, station)
        await set_source(ha, entry, "leistung_pv", 5000)
        await cycle(pv, clock)
        day, target = pv.planner.saved(), pv.target
        assert day["phase"] == "DYNAMIC" and target == 40
        assert station.profiles == []
        await pv.grid.action()
        assert pv.settings.get("charge_release")
        assert pv.planner.saved() == day and pv.target == target
        assert (
            station.profiles == []
        )  # Start grants intent, not an event regulation pass.
        await tick(pv)
        assert station.amps == 6
        assert pv.planner.saved() == day and pv.target == target
        await pv.grid.stop_release()
        if pv.grid.task:
            await pv.grid.task
        assert not pv.settings.get("charge_release") and station.amps == 0
        assert pv.planner.saved() == day and pv.target == target
        assert pv.timer is not None
        count = len(station.profiles)
        await set_source(ha, entry, "remaining_pv_energy", 6000)
        await cycle(pv, clock)
        assert pv.planner.phase == "FINISHED" and pv.target == 80
        assert len(station.profiles) == count
        await pv.grid.action()
        assert pv.planner.phase == "FINISHED" and pv.target == 80
        await tick(pv)
        assert pv.planner.phase == "FINISHED" and pv.target == 80
        assert station.amps == 0  # existing mandatory SoC STOP, not a planning command
        await pv.grid.stop_release()
        if pv.grid.task:
            await pv.grid.task
        await set_source(ha, entry, "remaining_pv_energy", 20000)
        count = len(station.profiles)
        await cycle(pv, clock)
        assert pv.planner.phase == "FINISHED" and len(station.profiles) == count
        assert station.values["ChargingEnabled"] == "true"


async def test_local_disabled_no_transaction_plans_without_any_profile_command(ha):
    from test_active_control import event, ready
    from test_grid_profile import GridStation
    from test_protocol import peer
    from test_pv_runtime import power

    entry, pv, clock = await selected_pv(ha, production=1000, load=1500)
    assert pv.planner.phase == "BEFORE_SURPLUS"
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        event(entry, "ChargingEnabled", "false")
        # A genuine current-generation EVSE measurement can prove household load
        # without a transaction. Release OFF itself never supplies this number.
        await power(station, 1000)
        assert pv.grid.transaction() is None and pv.ev_power() is None
        await cycle(pv, clock)
        assert pv.planner.phase == "DYNAMIC" and pv.target == 40
        for forecast, expected in ((8000, 60), (6000, 80), (10000, 80)):
            await set_source(ha, entry, "remaining_pv_energy", forecast)
            await cycle(pv, clock)
            assert pv.target == expected
        assert station.profiles == []
        assert not any(op == "SetChargingProfile" for op in station.operations)
        assert not pv.regulation_active() and pv.budget.scope is None


async def test_ocpp_disconnect_and_bootstrap_do_not_queue_planning_points(ha):
    from conftest import eventually
    from test_active_control import ready
    from test_grid_profile import GridStation
    from test_protocol import peer

    entry, pv, clock = await selected_pv(ha, production=0)
    async with peer(entry, station_type=GridStation) as station:
        await transaction_ready(entry, station)
        assert station.profiles == []
        requested = (
            pv.grid.requested
        )  # existing release-OFF reconciliation, not planning
    await eventually(lambda: not pv.state.connected)
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    assert pv.planner.phase == "DYNAMIC" and pv.target == 40
    await set_source(ha, entry, "remaining_pv_energy", 8000)
    await cycle(pv, clock)
    assert pv.target == 60 and pv.grid.requested == requested
    assert pv.proposed is None
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        await cycle(pv, clock)
        assert pv.target == 60 and station.profiles == []
        assert not pv.settings.get("charge_release")


async def test_profile_switch_retires_target_and_preserves_only_current_optimum_day(ha):
    from datetime import timedelta

    entry, pv, clock = await selected_pv(ha, production=0)
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    assert pv.target == 40 and pv.planner.phase == "DYNAMIC"
    saved = pv.planner.saved()
    await pv.settings.set("charging_profile", "PV_MAXIMUM")
    assert pv.target is None
    await cycle(pv, clock)
    assert pv.target == 22 and pv.planner.saved() == saved
    await set_source(ha, entry, "remaining_pv_energy", 8000)
    await set_source(ha, entry, "leistung_pv", 0)
    await pv.settings.set("charging_profile", "PV_OPTIMUM")
    assert pv.target is None
    await cycle(pv, clock)
    assert pv.target == 60 and pv.planner.phase == "DYNAMIC"
    pv.now = lambda: (
        datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna")) + timedelta(days=1)
    )
    await cycle(pv, clock)
    assert pv.target == 80 and pv.planner.phase == "BEFORE_SURPLUS"
    await pv.settings.set("charging_profile", "NETZ")
    assert pv.target is None and pv.timer is None
    await pv.tick()
    assert pv.target is None
    assert entry.runtime_data.battery.reserve.record is None


async def test_planning_alone_never_changes_economic_provenance_or_control(ha):
    entry, pv, clock = await selected_pv(ha, production=0)
    pv.budget.scope = [*pv.state.binding, "remote", "old-tx"]
    pv.budget.wh, pv.budget.stopped, pv.budget.cause = 51, True, "import_budget"
    before = pv.budget.saved()
    for forecast, production in ((10000, 5000), (8000, 5000), (0, 0), (20000, 5000)):
        await set_source(ha, entry, "remaining_pv_energy", forecast)
        await set_source(ha, entry, "leistung_pv", production)
        await cycle(pv, clock)
        assert pv.budget.saved() == before
        assert pv.grid.transaction() is None
        assert pv.grid.requested is None and pv.proposed is None
        assert not pv.settings.get("charge_release")
    assert pv.planner.phase == "FINISHED"


async def test_planning_persistence_failure_is_reported_and_retried_on_existing_tick(
    ha, monkeypatch
):
    entry, pv, clock = await selected_pv(ha, production=0)
    previous = dict(pv.last_saved)
    original = pv.store.async_save

    async def failure(record):
        raise OSError("planning journal failed")

    with monkeypatch.context() as patch:
        patch.setattr(pv.store, "async_save", failure)
        await set_source(ha, entry, "leistung_pv", 5000)
        await cycle(pv, clock)
        assert pv.planner.phase == "DYNAMIC"
        assert pv.input_problem == "journal_write_failed" and pv.last_saved == previous
        assert pv.grid.requested is None
        assert not pv.settings.get("charge_release")
    assert pv.store.async_save == original
    await cycle(pv, clock)
    assert pv.input_problem is None
    assert pv.last_saved["day"] == pv.planner.saved()


async def test_single_scheduler_coalesces_slow_planning_and_unchanged_ticks_are_sparse(
    ha, monkeypatch, caplog
):
    import asyncio
    import logging

    entry, pv, clock = await selected_pv(ha, production=0)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = pv.store.async_save

    async def delayed(record):
        entered.set()
        await proceed.wait()
        await original(record)

    await set_source(ha, entry, "leistung_pv", 5000)
    with monkeypatch.context() as patch:
        patch.setattr(pv.store, "async_save", delayed)
        await clock.advance(5)
        await entered.wait()
        task = pv.task
        await clock.advance(5)
        assert pv.task is task and len([h for h in clock.calls if not h.cancelled]) == 1
        proceed.set()
        await task
    publications = []
    remove = pv.state.watch(
        ("grid", pv.state.binding, "charging_profile"),
        lambda: publications.append(pv.target),
    )
    caplog.clear()
    try:
        with caplog.at_level(
            logging.INFO, logger="custom_components.wallbox_manager.pv"
        ):
            await cycle(pv, clock)
            count = len(caplog.records), len(publications)
            for _ in range(300):
                await cycle(pv, clock)
            assert (len(caplog.records), len(publications)) == count
            assert pv.core.up_cycles == 0 and pv.grid.requested is None
            assert len([h for h in clock.calls if not h.cancelled]) == 1
    finally:
        remove()


async def test_battery_free_surplus_stays_without_a_battery_target_or_idle_planner(ha):
    import asyncio

    from test_pv_runtime import install

    entry = await install(ha, battery=False)
    pv = entry.runtime_data.pv
    await pv.settings.set("charging_profile", "PV_SURPLUS")
    await asyncio.sleep(0)
    await pv.tick()
    assert pv.target is None and pv.timer is None
    assert not pv.planning_selected() and pv.grid.requested is None


async def test_off_planning_does_not_call_regulator_or_execution(ha, monkeypatch):
    entry, pv, clock = await selected_pv(ha, production=0)

    def forbidden(*args, **kwargs):
        raise AssertionError("planning must not enter power regulation or dispatch")

    monkeypatch.setattr(pv.core, "decide", forbidden)
    monkeypatch.setattr(pv, "submit", forbidden)
    monkeypatch.setattr(pv.grid, "launch", forbidden)
    await set_source(ha, entry, "leistung_pv", 5000)
    await cycle(pv, clock)
    await set_source(ha, entry, "remaining_pv_energy", 6000)
    await cycle(pv, clock)
    assert pv.target == 80 and pv.planner.phase == "FINISHED"
    assert not pv.settings.get("charge_release")


async def test_valid_target_does_not_require_soc_or_storage_power_mappings(ha):
    entry, pv, clock = await selected_pv(ha, "PV_MAXIMUM", production=0)
    for role in (
        "soc_speicher_aktuell",
        "storage_charge_power",
        "storage_discharge_power",
    ):
        await set_source(ha, entry, role, "unavailable")
    await cycle(pv, clock)
    assert pv.target == 22 and not pv.regulation_active()
    assert pv.grid.requested is None


async def test_listener_bind_failure_closes_planning_clock_before_retry(
    ha, monkeypatch
):
    import pytest
    from homeassistant.exceptions import ConfigEntryNotReady

    from custom_components.wallbox_manager import async_setup_entry

    entry, original, _ = await selected_pv(ha)
    await ha.config_entries.async_unload_platforms(entry, [])
    await original.close()
    await entry.runtime_data.grid.close()
    await entry.runtime_data.battery.close()
    await entry.runtime_data.server.stop()
    listener = type(entry.runtime_data.server)
    start = listener.start

    async def fail(self):
        raise OSError("listener already bound")

    monkeypatch.setattr(listener, "start", fail)
    with pytest.raises(ConfigEntryNotReady, match="Cannot bind"):
        await async_setup_entry(ha, entry)
    failed = entry.runtime_data.pv
    assert failed.closed and failed.timer.cancelled()
    assert all(not listeners for listeners in failed.settings.listeners.values())
    monkeypatch.setattr(listener, "start", start)
    await async_setup_entry(ha, entry)
    assert entry.runtime_data.pv is not failed
    assert entry.runtime_data.pv.timer is not None
    assert not entry.runtime_data.pv.closed
