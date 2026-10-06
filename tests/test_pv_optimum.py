"""Optimum policy, continuous day evidence and shared runtime integration."""

import asyncio
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import ANY, patch

import pytest
from homeassistant.util import dt as dt_util
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.pv_optimum import (
    OPTIMUM_REFERENCES,
    PVDay,
    remaining_house_energy,
    target_soc,
)
from custom_components.wallbox_manager.pv_surplus import reading


@pytest.mark.parametrize(
    "forecast,house,expected",
    [(4423.6, 0, 40), (10000, 0, 20), (0, 0, 80), (100, 200, 80)],
)
def test_dynamic_target_capacity_and_clamps(forecast, house, expected):
    assert target_soc(20, 80, 11059, Fraction(str(forecast)), house) == expected


def test_linear_house_forecast_integrates_remaining_day_seconds():
    assert remaining_house_energy(24, 6 * 3600) == 6000
    assert remaining_house_energy(24, -3600) == 0


@pytest.mark.parametrize("unit,value", [("Wh", "11059"), ("kWh", "11.059")])
def test_capacity_energy_units(unit, value):
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state=value, attributes={"unit_of_measurement": unit}, last_updated=now
    )
    assert reading(state, now, energy=True) == 11059


def test_daily_surplus_latch_and_midnight():
    day = PVDay()
    day.observe("2026-10-04", -1)
    assert day.state == "BEFORE_SURPLUS"
    day.observe("2026-10-04", 0)
    assert day.state == "BEFORE_SURPLUS"
    day.observe("2026-10-04", 1)
    assert day.state == "DYNAMIC"
    for surplus in (-10000, 0, None):
        day.observe("2026-10-04", surplus)
        assert day.state == "DYNAMIC"
    day.state = "FINISHED"
    day.observe("2026-10-04", 10000)
    assert day.state == "FINISHED"
    day.observe("2026-10-05")
    assert day.state == "BEFORE_SURPLUS"


def setup_optimum(p, t, *, soc=90, pv=8000, load=5000, actual=3000):
    measurements(p, t, pv=pv, load=load, actual=actual, soc=soc)
    from custom_components.wallbox_manager.core.models import PhaseMode

    p.control.capability_source.mode = PhaseMode.canonical(1)
    for key, value, unit in (
        ("storage_discharge_power", 1000, "W"),
        ("storage_capacity", 11059, "Wh"),
        ("remaining_pv_energy", 4423.6, "Wh"),
        ("grid_import_power", 0, "W"),
        ("grid_export_power", 0, "W"),
    ):
        p.references[key] = f"sensor.{key}"
        p.hass.states.async_set(f"sensor.{key}", value, {"unit_of_measurement": unit})
    p.setting(t).update(
        profile="PV_OPTIMUM",
        optimum_lower_soc=20,
        optimum_upper_soc=80,
        optimum_max_discharge_w=3500,
        estimated_daily_house_consumption_kwh=0,
    )
    now = datetime.now(UTC)
    p.hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {
            "next_setting": (now + timedelta(hours=1)).isoformat(),
        },
    )
    return datetime.now(UTC)


async def test_before_start_and_after_end_target_upper_night_consumption_allowed(grid):
    p, t, _ = grid
    now = setup_optimum(p, t, soc=70, pv=0)
    p.pv_request(t)
    assert p.optimum_targets[t] == 80
    assert p.optimum_modes[t] == "PV_BALANCE"
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "FINISHED")
    p.pv_request(t)
    assert p.optimum_targets[t] == 80 and p.optimum_modes[t] == "PV_BALANCE"
    assert p.battery.record is None
    assert p.battery.diagnostics.get("write_count", 0) == 0


async def test_dynamic_policy_and_regulator_selection_hysteresis(grid):
    p, t, _ = grid
    now = setup_optimum(p, t, soc=50)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "DYNAMIC")
    with patch(
        "custom_components.wallbox_manager.pv_regulators.FastDischargeRegulator.request",
        return_value=4000,
    ) as fast:
        assert p.pv_request(t) == (4000, Direction.DOWN, "actively_charging")
        fast.assert_called_once_with(3000, 1000, 3500, 0, 0, now=ANY, interval=5)
    assert p.optimum_targets[t] == 40
    for soc, mode in [
        (42, "FAST_DISCHARGE"),
        (40, "PV_BALANCE"),
        (45, "PV_BALANCE"),
        (46, "FAST_DISCHARGE"),
    ]:
        measurements(p, t, soc=soc)
        p.pv_request(t)
        assert p.optimum_modes[t] == mode


async def test_target_and_reentry_independent_of_ev_power_or_connection(grid):
    p, t, (c, *_) = grid
    now = setup_optimum(p, t, soc=40)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "DYNAMIC")
    p.hass.states.async_remove("sensor.selected")
    p.optimum_refresh(now)
    assert p.optimum_targets[t] == 40
    assert p.optimum_modes[t] == "PV_BALANCE"
    p.hass.states.async_set(
        p.references["soc_speicher_aktuell"], 60, {"unit_of_measurement": "%"}
    )
    p.optimum_refresh(datetime.now(UTC))
    assert p.optimum_modes[t] == "FAST_DISCHARGE"
    assert p.pv_request(t)[2] == "measurements_unavailable"
    measurements(p, t, soc=60)
    assert p.pv_request(t)[0] > 3000
    assert p.optimum_modes[t] == "FAST_DISCHARGE"
    assert c.runtime.enabled(t) is False


@pytest.mark.parametrize("key", OPTIMUM_REFERENCES)
@pytest.mark.parametrize("kind", ["missing", "stale"])
async def test_missing_or_stale_input_blocks_new_request(grid, key, kind):
    p, t, _ = grid
    setup_optimum(p, t)
    entity = p.references[key]
    if kind == "missing":
        p.hass.states.async_remove(entity)
    else:
        state = p.hass.states.get(entity)
        p.hass.states.async_set(
            entity,
            state.state,
            {
                **state.attributes,
                "valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
    assert p.pv_request(t)[2] == "measurements_unavailable"
    assert p.pv_plan(t)[3] is None


async def test_independent_profile_targets_and_persistence(grid):
    p, t, _ = grid
    setup_optimum(p, t)
    p.pv_request(t)
    before = p.optimum_targets[t]
    await p.set_value(t, "soll_soc_speicher", 10)
    p.pv_request(t)
    assert p.optimum_targets[t] == before
    await p.set_value(t, "optimum_upper_soc", 75)
    assert p.setting(t)["soll_soc_speicher"] == 10
    await p.save()
    p.settings.clear()
    await p.load()
    assert p.setting(t)["profile"] == "PV_OPTIMUM"
    assert p.setting(t)["optimum_upper_soc"] == 75
    assert p.setting(t)["optimum_max_discharge_w"] == 3500


async def test_discrete_solver_and_zero_pause_preserve_permission(grid):
    p, t, (c, _, peer, *_) = grid
    setup_optimum(p, t, soc=90)
    clock = [0]
    p.monotonic = lambda: clock[0]
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(t, True)
    point = c.confirmed_point(t)
    assert point.charging and point.current_a.denominator == 1
    assert point.offered_power_w <= p.pv_request(t)[0]
    assert c.runtime.enabled(t) is True
    measurements(p, t, pv=0, load=5000, actual=3000, soc=30)
    result = p.pv_edit(t)
    assert result.point.charging and p.status[t] == "optimum_pause_pending"
    clock[0] = p.setting(t)["regulation_interval"]
    result = p.pv_edit(t)
    assert result.point.offered_power_w == 0
    await c.apply_stored(t)
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).offered_power_w == 0
    assert (
        peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][0][
            "limit"
        ]
        == 0
    )


async def test_configuration_never_takes_authority(grid):
    p, t, (c, bound, peer, *_) = grid
    setup_optimum(p, t)
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    count = len(peer.requests)
    await p.set_value(t, "optimum_upper_soc", 79)
    await p.permission(t, True)
    assert len(peer.requests) == count
    assert c.runtime.authority(t.station) == ControlAuthority.LOCAL


async def test_remaining_day_house_and_finished_latch(grid):
    from custom_components.wallbox_manager.pv_optimum import remaining_day_seconds

    p, t, _ = grid
    now = setup_optimum(p, t)
    p.setting(t)["estimated_daily_house_consumption_kwh"] = 1
    house = remaining_house_energy(1, remaining_day_seconds(now))
    p.optimum_policy(t, now)
    assert p.optimum_targets[t] == 80 - (Fraction("4423.6") - house) / 11059 * 100
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", 0, {"unit_of_measurement": "Wh"}
    )
    p.optimum_policy(t)
    assert p.optimum_day_for(t).state == "FINISHED"
    assert p.optimum_targets[t] == 80
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", 99999, {"unit_of_measurement": "Wh"}
    )
    p.optimum_policy(t)
    assert p.optimum_targets[t] == 80


async def test_finished_restored_and_legacy_not_trusted(grid):
    from custom_components.wallbox_manager.profiles import target_key

    p, t, _ = grid
    now = setup_optimum(p, t, pv=0)
    date = dt_util.as_local(now).date().isoformat()
    await p.optimum_day_store.async_save(
        {"version": 2, "days": {target_key(t): {"date": date, "state": "FINISHED"}}}
    )
    await p.load()
    assert p.optimum_day_for(t).state == "FINISHED"
    await p.optimum_day_store.async_save({"date": date, "state": "ended"})
    await p.load()
    assert p.optimum_day_for(t).state == "BEFORE_SURPLUS"


async def test_balance_reuses_regulator_and_never_uses_surplus_upper_approximation(
    grid,
):
    p, t, _ = grid
    now = setup_optimum(p, t, soc=50, pv=1, load=0, actual=0)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "FINISHED")
    p.setting(t).update(soll_soc_speicher=10, approximation="up", pv_stop_delay=3600)
    with patch(
        "custom_components.wallbox_manager.pv_surplus.pv_balance", return_value=1
    ) as balance:
        power, direction, _, result = p.pv_plan(t)
        balance.assert_called_once_with(1, 0, 0)
    assert direction == Direction.DOWN
    assert power == 0 and not result.point.charging


async def test_other_profile_parameter_changes_do_not_invalidate_control(grid):
    p, t, _ = grid
    setup_optimum(p, t)
    epoch = p.epochs.get(t, 0)
    await p.set_value(t, "soll_soc_speicher", 15)
    assert p.epochs.get(t, 0) == epoch
    p.setting(t)["profile"] = "PV_SURPLUS"
    await p.set_value(t, "optimum_upper_soc", 77)
    assert p.epochs.get(t, 0) == epoch


@pytest.mark.parametrize(
    "field,value",
    [
        ("optimum_lower_soc", 81),
        ("optimum_upper_soc", 19),
        ("optimum_max_discharge_w", -1),
        ("estimated_daily_house_consumption_kwh", -1),
    ],
)
async def test_optimum_parameter_validation(grid, field, value):
    p, t, _ = grid
    setup_optimum(p, t)
    with pytest.raises(ValueError):
        await p.set_value(t, field, value)


async def test_battery_supported_start_crosses_minimum_via_common_solver(grid):
    p, t, _ = grid
    setup_optimum(p, t, soc=90, pv=200, load=200, actual=0)
    p.hass.states.async_set(
        "sensor.storage_discharge_power", 0, {"unit_of_measurement": "W"}
    )
    clock = [0]
    p.monotonic = lambda: clock[0]
    initial = p.pv_edit(t).point
    assert initial.charging and initial.current_a == 6 and initial.mode.count == 1
    clock[0] = 5
    point = p.pv_edit(t).point
    assert point.charging and point.current_a == 6 and point.mode.count == 1
    assert point.offered_power_w <= 3500
