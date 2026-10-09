"""Consumed-state diagnostics, timing, unavailable inputs and control equivalence."""

import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_common_runtime import configure
from test_pv_diagnostics import records
from test_pv_optimum_hold import configure as hold_configure
from test_pv_optimum_hold import evaluate, period, phase_start, samples
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.pv_diagnostics import cycle
from custom_components.wallbox_manager.pv_input_diagnostics import input_sample


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"])
async def test_fast_actual_inputs_all_profiles(grid, caplog, profile):
    p, t, _ = grid
    configure(p, t, profile)
    p.entry.options = {"diagnostic_level": 3}
    p.monotonic = lambda: 10
    measurements(p, t, soc=83, actual=2000)
    p.hass.states.async_set("sensor.grid_import_power", 0, {"unit_of_measurement": "W"})
    p.hass.states.async_set(
        "sensor.grid_export_power", 0.5, {"unit_of_measurement": "kW"}
    )
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    (ev,) = line["regulator_evaluations"]
    fast = ev["regulator"]
    assert fast["wallbox_power_w"] == 2000
    assert fast["battery_discharge_power_w"] == 1000
    assert fast["battery_max_discharge_power_w"] == 3500
    assert fast["grid_import_power_w"] == 0
    assert fast["grid_export_power_w"] == 500
    assert fast["previous_request_w"] is None
    assert fast["request_w"] == ev["request_w"] == 3450
    assert fast["grid_deadband_w"] == 100 and fast["import_grace_s"] == 10
    assert ev["grid_net_power_w"] == -500
    assert ev["battery_soc_pct"] == 83
    assert ev["battery_power_signed_w"] is None
    assert ev["battery_charge_power_w"] is None
    assert ev["battery_dynamic_discharge_limit_w"] is None
    assert (
        ev["lower_stop_threshold"],
        ev["pv_start_threshold"],
        ev["fast_start_threshold"],
    ) == (79, 81, 82)
    assert line["external"]["grid_export_power"]["value"] == 0.5
    assert line["external"]["grid_export_power"]["normalized_value"] == 500


@pytest.mark.parametrize("missing", ["unavailable", None, "0"])
async def test_missing_and_zero_grid_values(grid, caplog, missing):
    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    measurements(p, t, soc=83)
    p.entry.options = {"diagnostic_level": 3}
    if missing is None:
        p.hass.states.async_remove("sensor.grid_export_power")
    else:
        p.hass.states.async_set(
            "sensor.grid_export_power", missing, {"unit_of_measurement": "W"}
        )
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    ev = line["regulator_evaluations"][0]
    assert ev["grid_net_power_w"] == (0 if missing == "0" else None)
    sample = line["external"]["grid_export_power"]
    assert sample["normalized_value"] == (0 if missing == "0" else None)
    assert sample["reading_accepted"] == (missing == "0")
    if missing != "0":
        assert ev["regulator"] is None
        assert ev["reason"] == "measurements_unavailable"


async def test_surplus_without_optional_mappings(grid, caplog):
    p, t, _ = grid
    measurements(p, t, pv=3000, load=500, actual=0)
    p.setting(t)["profile"] = "PV_SURPLUS"
    p.entry.options = {"diagnostic_level": 3}
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    ev = line["regulator_evaluations"][0]
    assert ev["request_w"] == 2500
    assert ev["battery_soc_pct"] is None and ev["grid_net_power_w"] is None
    assert line["external"]["grid_import_power"]["freshness"] == "not_configured"


@pytest.mark.parametrize("age,expected", [(2, "fresh"), (91, "stale"), (-1, "future")])
@pytest.mark.parametrize(
    "source", [None, "2026-10-08T10:00:00+00:00", "invalid", "2026-10-08T10:00:00"]
)
def test_timestamps_keep_source_and_ha_clocks_distinct(age, expected, source):
    now = datetime(2026, 10, 8, 10, 1, tzinfo=UTC)
    state = SimpleNamespace(
        state="0",
        last_updated=now - timedelta(seconds=120),
        last_reported=now - timedelta(seconds=age),
        attributes={"unit_of_measurement": "W", "observed_at": source},
    )
    sample = input_sample(state, "sensor.grid", now, 90, Fraction(0), age == 2)
    assert sample["freshness"] == expected
    assert sample["age_s"] == age
    assert sample["timestamp"] == state.last_reported.isoformat()
    assert sample["last_updated"] == state.last_updated.isoformat()
    assert (
        sample["effective_valid_until"]
        == min(
            state.last_reported + timedelta(seconds=90),
            datetime.fromisoformat(source) + timedelta(seconds=90)
            if source and "+00:00" in source
            else state.last_reported + timedelta(seconds=90),
        ).isoformat()
    )
    assert sample["source_observed_at"] == (
        source if source and "+00:00" in source else None
    )
    assert sample["normalized_value"] == (0 if age == 2 else None)


def test_effective_deadline_and_source_received_time():
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state="1",
        last_updated=now,
        attributes={
            "unit_of_measurement": "W",
            "valid_until": (now - timedelta(seconds=1)).isoformat(),
            "received_at": now.isoformat(),
        },
    )
    sample = input_sample(state, "sensor.ev", now, 90, None, False)
    assert sample["freshness"] == "expired"
    assert sample["effective_valid_until"] == state.attributes["valid_until"]
    assert sample["last_reported"] is None
    assert sample["source_received_at"] == now.isoformat()
    assert sample["source_observed_at"] is None


async def test_repeated_fence_evaluations_do_not_mix_snapshots(grid, caplog):
    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    p.entry.options = {"diagnostic_level": 3}
    p.monotonic = lambda: 10
    measurements(p, t, soc=83, actual=2000)
    caplog.set_level(logging.INFO)
    with cycle(p, t, "regulation"):
        first = p.pv_request(t)
        p.hass.states.async_set(
            "sensor.storage_discharge_power", 2000, {"unit_of_measurement": "W"}
        )
        second = p.pv_request(t)
        p.hass.states.async_set(
            "sensor.storage_discharge_power", 9999, {"unit_of_measurement": "W"}
        )
    (line,) = records(caplog)
    a, b = line["regulator_evaluations"]
    assert a["battery_discharge_power_w"] == 1000
    assert b["battery_discharge_power_w"] == 2000
    assert a["request_w"] == first[0] and b["request_w"] == second[0]
    assert b["regulator"]["previous_request_w"] == first[0]
    assert line["external"]["storage_discharge_power"]["value"] == 2000
    assert a["evaluation_id"] != b["evaluation_id"]
    assert line["evaluation_id"] == b["evaluation_id"]


async def test_failed_followup_does_not_reuse_fast_state_or_power(grid, caplog):
    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    p.entry.options = {"diagnostic_level": 3}
    measurements(p, t, soc=83)
    caplog.set_level(logging.INFO)
    with cycle(p, t, "regulation"):
        p.pv_request(t)
        p.hass.states.async_set("sensor.soc", "unavailable")
        p.pv_request(t)
    (line,) = records(caplog)
    first, failed = line["regulator_evaluations"]
    assert first["regulator"]["algorithm"] == "FAST_DISCHARGE"
    assert failed["regulator"] is None
    assert all(value is None for value in failed["calculations"].values())
    assert failed["lower_stop_threshold"] is None
    assert failed["mode"] is None
    assert failed["target_soc"] is None
    assert "optimum_mode" not in line


async def test_balance_exposes_uncorrected_smoothed_inputs(grid, caplog):
    from custom_components.wallbox_manager.power_history import PowerHistory

    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    p.pv_ongoing[t] = True
    p.optimum_modes[t] = "PV_BALANCE"
    p.entry.options = {"diagnostic_level": 3}
    p.monotonic = lambda: 5
    measurements(p, t, pv=4000, load=4000, actual=3500, soc=80)
    for key, value in (("leistung_pv", 4000), ("leistung_verbraucher", 2500)):
        p.power_history[key] = PowerHistory(5)
        p.power_history[key].add(0, Fraction(value))
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    ev = line["regulator_evaluations"][0]
    calc = ev["calculations"]
    assert calc["raw_consumption_power_w"] == 4000
    assert calc["smoothed_consumption_power_w"] == 2500
    assert calc["site_load_w"] == -1000  # Deliberately NOT fixed in beta.8.
    assert ev["request_w"] == calc["surplus_w"] == 5000
    assert ev["regulator"]["algorithm"] == "PV_BALANCE"
    assert ev["grid_net_power_w"] == 0
    assert line["external"]["grid_import_power"]["freshness"] == "fresh"


async def test_no_additional_ha_reads_or_regulator_updates(grid, caplog, monkeypatch):
    p, t, _ = grid
    configure(p, t, "PV_MAXIMUM")
    measurements(p, t, soc=83, actual=2000)
    p.monotonic = lambda: 10
    original = type(p.hass.states).get
    counts = Counter()

    def counted(machine, entity_id):
        counts[entity_id] += 1
        return original(machine, entity_id)

    monkeypatch.setattr(type(p.hass.states), "get", counted)
    caplog.set_level(logging.INFO)
    results = []
    for level in (0, 3):
        p.entry.options = {"diagnostic_level": level}
        p.optimum_regulators.clear()
        counts.clear()
        with cycle(p, t, "regulation"):
            result = p.pv_request(t)
        results.append((result, dict(counts), vars(p.optimum_regulators[t]).copy()))
    assert results[0] == results[1]


@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_identical_minimum_hold_pause_commands_and_soc(grid, caplog, level):
    p, t, (c, _, peer, *_), clock = hold_configure(grid)
    p.entry.options = {"diagnostic_level": level}
    caplog.set_level(logging.INFO)
    await p.permission(t, True)
    assert c.confirmed_point(t).current_a == 7
    clock[0] = 1
    samples(p, t, soc=80, pv=0, load=0)
    await evaluate(p, t)
    assert p.optimum_modes[t] == "PV_BALANCE"
    assert c.confirmed_point(t).current_a == 6
    clock[0] = 11
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert [period(r)["limit"] for r in peer.requests] == [7, 6, 0]
    lines = records(caplog)
    assert any("regulator_evaluations" in line for line in lines) == (level == 3)
    if level < 3:
        assert all("input_reads" not in line for line in lines)


@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_identical_phase_lockout_commands(grid, caplog, level):
    p, t, (c, _, peer, *_), clock, _ = await phase_start(grid)
    p.entry.options = {"diagnostic_level": level}
    caplog.set_level(logging.INFO)
    clock[0] = 1
    samples(p, t, actual=4140, discharge=3200, imported=1800)
    before = len(peer.requests)
    await evaluate(p, t)
    assert len(peer.requests) == before + 2
    assert c.phase_restricted(t)
    assert (c.confirmed_point(t).mode.count, c.confirmed_point(t).current_a) == (3, 6)


async def test_tap_failure_cannot_change_decision(grid, monkeypatch):
    import custom_components.wallbox_manager.pv_input_diagnostics as diagnostics

    p, t, _ = grid
    measurements(p, t)
    baseline = p.pv_plan(t)
    p.entry.options = {"diagnostic_level": 3}

    def broken(*args):
        raise RuntimeError("diagnostic only")

    monkeypatch.setattr(diagnostics, "input_sample", broken)
    assert p.pv_plan(t) == baseline


async def test_malformed_source_timestamp_does_not_leak_attributes(grid, caplog):
    p, t, _ = grid
    measurements(p, t)
    p.entry.options = {"diagnostic_level": 3}
    p.hass.states.async_set(
        "sensor.pv",
        8000,
        {"unit_of_measurement": "W", "observed_at": "bad", "token": "SECRET"},
    )
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    assert line["external"]["leistung_pv"]["source_timestamp_status"] == "invalid"
    assert "SECRET" not in json.dumps(line)
