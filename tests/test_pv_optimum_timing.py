"""Independent fast observation, transient grace and target planning clocks."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.pv_optimum import PVDay, target_soc


def active_policy(p, t):
    now = setup_optimum(p, t, soc=50)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "DYNAMIC")
    clock = [0]
    p.monotonic = lambda: clock[0]
    return now, clock


def set_sensor(p, key, value, unit="W"):
    p.hass.states.async_set(p.references[key], value, {"unit_of_measurement": unit})


async def test_target_initial_then_exact_five_minute_planning(grid):
    p, t, _ = grid
    _, clock = active_policy(p, t)
    p.setting(t)["regulation_interval"] = 17
    with patch(
        "custom_components.wallbox_manager.pv_optimum.target_soc", wraps=target_soc
    ) as calculate:
        p.optimum_policy(t)
        assert p.optimum_targets[t] == 40
        assert calculate.call_count == 1
        set_sensor(p, "remaining_pv_energy", 2211.8, "Wh")
        for tick in (1, 17, 90, 299.999):
            clock[0] = tick
            p.optimum_policy(t)
            assert p.optimum_targets[t] == 40
            assert calculate.call_count == 1
        clock[0] = 300
        p.optimum_policy(t)
        assert p.optimum_targets[t] == 60
        assert calculate.call_count == 2


async def test_soc_compares_against_cached_target_between_planning_cycles(grid):
    p, t, _ = grid
    _, clock = active_policy(p, t)
    p.optimum_policy(t)
    assert p.optimum_modes[t] == "FAST_DISCHARGE"
    deadline = p.optimum_plans[t][1]
    for tick, soc, mode in [
        (1, 40, "PV_BALANCE"),
        (2, 45, "PV_BALANCE"),
        (3, 46, "FAST_DISCHARGE"),
    ]:
        clock[0] = tick
        set_sensor(p, "soc_speicher_aktuell", soc, "%")
        p.optimum_refresh(datetime.now(UTC))
        assert p.optimum_modes[t] == mode
        assert p.optimum_plans[t][1] == deadline


async def test_surplus_and_finished_bypass_planning_deadline(grid):
    p, t, _ = grid
    now, clock = active_policy(p, t)
    p.optimum_days[t] = PVDay(
        dt_util.as_local(now).date().isoformat(), "BEFORE_SURPLUS"
    )
    set_sensor(p, "leistung_pv", 0)
    p.optimum_policy(t)
    assert p.optimum_targets[t] == 80
    clock[0] = 1
    set_sensor(p, "leistung_pv", 8000)
    p.optimum_policy(t)
    assert p.optimum_day_for(t).state == "DYNAMIC"
    assert p.optimum_targets[t] == 40
    set_sensor(p, "leistung_pv", 0)
    clock[0] = 2
    p.optimum_policy(t)
    assert p.optimum_day_for(t).state == "DYNAMIC"
    set_sensor(p, "remaining_pv_energy", 0, "Wh")
    p.optimum_policy(t)
    assert p.optimum_day_for(t).state == "FINISHED"
    assert p.optimum_targets[t] == 80


async def test_activation_and_reload_replan_immediately_and_observe_each_second(grid):
    p, t, _ = grid
    now, _ = active_policy(p, t)
    p.optimum_policy(t)
    set_sensor(p, "remaining_pv_energy", 0, "Wh")
    await p.select(t, "PV_OPTIMUM")
    assert p.optimum_targets[t] == 80
    set_sensor(p, "remaining_pv_energy", 4423.6, "Wh")
    from custom_components.wallbox_manager.profiles import target_key

    await p.optimum_day_store.async_save(
        {
            "version": 2,
            "days": {
                target_key(t): {
                    "date": dt_util.as_local(now).date().isoformat(),
                    "state": "DYNAMIC",
                }
            },
        }
    )
    with patch(
        "homeassistant.helpers.event.async_track_time_interval",
        return_value=lambda: None,
    ) as track:
        await p.load()
        assert track.call_args.args[2] == timedelta(seconds=1)
        assert p.optimum_targets[t] == 40
        set_sensor(p, "soc_speicher_aktuell", 40, "%")
        track.call_args.args[1](datetime.now(UTC))
        assert p.optimum_modes[t] == "PV_BALANCE"


async def test_cached_target_never_authorizes_stale_forecast(grid):
    p, t, _ = grid
    _, clock = active_policy(p, t)
    p.optimum_policy(t)
    clock[0] = 1
    state = p.hass.states.get(p.references["remaining_pv_energy"])
    p.hass.states.async_set(
        state.entity_id,
        state.state,
        {
            **state.attributes,
            "valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert p.pv_request(t)[2] == "measurements_unavailable"
    assert p.pv_plan(t)[3] is None


@pytest.mark.parametrize("interval", [5, 17, 300])
async def test_fast_loop_one_second_and_no_extra_command_debounce(grid, interval):
    p, t, _ = grid
    _, clock = active_policy(p, t)
    p.setting(t)["regulation_interval"] = interval
    waits = []
    p.debounce_wait = AsyncMock()
    valid = p.valid
    p.valid = lambda target, epoch: clock[0] < 3 and valid(target, epoch)

    async def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    p.wait = wait
    await p.permission(t, True)
    await p.tasks[t]
    assert waits == [1, 1, 1]
    p.debounce_wait.assert_not_called()
    assert p.optimum_plans[t][1] == 300
    p.optimum_modes[t] = "PV_BALANCE"
    p.pv_expiry.pop(t, None)  # No earlier freshness deadline in this assertion.
    assert p.pv_wait_seconds(t) == interval


async def test_soc_transition_wakes_balance_loop_without_new_authority(grid):
    p, t, (_, _, peer, *_) = grid
    active_policy(p, t)
    set_sensor(p, "soc_speicher_aktuell", 40, "%")
    p.optimum_policy(t)
    p.optimum_wakes[t].clear()
    entered = asyncio.Event()

    async def wait(seconds):
        entered.set()
        await asyncio.Event().wait()

    p.wait = wait
    task = asyncio.create_task(p.optimum_wait(t, 300))
    await entered.wait()
    count = len(peer.requests)
    set_sensor(p, "soc_speicher_aktuell", 46, "%")
    p.optimum_refresh(datetime.now(UTC))
    await asyncio.wait_for(task, 1)
    assert p.optimum_modes[t] == "FAST_DISCHARGE"
    assert len(peer.requests) == count  # Policy observation alone cannot send commands.


async def test_observer_tracks_grace_while_command_execution_is_pending(grid):
    p, t, (c, _, peer, *_) = grid
    _, clock = active_policy(p, t)
    p.setting(t)["optimum_max_discharge_w"] = 4800
    set_sensor(p, "storage_discharge_power", 2000)
    set_sensor(p, "grid_import_power", 2500)
    # No command execution or solver call is authorized by the observer, even
    # when the normal PV task is blocked by an outstanding serialized operation.
    count = len(peer.requests)
    with patch.object(c, "pending_points", {t: object()}):
        for tick in (0, 1, 2, 3):
            clock[0] = tick
            p.optimum_refresh(datetime.now(UTC))
            regulator = p.optimum_regulators[t]
            assert regulator.import_since == 0
            assert regulator.requested == (3000 if tick < 3 else 500)
    assert len(peer.requests) == count


async def test_missing_actual_power_resets_grace_but_keeps_independent_target(grid):
    p, t, _ = grid
    _, clock = active_policy(p, t)
    set_sensor(p, "grid_import_power", 1000)
    p.optimum_refresh(datetime.now(UTC))
    assert p.optimum_regulators[t].import_since == 0
    p.hass.states.async_remove("sensor.selected")
    clock[0] = 1
    p.optimum_refresh(datetime.now(UTC))
    assert t not in p.optimum_regulators
    assert p.optimum_targets[t] == 40
