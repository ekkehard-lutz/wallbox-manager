"""Time integration is independent of event count and excludes unknown time."""

from fractions import Fraction

import pytest

from custom_components.wallbox_manager.power_history import PowerHistory


@pytest.mark.parametrize("window", [0, 5, 15, 300])
def test_constant(window):
    history = PowerHistory(window)
    history.add(0, Fraction(2000))
    history.add(3, Fraction(2000))
    assert history.average(5, Fraction(2000))[0] == 2000


def test_duration_weighted_not_sample_count():
    history = PowerHistory(5)
    for time, power in [(0, 2000), (1, 2000), (2, 2000), (4, 1000)]:
        history.add(time, Fraction(power))
    assert history.average(5, 1000) == (1800, 5)
    assert history.average(6, 1000) == (1600, 5)


def test_partial_window_and_instant_start():
    history = PowerHistory(5)
    history.add(10, Fraction(2000))
    assert history.average(10, 2000) == (2000, 0)
    assert history.average(12, 2000) == (2000, 2)


def test_history_pruned_retaining_left_boundary():
    history = PowerHistory(5)
    for timestamp in range(100):
        history.add(timestamp, Fraction(timestamp))
    assert len(history.samples) == 6
    assert history.average(100, 99)[0] == 97
    assert len(history.samples) == 5


def test_disabled_returns_raw():
    history = PowerHistory(0)
    history.add(0, Fraction(2000))
    assert history.average(4, 1000) == (1000, 0)


def test_unknown_intervals_are_not_zero():
    history = PowerHistory(5)
    history.add(0, Fraction(2000))
    history.add(2, None)
    history.add(4, Fraction(1000))
    assert history.average(5, 1000) == (Fraction(5000, 3), 3)


def test_expired_measurement_does_not_fill_unknown_gap():
    history = PowerHistory(300)
    history.add(0, Fraction(2000), valid_for=90)
    history.add(200, Fraction(1000), valid_for=90)
    assert history.average(210, 1000) == (1900, 100)
