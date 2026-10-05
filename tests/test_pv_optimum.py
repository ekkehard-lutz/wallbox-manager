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


def test_linear_house_forecast_uses_sunset_not_midnight():
    assert remaining_house_energy(24, 6 * 3600) == 6000
    assert remaining_house_energy(24, -3600) == 0


@pytest.mark.parametrize("unit,value", [("Wh", "11059"), ("kWh", "11.059")])
def test_capacity_energy_units(unit, value):
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state=value, attributes={"unit_of_measurement": unit}, last_updated=now
    )
    assert reading(state, now, energy=True) == 11059


def observe(day, start, seconds, watts, valid=True):
    now = start + timedelta(seconds=seconds)
    day.observe(
        now,
        now.date().isoformat(),
        watts if valid else None,
        now + timedelta(seconds=90) if valid else now,
    )


def test_day_threshold_continuity_clouds_end_and_midnight():
    day = PVDay()
    start = datetime(2026, 10, 4, 8, tzinfo=UTC)
    observe(day, start, 0, 99)
    assert day.state == "before"
    observe(day, start, 10, 100)
    observe(day, start, 20, 99)
    for seconds in range(30, 330, 30):
        observe(day, start, seconds, 100)
        assert day.state == "before"
    observe(day, start, 330, 100)
    assert day.state == "active"
    for seconds in range(360, 900, 30):
        observe(day, start, seconds, 99)
    observe(day, start, 900, 100)
    assert day.state == "active" and day.since is None
    for seconds in range(930, 1830, 30):
        observe(day, start, seconds, 0)
        assert day.state == "active"
    observe(day, start, 1830, 0)
    assert day.state == "ended"
    for seconds in range(1860, 2400, 30):
        observe(day, start, seconds, 500)
    assert day.state == "ended"
    tomorrow = start + timedelta(days=1)
    observe(day, tomorrow, 0, 100)
    assert day.state == "before"


@pytest.mark.parametrize("explicit_gap", [True, False])
def test_missing_or_stale_evidence_breaks_start_debounce(explicit_gap):
    day = PVDay()
    start = datetime(2026, 10, 4, 8, tzinfo=UTC)
    observe(day, start, 0, 100)
    if explicit_gap:
        observe(day, start, 60, 100, valid=False)
    observe(day, start, 300, 100)
    assert day.state == "before" and day.since == start + timedelta(seconds=300)


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
    now = setup_optimum(p, t, soc=70)
    p.pv_request(t)
    assert p.optimum_targets[t] == 80
    assert p.optimum_modes[t] == "PV_BALANCE"
    p.optimum_day = PVDay(dt_util.as_local(now).date().isoformat(), "ended")
    p.pv_request(t)
    assert p.optimum_targets[t] == 80 and p.optimum_modes[t] == "PV_BALANCE"
    assert p.battery.record is None
    assert p.battery.diagnostics.get("write_count", 0) == 0


async def test_dynamic_policy_and_regulator_selection_hysteresis(grid):
    p, t, _ = grid
    now = setup_optimum(p, t, soc=50)
    p.optimum_day = PVDay(dt_util.as_local(now).date().isoformat(), "active")
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
    p.optimum_day = PVDay(dt_util.as_local(now).date().isoformat(), "active")
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
    measurements(p, t, pv=0, load=5000, actual=3000, soc=70)
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


async def test_sunset_horizon_house_model_and_next_setting_rollover(grid):
    p, t, _ = grid
    setup_optimum(p, t)
    # Explicit future evaluation with refreshed fixture timestamps avoids wall-clock
    # time-of-day assumptions and exercises HA's next_setting date rollover.
    now = datetime.now(UTC).replace(
        hour=12, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    lookup = {}
    for key in OPTIMUM_REFERENCES:
        state = p.hass.states.get(p.references[key])
        lookup[p.references[key]] = SimpleNamespace(
            state=state.state, attributes=state.attributes, last_updated=now
        )
    sunset = now + timedelta(hours=6)
    lookup["sun.sun"] = SimpleNamespace(
        state="above_horizon",
        attributes={"next_setting": sunset.isoformat()},
        last_updated=now,
    )
    p.setting(t)["estimated_daily_house_consumption_kwh"] = 24
    p.optimum_day = PVDay(dt_util.as_local(now).date().isoformat(), "active")
    with patch.object(type(p.hass.states), "get", side_effect=lookup.get):
        p.optimum_policy(t, now)
        assert p.optimum_targets[t] == 80  # 6 kWh house > 4.4236 kWh PV.
        p.setting(t)["estimated_daily_house_consumption_kwh"] = 4
        p.optimum_policy(t, now)
        assert p.optimum_targets[t] == 80 - Fraction("3423.6") / 11059 * 100
        lookup["sun.sun"].state = "below_horizon"
        lookup["sun.sun"].attributes["next_setting"] = (
            sunset + timedelta(days=1)
        ).isoformat()
        p.monotonic = lambda: p.optimum_plans[t][1]
        p.optimum_policy(t, now)
        assert p.optimum_targets[t] == 40  # No tomorrow-household budget.
        lookup["sun.sun"].last_updated = now - timedelta(seconds=91)
        with pytest.raises(ValueError, match="stale sun"):
            p.optimum_policy(t, now)


async def test_day_end_restored_without_debounce_or_second_day(grid):
    p, t, _ = grid
    now = setup_optimum(p, t)
    date = dt_util.as_local(now).date().isoformat()
    await p.optimum_day_store.async_save({"date": date, "state": "ended"})
    await p.load()
    assert p.optimum_day.state == "ended"
    assert p.optimum_day.since is None
    p.optimum_observe_day()
    assert p.optimum_day.state == "ended"
    assert p.optimum_unsubscribe is not None


async def test_balance_reuses_regulator_and_never_uses_surplus_upper_approximation(
    grid,
):
    p, t, _ = grid
    setup_optimum(p, t, soc=50, pv=1, load=0, actual=0)
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
