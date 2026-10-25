"""Real HA event sources and localhost OCPP peer; no physical hardware acceptance."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from conftest import config, entities, eventually
from ocpp.v21 import call
from test_active_control import event
from test_grid_profile import GridStation, start_grid
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
from custom_components.wallbox_manager.pv import reading
from custom_components.wallbox_manager.settings import NUMBERS, OBSOLETE_PV


def sources(ha, *, battery=True):
    values = dict(grid_import_power=0, grid_export_power=2000)
    if battery:
        values.update(
            min_soc_speicher=20,
            soc_speicher_aktuell=60,
            storage_charge_power=0,
            storage_discharge_power=0,
            leistung_pv=5000,
            leistung_verbraucher=300,
            storage_capacity=10000,
            remaining_pv_energy=10000,
        )
    options = {key: "sensor.pv_" + key for key in values}
    for key, value in values.items():
        unit = (
            "%"
            if "soc" in key
            else "Wh"
            if key in ("storage_capacity", "remaining_pv_energy")
            else "W"
        )
        ha.states.async_set(options[key], str(value), {"unit_of_measurement": unit})
    return options


async def power(station, watts):
    await station.call(
        call.MeterValues(
            evse_id=1,
            meter_value=[
                dict(
                    timestamp=datetime.now(UTC).isoformat(),
                    sampled_value=[
                        dict(
                            measurand="Power.Active.Import",
                            value=watts,
                            unit_of_measure={"unit": "W"},
                        )
                    ],
                )
            ],
        )
    )


async def install(ha, *, battery=True):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(
        entry, options={**sources(ha, battery=battery), "diagnostic_level": 2}
    )
    return await ha.install(entry)


async def release(entry, station, *, profile="PV_SURPLUS"):
    g = await start_grid(entry, station)
    s = entry.runtime_data.settings
    await s.set("optimum_lower_soc", 22)
    await s.set("soll_soc_speicher", 50)
    await s.set("charging_profile", profile)
    await eventually(
        lambda: (
            station.amps == 0
            and not g.point_uncertain
            and g.current_point()
            and g.current_point().amps == 0
        )
    )
    await g.action()
    pv = entry.runtime_data.pv
    if pv.timer:
        pv.timer.cancel()
        pv.timer = None
    await power(station, 0)
    if g.task and not g.task.done():
        await g.task
    return g, pv


async def tick(pv):
    await pv.tick()
    g = pv.grid
    if g.task and not g.task.done():
        await g.task


async def test_event_inputs_wait_for_tick_and_startup_is_ten_complete_cycles(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        count = len(station.profiles)
        ha.states.async_set(
            entry.options["grid_export_power"], "0", {"unit_of_measurement": "W"}
        )
        await asyncio.sleep(0)
        assert len(station.profiles) == count
        await tick(pv)
        assert station.amps == 6 and station.phases == 1
        assert pv.core.startup == 0
        await power(station, 1380)
        for _ in range(10):
            await tick(pv)
            assert station.amps == 6
        await tick(pv)
        assert station.amps == 7 and pv.core.startup == 11
        assert g.attributes()["effective_target_soc"] == 50
        assert entry.runtime_data.settings.get("charge_release") is True


async def test_battery_free_surplus_only_three_measurements(ha):
    entry = await install(ha, battery=False)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        assert station.amps > 0
        assert pv.input_problem is None and pv.core.mode == "SURPLUS"
        assert pv.target is None


async def test_zero_desired_does_not_start_battery_free(ha):
    entry = await install(ha, battery=False)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        pv.values["grid_export_power"] = 0
        await tick(pv)
        assert station.amps == 0
        pv.values["grid_export_power"] = 1
        await tick(pv)
        assert station.amps == 6


@pytest.mark.parametrize("kind", ["operator", "soc", "authority", "budget"])
async def test_startup_stop_precedence_and_late_positive_fences(ha, kind):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        assert station.amps == 6
        if kind == "operator":
            await g.stop_release()
        elif kind == "authority":
            event(entry, "ControlAuthority", "Local")
        elif kind == "soc":
            pv.values["soc_speicher_aktuell"] = 47
            pv.values["storage_charge_power"] = None
            pv.values["grid_import_power"] = None
            await tick(pv)
        else:
            pv.budget.wh = 50
            await tick(pv)
        if kind != "authority":
            await eventually(lambda: station.amps == 0)
        count = len(station.profiles)
        if kind in ("operator", "authority"):
            await pv.tick()
            assert len(station.profiles) == count
            assert not entry.runtime_data.settings.get("charge_release")
        else:
            assert entry.runtime_data.settings.get("charge_release")
        assert station.values["ChargingEnabled"] == "true"


async def test_economic_reset_and_night_low_household_restart_at_499_not_500(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 50
        await tick(pv)
        assert station.amps == 0 and pv.budget.stopped
        await power(station, 0)
        clock = [0]
        pv.monotonic = lambda: clock[0]
        pv.budget.interrupt()
        pv.values.update(
            grid_import_power=0,
            grid_export_power=0,
            storage_discharge_power=2000,
            leistung_pv=0,
            leistung_verbraucher=500,
        )
        for t in (0, 15, 16):
            clock[0] = t
            await tick(pv)
        assert pv.budget.reset and station.amps == 0
        pv.values["leistung_verbraucher"] = 499
        clock[0] = 21
        await tick(pv)
        assert station.amps == 6 and not pv.budget.stopped
        assert g.settings.get("charge_release")


async def test_balance_stop_a_only_real_applied_after_hold(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        pv.core.mode = "BALANCE"
        pv.values.update(soc_speicher_aktuell=48.9, storage_discharge_power=1)
        for _ in range(10):
            await tick(pv)
            assert station.amps == 6
        await tick(pv)
        assert station.amps == 0 and pv.core.mode == "STOP"
        pv.values.update(
            soc_speicher_aktuell=51,
            storage_discharge_power=0,
            storage_charge_power=1000,
        )
        await tick(pv)
        assert station.amps == 0
        pv.values["soc_speicher_aktuell"] = 51.1
        await tick(pv)
        assert station.amps > 0 and pv.core.mode == "BALANCE"
        assert g.settings.get("charge_release")


async def test_sparse_300_ticks_noise_and_invalid_recovery(caplog, ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        pv.values.update(soc_speicher_aktuell=50)
        pv.core.mode = "BALANCE"
        await power(station, 0)
        pv.values["storage_charge_power"] = 1380
        await tick(pv)
        await power(station, 1380)
        pv.values["storage_charge_power"] = 0
        pv.core.startup = None
        caplog.clear()
        with caplog.at_level(
            logging.INFO, logger="custom_components.wallbox_manager.pv"
        ):
            await tick(pv)
            count = len(caplog.records)
            requests = len(station.profiles)
            for n in range(300):
                pv.values["storage_charge_power"] = n % 2
                await tick(pv)
            assert len(caplog.records) == count
            assert len(station.profiles) == requests
            pv.values["storage_charge_power"] = None
            await tick(pv)
            failed = len(caplog.records)
            for _ in range(30):
                await tick(pv)
            assert len(caplog.records) == failed
            pv.values["storage_charge_power"] = 0
            await tick(pv)
            assert any("inputs_recovered" in r.message for r in caplog.records)
            assert all("transaction_id" not in r.message for r in caplog.records)
            assert all(r.levelno <= logging.INFO for r in caplog.records)


async def test_journal_survives_reload_without_downtime_or_release(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 51
        await tick(pv)
        assert pv.budget.stopped
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    pv = entry.runtime_data.pv
    assert pv.budget.wh == 51 and pv.budget.stopped
    assert pv.budget.previous is None and not pv.budget.reset
    assert not entry.runtime_data.settings.get("charge_release")
    assert pv.timer is None


async def test_runtime_setting_changes_no_second_tick_budget_edit_and_cycle_validation(
    ha,
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        s = entry.runtime_data.settings
        await tick(pv)
        pv.budget.wh = 2
        count = len(station.profiles)
        await s.set("pv_grid_import_budget_wh", 1)
        assert len(station.profiles) == count and pv.budget.wh == 2
        await tick(pv)
        assert station.amps == 0 and pv.budget.stopped
        await s.set("pv_grid_import_budget_wh", 100)
        assert pv.budget.stopped
        for key in (
            "pv_startup_hold_cycles",
            "pv_increase_interval_cycles",
            "pv_decrease_delay_cycles",
        ):
            with pytest.raises(ValueError, match="whole"):
                await s.set(key, 1.5)
        for key in ("pv_grid_import_budget_wh", "pv_ev_uptake_threshold_pct"):
            with pytest.raises(ValueError):
                await s.set(key, 0)


async def test_obsolete_keys_entities_and_option_fields_are_removed_only(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(
        entry, options=dict(pv_start_delay=77, pv_stop_delay=99)
    )
    await ha.install(entry)
    assert all(key not in entry.options for key in OBSOLETE_PV)
    assert all(e.role not in OBSOLETE_PV for e in entities(ha, entry))
    assert not set(OBSOLETE_PV) & NUMBERS.keys()
    for name in ("strings.json", "translations/en.json", "translations/de.json"):

        def load_text(name=name):
            with open("custom_components/wallbox_manager/" + name) as handle:
                return json.dumps(json.load(handle))

        text = await ha.async_add_executor_job(load_text)
        assert all(key not in text for key in OBSOLETE_PV)
        assert "storage_charge_power" in text


@pytest.mark.parametrize(
    "value,unit,role,expected",
    [
        ("1", "kW", "storage_charge_power", 1000),
        ("1", "kWh", "remaining_pv_energy", 1000),
        ("0", "W", "storage_charge_power", 0),
        ("unavailable", "W", "storage_charge_power", None),
        ("NaN", "W", "storage_charge_power", None),
        ("-1", "W", "storage_charge_power", None),
        ("0", "Wh", "storage_charge_power", None),
    ],
)
async def test_mapping_units_and_invalid_values(ha, value, unit, role, expected):
    ha.states.async_set("sensor.input", value, {"unit_of_measurement": unit})
    if expected is None:
        with pytest.raises(ValueError):
            reading(ha.states.get("sensor.input"), role)
    else:
        assert reading(ha.states.get("sensor.input"), role) == expected


async def test_phase_lockout_retains_confirmed_3p_minimum_and_tick_owns_retry(ha):
    from test_grid_profile import FakeClock

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await g.settings.set("allowed_current_1p", 0)
        await tick(pv)
        assert station.amps == 6 and station.phases == 3
        await power(station, 4140)
        pv.core.startup = None
        await g.settings.set("allowed_current_1p", 20)
        pv.core.mode = "BALANCE"
        pv.values.update(soc_speicher_aktuell=50, storage_discharge_power=1840)
        clock = FakeClock()
        g.wall_time, g.call_later = lambda: clock.now, clock.later
        station.profile_result = "Rejected"
        station.profile_info = dict(
            reason_code="PhaseSwitchLockout", additional_info="remainingSeconds=31"
        )
        await tick(pv)
        assert g.retry_deadline == clock.now + 36
        assert g.requested.phases == 1
        assert station.phases == 3 and station.amps == 6
        assert pv.core.mode == "BALANCE"
        count = len(station.profiles)
        await tick(pv)
        assert len(station.profiles) == count
        station.profile_result, station.profile_info = "Accepted", None
        await clock.advance(36)
        # Lockout expiry alone is not a second PV regulation/command pass.
        assert len(station.profiles) == count
        await tick(pv)
        assert station.phases == 1 and station.amps == 10
        assert g.confirmed == g.requested


async def test_unconfirmed_positive_and_zero_ack_never_stack_requests(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        station.readback = False
        await tick(pv)
        assert g.point_uncertain and g.status == "accepted_unconfirmed"
        count = len(station.profiles)
        for _ in range(8):
            await tick(pv)
        assert len(station.profiles) == count
        pv.budget.wh = 50
        await tick(pv)
        assert station.amps == 0
        count = len(station.profiles)
        for _ in range(8):
            await tick(pv)
        assert len(station.profiles) == count
        assert g.status == "accepted_unconfirmed" and pv.budget.stopped
        station.readback = True
        await tick(pv)
        assert not g.point_uncertain and g.confirmed.amps == 0


async def test_generic_rejection_is_not_a_repeating_retry(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        station.profile_result = "Rejected"
        await tick(pv)
        count = len(station.profiles)
        for _ in range(20):
            await tick(pv)
        assert len(station.profiles) == count


async def test_disk_failure_inhibits_positive_but_cannot_defer_required_stop(
    ha, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)

        async def failed_save(record):
            raise OSError("disk unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", failed_save)
            await tick(pv)
            assert station.amps == 0
        await tick(pv)
        assert station.amps > 0
        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", failed_save)
            pv.budget.wh = 51
            await tick(pv)
            assert station.amps == 0
            assert pv.budget.stopped


async def test_profile_changes_preserve_budget_until_netz_or_explicit_stop(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 51
        await tick(pv)
        await g.settings.set("charging_profile", "PV_MAXIMUM")
        assert pv.budget.stopped and pv.budget.wh == 51
        assert not g.settings.get("charge_release")
        await g.action()
        await g.stop_release()
        assert pv.budget.scope is None and pv.budget.wh == 0
        await g.settings.set("charging_profile", "NETZ")
        assert pv.budget.scope is None and pv.timer is None


async def test_connection_loss_has_no_commands_or_downtime_credit(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 23
        await station._connection.close()
        await eventually(lambda: not pv.state.connected)
        before = len(station.profiles)
        pv.values["soll_soc_speicher"] = 60
        for _ in range(5):
            await pv.tick()
        assert len(station.profiles) == before
        assert pv.budget.wh == 23 and pv.budget.previous is None
        assert pv.target == 50  # provisional HA-only T, never a queued point
        assert g.current_point() is None


async def test_existing_late_ack_fence_applies_to_pv_operator_stop(ha):
    from ocpp.v21 import call_result

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        adapter = g.listener.adapter
        original = adapter.request
        reached, resume = asyncio.Event(), asyncio.Event()

        async def delayed(payload):
            if (
                isinstance(payload, call.SetChargingProfile)
                and payload.charging_profile["charging_schedule"][0][
                    "charging_schedule_period"
                ][0]["limit"]
                > 0
            ):
                reached.set()
                await resume.wait()
                return call_result.SetChargingProfile(status="Accepted")
            return await original(payload)

        adapter.request = delayed
        await pv.tick()
        await reached.wait()
        await g.stop_release()
        resume.set()
        await asyncio.sleep(0)
        await eventually(lambda: g.task.done())
        assert not g.settings.get("charge_release")
        assert station.amps == 0
        assert pv.budget.scope is None


async def test_one_clock_only_and_inactive_has_no_periodic_regulation(ha):
    from test_grid_profile import FakeClock

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        clock = FakeClock()
        g.wall_time, g.call_later = lambda: clock.now, clock.later
        pv.monotonic = lambda: clock.now
        pv.next_due = None
        pv.lifecycle()
        count = len(station.profiles)
        for _ in range(10):
            pv.lifecycle()
        assert len([h for h in clock.calls if not h.cancelled]) == 1
        assert len(station.profiles) == count
        await clock.advance(5)
        await pv.task
        if g.task:
            await g.task
        assert len([h for h in clock.calls if not h.cancelled]) == 1
        assert station.amps == 6
        await g.stop_release()
        count = len(station.profiles)
        await clock.advance(500)
        assert len(station.profiles) == count and pv.timer is None


async def test_soc_stop_is_independent_of_unreconciled_economic_scope(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        pv.budget.scope = ["unreconciled"]
        pv.values["soc_speicher_aktuell"] = 47
        await tick(pv)
        assert station.amps == 0 and pv.core.mode == "STOP"
        assert pv.budget.scope == ["unreconciled"]


async def test_positive_one_watt_battery_restart_without_household_mapping(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 50
        await tick(pv)
        await power(station, 0)
        pv.budget.interrupt()
        now = [0]
        pv.monotonic = lambda: now[0]
        pv.values.update(
            storage_charge_power=1, storage_discharge_power=0, leistung_verbraucher=None
        )
        await tick(pv)
        now[0] = 16
        await tick(pv)
        assert station.amps == 6 and not pv.budget.stopped


async def test_both_positive_pair_does_not_rewrite_ha_sources_or_recover_economics(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 50
        await tick(pv)
        await power(station, 0)
        pv.budget.interrupt()
        now = [0]
        pv.monotonic = lambda: now[0]
        for role in ("storage_charge_power", "storage_discharge_power"):
            ha.states.async_set(entry.options[role], "1", {"unit_of_measurement": "W"})
        await asyncio.sleep(0)
        pv.values["leistung_verbraucher"] = 500
        await tick(pv)
        now[0] = 16
        await tick(pv)
        assert pv.budget.reset and pv.budget.stopped and station.amps == 0
        assert all(
            ha.states.get(entry.options[k]).state == "1"
            for k in ("storage_charge_power", "storage_discharge_power")
        )


async def test_pv_reconnect_requires_new_observations_and_regular_tick(ha):
    from test_active_control import ready

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 23
    await eventually(lambda: not pv.state.connected)
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        assert not g.settings.get(
            "charge_release"
        )  # Local observation retired old intent.
        assert not station.profiles
        await control.request("ControlAuthority", "OCPP")
        await station.call(
            call.TransactionEvent(
                event_type="Updated",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="MeterValuePeriodic",
                seq_no=1,
                transaction_info={
                    "transaction_id": "net-test",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        await eventually(lambda: g.current_point() is not None)
        await power(station, 0)
        await g.action()
        count = len(station.profiles)
        await asyncio.sleep(0)
        assert len(station.profiles) == count
        await tick(pv)
        assert station.amps == 6 and pv.budget.wh == 23
