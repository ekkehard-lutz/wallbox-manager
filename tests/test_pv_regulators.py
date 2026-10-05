"""Shared power algorithms are independent of profile SoC policy and hardware."""

from fractions import Fraction

import pytest

from custom_components.wallbox_manager.pv_regulators import fast_discharge, pv_balance


@pytest.mark.parametrize(
    "pv,load,actual,expected",
    [(8000, 5000, 3000, 6000), (0, 500, 0, -500), (500, 500, 0, 0)],
)
def test_balance_preserves_existing_signed_budget(pv, load, actual, expected):
    assert pv_balance(pv, load, actual) == expected


def request(**changes):
    inputs = dict(
        wallbox_power=3000,
        battery_discharge=1000,
        max_discharge=3500,
        grid_import=0,
        grid_export=0,
        deadband=100,
        increase_fraction=Fraction(1, 4),
    )
    return fast_discharge(**(inputs | changes))


def test_current_discharge_consumes_user_limit_headroom():
    assert request() == 3625
    assert request(battery_discharge=3500) == 3000
    assert request(max_discharge=4800) == 3950
    assert request(max_discharge=3500, increase_fraction=1) == 5500


def test_export_adds_to_controlled_increase():
    assert request(grid_export=1000) == 3875


def test_import_reduces_immediately_without_spending_headroom():
    assert request(grid_import=1000) == 2000
    assert request(grid_import=1000, grid_export=200) == 2200
    assert request(grid_import=4000) == 0


def test_reduced_user_limit_corrects_existing_excess_discharge():
    assert request(battery_discharge=4800) == 1700
    assert request(battery_discharge=4800, grid_import=500) == 1200


@pytest.mark.parametrize("watts", [0, 1, 50, 100])
def test_zero_grid_deadband(watts):
    assert request(grid_import=watts) == request(grid_export=watts) == request()
    assert request(grid_import=watts, battery_discharge=3500) == 3000


@pytest.mark.parametrize(
    "changes",
    [
        {"battery_discharge": None},
        {"grid_import": "unavailable"},
        {"grid_export": float("nan")},
        {"wallbox_power": float("inf")},
        {"max_discharge": -1},
        {"battery_discharge": -1},
        {"deadband": -1},
        {"increase_fraction": -1},
        {"increase_fraction": 2},
    ],
)
def test_missing_invalid_input_never_becomes_zero(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_smoothed_request_reaches_usable_power_even_while_wallbox_is_paused():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    first = regulator.request(0, 0, 3500, 0, 0, now=0, interval=5)
    assert first == 875
    assert regulator.request(0, 0, 3500, 0, 0, now=0, interval=5) == first
    assert regulator.request(0, 0, 3500, 0, 0, now=5, interval=5) > 1380
    assert regulator.request(0, 0, 3500, 1000, 0, now=5, interval=5) == 0


def transient_request(
    regulator, seconds, *, discharge=2000, imported=2500, wallbox=5000, exported=0
):
    return regulator.request(
        wallbox, discharge, 4800, imported, exported, now=seconds, interval=5
    )


def test_battery_takes_transient_load_during_grace_without_reduction():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    assert transient_request(regulator, 0) == 5000
    assert regulator.import_since == 0
    assert transient_request(regulator, 1) == 5000
    assert transient_request(regulator, 2, discharge=4500, imported=0) == 5000
    assert regulator.import_since is None


def test_persistent_import_reduces_at_three_seconds_not_an_extending_deadline():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    for seconds in (0, 1, 2, 2.999):
        assert transient_request(regulator, seconds) == 5000
        assert regulator.import_since == 0
    assert transient_request(regulator, 3) == 2500
    assert transient_request(regulator, 4) == 2500
    assert regulator.import_since == 0


@pytest.mark.parametrize("discharge", [4700, 4800])
def test_exhausted_or_noise_sized_headroom_never_delays_deficit(discharge):
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    assert transient_request(FastDischargeRegulator(), 0, discharge=discharge) == 2500


def test_partial_headroom_only_defers_coverable_part():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    assert transient_request(regulator, 0, discharge=4000) == 3300
    # The EV reduction removes 1700 W import; only the remaining 800 W gets grace.
    assert (
        transient_request(regulator, 1, discharge=4000, imported=800, wallbox=3300)
        == 3300
    )
    assert (
        transient_request(regulator, 3, discharge=4000, imported=800, wallbox=3300)
        == 2500
    )


def test_excess_discharge_bypasses_grace_and_noise_does_not_start_it():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    assert transient_request(regulator, 0, discharge=5000) == 2300
    assert transient_request(regulator, 1, discharge=4800, imported=100) == 5000
    assert regulator.import_since is None
    assert transient_request(regulator, 2) == 5000
    assert regulator.import_since == 2


def test_grace_loses_headroom_and_invalid_input_breaks_continuity():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    regulator = FastDischargeRegulator()
    assert transient_request(regulator, 0) == 5000
    assert transient_request(regulator, 1, discharge=4800) == 2500
    with pytest.raises(ValueError):
        transient_request(regulator, 2, discharge=None)
    assert regulator.import_since is None


def test_one_second_observation_does_not_speed_up_upward_smoothing():
    from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator

    fast, slow = FastDischargeRegulator(), FastDischargeRegulator()
    for regulator in (fast, slow):
        transient_request(regulator, 0, imported=0, exported=1000)
    for seconds in range(1, 6):
        frequent = transient_request(fast, seconds, imported=0, exported=1000)
    sparse = transient_request(slow, 5, imported=0, exported=1000)
    assert abs(frequent - sparse) < Fraction(1, 100)
