"""Remaining-day reserve planning, fresh selected-EV subtraction and persistence."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.profiles import target_key
from custom_components.wallbox_manager.pv_optimum import (
    PVDay,
    remaining_day_seconds,
    remaining_house_energy,
)


@pytest.mark.parametrize(
    "pv,load,actual,phase",
    [
        (0, 500, 0, "BEFORE_SURPLUS"),
        (100, 500, 0, "BEFORE_SURPLUS"),
        (500, 500, 0, "BEFORE_SURPLUS"),
        (501, 500, 0, "DYNAMIC"),
        (501, 3500, 3000, "DYNAMIC"),
    ],
)
async def test_first_surplus_uses_selected_ev_exclusion(grid, pv, load, actual, phase):
    p, t, _ = grid
    now = setup_optimum(p, t, pv=pv, load=load, actual=actual)
    p.optimum_target(t, now)
    assert p.optimum_day_for(t).state == phase
    assert p.optimum_targets[t] == (40 if phase == "DYNAMIC" else 80)


@pytest.mark.parametrize(
    "forecast,expected", [(999, "FINISHED"), (1000, "FINISHED"), (1100, "DYNAMIC")]
)
async def test_exhausted_forecast_latches_without_waiting_for_cache(
    grid, monkeypatch, forecast, expected
):
    p, t, _ = grid
    setup_optimum(p, t)
    p.setting(t)["estimated_daily_house_consumption_kwh"] = 24
    monkeypatch.setattr(
        "custom_components.wallbox_manager.pv_optimum.remaining_day_seconds",
        lambda _: 3600,
    )
    p.optimum_target(t, datetime.now(UTC))
    assert p.optimum_day_for(t).state == "DYNAMIC"
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", forecast, {"unit_of_measurement": "Wh"}
    )
    p.optimum_target(t, datetime.now(UTC))
    assert p.optimum_day_for(t).state == expected
    if expected == "FINISHED":
        p.hass.states.async_set(
            "sensor.remaining_pv_energy", 99999, {"unit_of_measurement": "Wh"}
        )
        assert p.optimum_target(t, datetime.now(UTC)) == 80
        assert p.optimum_day_for(t).state == "FINISHED"


@pytest.mark.parametrize("phase", ["BEFORE_SURPLUS", "DYNAMIC", "FINISHED"])
async def test_phase_reload_and_midnight(grid, phase):
    p, t, _ = grid
    now = setup_optimum(p, t, pv=0)
    today = dt_util.as_local(now).date().isoformat()
    await p.optimum_day_store.async_save(
        {"version": 2, "days": {target_key(t): {"date": today, "state": phase}}}
    )
    await p.load()
    assert p.optimum_day_for(t).state == phase
    tomorrow = now + timedelta(days=1)
    assert p.optimum_target(t, tomorrow) == 80
    assert p.optimum_day_for(t).state == "BEFORE_SURPLUS"


@pytest.mark.parametrize("phase", ["BEFORE_SURPLUS", "FINISHED"])
async def test_fixed_target_independent_of_forecast_but_commands_still_validate(
    grid, phase
):
    p, t, _ = grid
    now = setup_optimum(p, t, pv=0)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), phase)
    p.hass.states.async_remove("sensor.remaining_pv_energy")
    assert p.optimum_target(t, now) == 80
    assert p.pv_plan(t)[3] is None


async def test_clouds_never_end_dynamic_and_unknown_ev_is_not_zero(grid):
    p, t, (c, *_rest) = grid
    now = setup_optimum(p, t)
    p.optimum_target(t, now)
    measurements(p, t, pv=0)
    p.monotonic = lambda: 1800
    p.optimum_target(t, datetime.now(UTC))
    assert p.optimum_day_for(t).state == "DYNAMIC"
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat())
    p.hass.states.async_remove("sensor.selected")
    measurements(p, t, pv=10000)
    p.hass.states.async_remove("sensor.selected")
    enabled = c.runtime.enabled
    c.runtime.enabled = lambda _: None
    c.runtime.physical_state_fresh = lambda _: False
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat())
    p.optimum_target(t, datetime.now(UTC))
    assert p.optimum_day_for(t).state == "BEFORE_SURPLUS"
    c.runtime.enabled = (
        enabled  # confirmed permission OFF is authoritative no-load proof
    )
    p.optimum_target(t, datetime.now(UTC))
    assert p.optimum_day_for(t).state == "DYNAMIC"


@pytest.mark.parametrize("month,day,hours", [(3, 29, 23), (10, 25, 25), (10, 6, 24)])
def test_remaining_local_day_obeys_dst(monkeypatch, month, day, hours):
    zone = ZoneInfo("Europe/Vienna")
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", zone)
    local = datetime(2026, month, day, tzinfo=zone)
    seconds = remaining_day_seconds(local.astimezone(UTC))
    assert seconds == hours * 3600
    assert remaining_house_energy(24, seconds) == Fraction(hours * 1000)


async def test_household_event_starts_planning_and_exhaustion_needs_no_capacity(grid):
    p, t, _ = grid
    now = setup_optimum(p, t, pv=500, load=1000, actual=0)
    p.optimum_target(t, now)
    assert p.optimum_day_for(t).state == "BEFORE_SURPLUS"
    p.hass.states.async_set("sensor.load", 100, {"unit_of_measurement": "W"})
    assert p.optimum_day_for(t).state == "DYNAMIC"
    p.hass.states.async_remove("sensor.storage_capacity")
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", 0, {"unit_of_measurement": "Wh"}
    )
    assert p.optimum_day_for(t).state == "FINISHED"
    assert p.optimum_targets[t] == 80
    assert p.pv_plan(t)[3] is None
