"""Normative common SoC boundaries, independent of profile and hardware."""

from fractions import Fraction

import pytest

from custom_components.wallbox_manager.pv_soc import (
    clamp_settings,
    clamp_target,
    soc_policy,
    target_bounds,
)


@pytest.mark.parametrize("previous", ["STOP", "PV_BALANCE", "FAST_DISCHARGE"])
@pytest.mark.parametrize("surplus", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
@pytest.mark.parametrize(
    "soc",
    ["78.999", "79", "79.001", "80.999", "81", "81.001", "81.999", "82", "82.001"],
)
def test_all_boundary_neighbors(previous, surplus, stopped, soc):
    result = soc_policy(80, 2, soc, previous, stopped=stopped, surplus=surplus)
    prior = "STOP" if stopped else previous
    value = Fraction(soc)
    if value <= 79:
        expected = "STOP"
    elif value >= 82:
        expected = "FAST_DISCHARGE"
    elif value < 81:
        expected = "STOP" if prior == "STOP" else "PV_BALANCE"
    elif prior != "STOP":
        expected = prior
    else:
        expected = "PV_BALANCE" if surplus else "STOP"
    assert result.mode == expected
    assert (
        result.lower_stop_threshold,
        result.pv_start_threshold,
        result.fast_start_threshold,
    ) == (79, 81, 82)


def test_start_evidence_is_not_continuation_requirement():
    mode = soc_policy(80, 2, 81, surplus=True).mode
    for soc in (81, 80, 79.001):
        mode = soc_policy(80, 2, soc, mode, surplus=False).mode
        assert mode == "PV_BALANCE"
    assert soc_policy(80, 2, 79, mode).mode == "STOP"


def test_global_bounds_and_migration():
    assert target_bounds(5, 2) == (7, 98)
    assert clamp_target(100, 5, 2) == 98
    assert clamp_target(5, 10, 2) == 12
    settings = dict(
        soll_soc_speicher=100,
        optimum_lower_soc=30,
        optimum_upper_soc=80,
        soc_hysterese=2,
    )
    migrated = clamp_settings(settings, 5)
    assert migrated == {**settings, "soll_soc_speicher": 98}
    assert settings["soll_soc_speicher"] == 100
    assert clamp_settings(migrated, 40)["optimum_lower_soc"] == 42
    assert (
        clamp_settings({**settings, "optimum_lower_soc": 90}, 5)["optimum_upper_soc"]
        == 90
    )
    with pytest.raises(ValueError, match="no safe"):
        target_bounds(99, 2)
