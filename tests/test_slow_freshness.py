"""Slow planning freshness does not relax live telemetry or other validation."""

import logging
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_diagnostics import records
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.freshness import (
    LIVE_FRESHNESS,
    SLOW_FRESHNESS,
    freshness_for,
)
from custom_components.wallbox_manager.pv_diagnostics import entity_sample
from custom_components.wallbox_manager.pv_optimum import OPTIMUM_REFERENCES
from custom_components.wallbox_manager.pv_surplus import power_valid_for, reading


def sample(now, age, value="20.3832", unit="kWh"):
    return SimpleNamespace(
        state=value,
        attributes={"unit_of_measurement": unit},
        last_updated=now - timedelta(hours=2),
        last_reported=now - timedelta(seconds=age),
    )


@pytest.mark.parametrize("age", [300, 600, 900, 900.001, 901])
def test_slow_freshness_exact_inclusive_boundary_and_expiry(age):
    now = datetime.now(UTC)
    state = sample(now, age)
    maximum = freshness_for("remaining_pv_energy")
    assert maximum == SLOW_FRESHNESS == 900
    if age <= 900:
        assert reading(state, now, energy=True, max_age=maximum) == Fraction("20383.2")
    else:
        with pytest.raises(ValueError, match="stale measurement"):
            reading(state, now, energy=True, max_age=maximum)
    assert power_valid_for(state, now, max_age=maximum) == max(0, 900 - age)
    assert entity_sample(state, "sensor.forecast", now, max_age=maximum)[
        "freshness"
    ] == ("fresh" if age <= 900 else "stale")


@pytest.mark.parametrize("age", [90, 90.001, 300])
def test_unclassified_live_reading_keeps_original_boundary(age):
    now = datetime.now(UTC)
    state = sample(now, age, value="1000", unit="W")
    assert freshness_for("another_live_reference") == LIVE_FRESHNESS == 90
    if age <= 90:
        assert reading(state, now) == 1000
    else:
        with pytest.raises(ValueError, match="stale measurement"):
            reading(state, now)
    assert power_valid_for(state, now) == max(0, 90 - age)


@pytest.mark.parametrize("age", [300, 600, 900, 900.001, 901])
async def test_forecast_policy_and_diagnostics_agree_without_sensor_refresh(
    grid, caplog, age
):
    p, t, _ = grid
    setup_optimum(p, t)
    now = datetime.now(UTC)
    state = sample(now, age)
    original_get = p.hass.states.get
    entity = p.references["remaining_pv_energy"]
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    with ExitStack() as stack:
        for module in ("pv_surplus", "pv_optimum", "pv_diagnostics"):
            clock = stack.enter_context(
                patch(
                    f"custom_components.wallbox_manager.{module}.datetime",
                    wraps=datetime,
                )
            )
            clock.now.return_value = now
        stack.enter_context(
            patch.object(
                type(p.hass.states),
                "get",
                side_effect=lambda key: state if key == entity else original_get(key),
            )
        )
        services = stack.enter_context(
            patch(
                "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
            )
        )
        power, _, status, plan = p.pv_plan(t)
        services.assert_not_called()
    line = records(caplog)[-1]
    if age <= 900:
        assert power > 0 and plan is not None
        assert status == "actively_charging"
        if age < 900:
            assert p.pv_expiry[t] > now
        else:
            assert p.pv_expiry[t] == now
        assert line["external"]["remaining_pv_energy"]["freshness"] == "fresh"
    else:
        assert plan is None and status == "measurements_unavailable"
        assert line["decision"] == "INPUT_UNAVAILABLE"
        assert line["external"]["remaining_pv_energy"]["freshness"] == "stale"
    assert line["external"]["remaining_pv_energy"]["unit"] == "kWh"
    assert line["external"]["remaining_pv_energy"]["value"] == 20.3832


@pytest.mark.parametrize(
    "key", [key for key in OPTIMUM_REFERENCES if key != "remaining_pv_energy"]
)
async def test_all_other_optimum_inputs_still_reject_91_seconds(grid, key):
    p, t, _ = grid
    setup_optimum(p, t)
    now = datetime.now(UTC)
    entity = p.references[key]
    original_get = p.hass.states.get
    original = original_get(entity)
    state = sample(now, 91, original.state, original.attributes["unit_of_measurement"])
    assert freshness_for(key) == 90
    with patch.object(
        type(p.hass.states),
        "get",
        side_effect=lambda key: state if key == entity else original_get(key),
    ):
        assert p.pv_request(t)[2] == "measurements_unavailable"
        assert p.pv_plan(t)[3] is None


@pytest.mark.parametrize(
    "value,unit,age",
    [
        ("unavailable", "kWh", 300),
        ("unknown", "kWh", 300),
        ("invalid", "kWh", 300),
        ("nan", "kWh", 300),
        ("inf", "kWh", 300),
        ("20", "W", 300),
        ("20", "kWh", -1),
    ],
)
def test_slow_class_does_not_relax_value_unit_or_future_validation(value, unit, age):
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        reading(sample(now, age, value, unit), now, energy=True, max_age=SLOW_FRESHNESS)


def test_explicit_expiry_still_overrides_slow_report_age():
    now = datetime.now(UTC)
    state = sample(now, 300)
    state.attributes["valid_until"] = now.isoformat()
    with pytest.raises(ValueError, match="expired measurement"):
        reading(state, now, energy=True, max_age=SLOW_FRESHNESS)
    assert power_valid_for(state, now, max_age=SLOW_FRESHNESS) == 0
    assert (
        entity_sample(state, "sensor.forecast", now, max_age=SLOW_FRESHNESS)[
            "freshness"
        ]
        == "expired"
    )
