"""All profile adapters share SoC decisions without changing the daily planner."""

import logging
from datetime import UTC, datetime
from fractions import Fraction
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.diagnostics import event_record
from custom_components.wallbox_manager.profiles import target_key
from custom_components.wallbox_manager.pv_optimum import PVDay
from custom_components.wallbox_manager.pv_soc import PV_PROFILES


def configure(p, t, profile):
    setup_optimum(p, t, soc=80, actual=0)
    p.setting(t).update(profile=profile, soll_soc_speicher=80)
    if profile == "PV_MAXIMUM":
        p.hass.states.async_set("number.reserve", 78)
    p.optimum_days[t] = PVDay(
        dt_util.as_local(datetime.now(UTC)).date().isoformat(), "FINISHED"
    )
    p.pv_sessions[t] = p.control.runtime.sessions.get(t).session_id
    p.optimum_initializations.discard(t)


@pytest.mark.parametrize("profile", PV_PROFILES)
@pytest.mark.parametrize("previous", ["STOP", "PV_BALANCE", "FAST_DISCHARGE"])
@pytest.mark.parametrize(
    "soc", [78.999, 79, 79.001, 80.999, 81, 81.001, 81.999, 82, 82.001]
)
@pytest.mark.parametrize("surplus", [False, True])
async def test_profile_adapters_boundaries(grid, profile, previous, soc, surplus):
    p, t, _ = grid
    configure(p, t, profile)
    measurements(p, t, soc=soc, actual=0, pv=1000 if surplus else 500, load=500)
    p.optimum_modes[t] = previous
    p.pv_ongoing[t] = previous != "STOP"
    p.pv_request(t)
    if soc <= 79:
        expected = "STOP"
    elif soc >= 82:
        expected = "FAST_DISCHARGE"
    elif soc < 81:
        expected = "STOP" if previous == "STOP" else "PV_BALANCE"
    elif previous != "STOP":
        expected = previous
    else:
        expected = "PV_BALANCE" if surplus else "STOP"
    assert p.optimum_targets[t] == 80
    assert p.optimum_modes[t] == expected


@pytest.mark.parametrize("profile", PV_PROFILES)
async def test_new_or_paused_has_no_fast_initialization_shortcut(grid, profile):
    p, t, _ = grid
    configure(p, t, profile)
    p.optimum_modes[t] = "FAST_DISCHARGE"
    p.optimum_initialize(t)
    measurements(p, t, soc=80.5, pv=8000, load=100, actual=0)
    assert p.pv_request(t)[0] == 0
    assert p.optimum_modes[t] == "STOP"
    measurements(p, t, soc=82, pv=0, load=100, actual=0)
    with patch(
        "custom_components.wallbox_manager.pv_regulators.FastDischargeRegulator.request",
        return_value=2000,
    ) as regulator:
        assert p.pv_request(t)[0] == 2000
        regulator.assert_called_once()
    assert p.optimum_modes[t] == "FAST_DISCHARGE"


async def test_maximum_target_tracks_live_reserve_and_blocks_impossible_range(grid):
    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    p.hass.states.async_set("number.reserve", 5)
    assert p.pv_target(t, datetime.now(UTC)) == 7
    p.hass.states.async_set("number.reserve", 20)
    assert p.pv_target(t, datetime.now(UTC)) == 22
    p.hass.states.async_set("number.reserve", 99)
    assert p.pv_request(t)[0] == 0
    assert p.optimum_modes[t] == "STOP"


async def test_dynamic_optimum_target_obeys_global_bounds_without_forcing_lower(grid):
    p, t, _ = grid
    configure(p, t, "PV_OPTIMUM")
    p.setting(t).update(optimum_lower_soc=30, optimum_upper_soc=80)
    p.optimum_days[t].state = "DYNAMIC"
    assert p.pv_target(t, datetime.now(UTC)) == 40
    assert p.setting(t)["optimum_lower_soc"] == 30
    p.hass.states.async_set("number.reserve", 50)
    assert p.pv_target(t, datetime.now(UTC)) == 52
    assert p.setting(t)["optimum_lower_soc"] == 52


@pytest.mark.parametrize("hysteresis", [2, 5, 99])
async def test_legacy_targets_survive_load_and_save_deterministically(grid, hysteresis):
    p, t, _ = grid
    configure(p, t, "PV_OPTIMUM")
    legacy = {
        **p.setting(t),
        "soc_hysterese": hysteresis,
        "optimum_lower_soc": 1,
        "optimum_upper_soc": 100,
        "soll_soc_speicher": 100,
    }
    p.entry.options = {**p.references, "soc_hysterese": hysteresis, "pv_stop_delay": 0}
    await p.store.async_save({target_key(t): legacy})
    p.settings.clear()
    await p.load()
    settings = p.setting(t)
    assert settings["profile"] == "PV_OPTIMUM"
    assert settings["soc_hysterese"] == hysteresis
    if hysteresis <= 5:
        assert settings["optimum_lower_soc"] == 5 + hysteresis
        assert settings["optimum_upper_soc"] == 100 - hysteresis
        assert settings["soll_soc_speicher"] == 100 - hysteresis
    else:
        assert p.pv_request(t)[0] == 0
    assert settings["legacy_soc_targets"]["optimum_lower_soc"] == 1
    await p.save()
    p.settings.clear()
    await p.load()
    assert p.setting(t) == settings


@pytest.mark.parametrize(
    "field,value",
    [
        ("soll_soc_speicher", 6),
        ("soll_soc_speicher", 99),
        ("optimum_lower_soc", 6),
        ("optimum_lower_soc", 81),
        ("optimum_upper_soc", 19),
        ("optimum_upper_soc", 99),
    ],
)
async def test_live_bounds_and_order_validation(grid, field, value):
    p, t, _ = grid
    configure(p, t, "PV_OPTIMUM")
    with pytest.raises(ValueError):
        await p.set_value(t, field, value)


@pytest.mark.parametrize("level", [1, 2, 3])
async def test_soc_transition_context_and_measurement_deduplication(
    grid, caplog, level
):
    p, t, _ = grid
    configure(p, t, "PV_SURPLUS")
    p.entry.options = {"diagnostic_level": level}
    caplog.set_level(logging.INFO)
    p.common_soc_policy(t, Fraction(82), Fraction(80), datetime.now(UTC))
    p.common_soc_policy(t, Fraction(83), Fraction(80), datetime.now(UTC))
    messages = [r.message for r in caplog.records if '"event":"soc_mode"' in r.message]
    assert len(messages) == 1
    assert '"previous_mode":"STOP"' in messages[0]
    if level >= 2:
        for key in (
            "target_soc",
            "lower_stop_threshold",
            "pv_start_threshold",
            "fast_start_threshold",
            "pv_start_evidence",
        ):
            assert key in messages[0]
    fields = dict(
        event="soc_mode", mode="FAST_DISCHARGE", reason="fast_start_threshold"
    )
    assert event_record(p.entry, "test", {**fields, "soc": 82, "target_soc": 80})
    assert (
        event_record(p.entry, "test", {**fields, "soc": 83, "target_soc": 80.1}) is None
    )


@pytest.mark.parametrize("profile", PV_PROFILES)
async def test_proven_soc_stop_does_not_require_power_measurements(grid, profile):
    p, t, _ = grid
    configure(p, t, profile)
    measurements(p, t, soc=79)
    p.pv_ongoing[t] = True
    p.optimum_modes[t] = "FAST_DISCHARGE"
    for entity in (
        "sensor.pv",
        "sensor.load",
        "sensor.selected",
        "sensor.grid_import_power",
    ):
        p.hass.states.async_remove(entity)
    assert p.pv_request(t)[0] == 0
    assert p.pv_plan(t)[3].point.charging is False
    assert p.optimum_modes[t] == "STOP"


async def test_maximum_uses_real_enable_and_zero_current_pause(grid):
    import asyncio

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (control, _, peer, *_) = grid
    configure(p, t, "PV_MAXIMUM")
    measurements(p, t, soc=90, actual=0)
    # This lifecycle test starts with sufficient conservative battery headroom.
    p.hass.states.async_set(
        "sensor.storage_discharge_power", 0, {"unit_of_measurement": "W"}
    )
    p.wait = lambda _: asyncio.Event().wait()
    assert "PV_MAXIMUM" in p.available_profiles(t)
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert control.confirmed_point(t).charging
    measurements(p, t, soc=79, actual=0)
    assert not p.pv_edit(t).point.charging
    await control.apply_stored(t)
    assert control.runtime.enabled(t) is True
    assert not control.confirmed_point(t).charging
    assert (
        peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][0][
            "limit"
        ]
        == 0
    )


async def test_planner_target_changes_remain_distinct_events(grid):
    p, _, _ = grid
    p.entry.options = {"diagnostic_level": 1}
    first = dict(event="target_soc", target_soc=40, phase="DYNAMIC")
    assert event_record(p.entry, "control", first)
    assert event_record(p.entry, "control", first) is None
    assert event_record(p.entry, "control", {**first, "target_soc": 41})
