"""Retention is a preference only after directional and electrical constraints."""

from fractions import Fraction

import pytest

from custom_components.wallbox_manager.control.requests import Direction, PowerRequest
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.solver.power import solve


@pytest.mark.parametrize(
    "target,direction,tolerance,expected_count",
    [
        (4000, Direction.NEAREST, 5, 3),
        (4000, Direction.NEAREST, 3, 1),
        (4000, Direction.NEAREST, 0, 1),
        (4000, Direction.NEAREST, Fraction(7, 2), 3),
        (4000, Direction.DOWN, 5, 1),
        (4300, Direction.UP, 5, 1),
        (6000, Direction.DOWN, 15, 3),
        (4300, Direction.UP, 15, 3),
    ],
)
def test_retention(
    capabilities, voltage, now, one, three, target, direction, tolerance, expected_count
):
    result = solve(
        PowerRequest(target, direction, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=True,
        phase_switch_deviation_pct=tolerance,
    )
    assert result.point.mode.count == expected_count
    if direction == Direction.DOWN:
        assert result.point.offered_power_w <= target
    if direction == Direction.UP:
        assert result.point.offered_power_w >= target


def test_retention_cannot_override_limit(capabilities, voltage, now, one, three):
    result = solve(
        PowerRequest(4000, Direction.NEAREST, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=True,
        phase_switch_deviation_pct=25,
        limits=(CurrentLimit(three, 0, 0, "inhibit"),),
    )
    assert result.point.mode == one


@pytest.mark.parametrize("tolerance", [0, 5, 25])
def test_zero(capabilities, voltage, now, one, three, tolerance):
    result = solve(
        PowerRequest(0, Direction.UP, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=True,
        phase_switch_deviation_pct=tolerance,
    )
    assert not result.point.charging


def test_zero_without_stop_preserves_solver_semantics(
    capabilities, voltage, now, one, three
):
    from dataclasses import replace

    from custom_components.wallbox_manager.core.capabilities import EvidenceState

    capabilities = replace(
        capabilities, stop=replace(capabilities.stop, state=EvidenceState.UNKNOWN)
    )
    result = solve(
        PowerRequest(0, Direction.UP, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=True,
        phase_switch_deviation_pct=25,
    )
    assert result.point.offered_power_w == 1380


@pytest.mark.parametrize("active,expected", [(True, 3), (False, 1)])
def test_retention_requires_positive_active_state(
    capabilities, voltage, now, one, three, active, expected
):
    result = solve(
        PowerRequest(4000, Direction.NEAREST, True),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=active,
        phase_switch_deviation_pct=5,
    )
    assert result.point.mode.count == expected


def test_disabled_ignores_physical_position(capabilities, voltage, now, one, three):
    result = solve(
        PowerRequest(4000, Direction.NEAREST, False),
        capabilities,
        voltage,
        now=now,
        eligible_modes=(one, three),
        current_mode=three,
        actively_charging=True,
        phase_switch_deviation_pct=25,
    )
    assert not result.point.charging
