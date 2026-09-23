"""Physical rounding, scope safety, electrical limits and deterministic selection."""

from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from itertools import permutations

import pytest

from custom_components.wallbox_manager.control.requests import Direction, PowerRequest
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityOverride,
    ChargingEnvelope,
    CurrentLimit,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import Phase, PhaseMode, StationId
from custom_components.wallbox_manager.solver.operating_point import (
    Reason,
    ResultStatus,
)
from custom_components.wallbox_manager.solver.power import solve


@pytest.fixture
def run(capabilities, voltage, now, one, three):
    def invoke(target, direction=Direction.NEAREST, allowed=True, **kwargs):
        return solve(
            PowerRequest(target, direction, allowed),
            kwargs.pop("capabilities", capabilities),
            kwargs.pop("voltage", voltage),
            now=kwargs.pop("now", now),
            eligible_modes=kwargs.pop("eligible_modes", (one, three)),
            actively_charging=kwargs.pop("actively_charging", True),
            **kwargs,
        )

    return invoke


@pytest.mark.parametrize(
    ("target", "direction", "count", "current", "power"),
    [
        (4000, Direction.DOWN, 1, 17, 3910),
        (4000, Direction.UP, 1, 18, 4140),
        (4000, Direction.NEAREST, 1, 17, 3910),
        (500, Direction.DOWN, 0, None, 0),
        (500, Direction.NEAREST, 0, None, 0),
        (500, Direction.UP, 1, 6, 1380),
        (3910, Direction.DOWN, 1, 17, 3910),
        (3910, Direction.UP, 1, 17, 3910),
        (3910, Direction.NEAREST, 1, 17, 3910),
        (12000, Direction.DOWN, 3, 16, 11040),
        (12000, Direction.NEAREST, 3, 16, 11040),
    ],
)
def test_architecture_examples(run, target, direction, count, current, power):
    result = run(target, direction)
    assert result.status == (ResultStatus.FEASIBLE if count else ResultStatus.OFF)
    assert (result.point.mode.count if count else 0) == count
    assert result.point.current_a == current
    assert result.point.offered_power_w == power


@pytest.mark.parametrize("direction", list(Direction))
def test_zero(run, direction):
    assert run(0, direction).status == ResultStatus.OFF


@pytest.mark.parametrize("direction", list(Direction))
def test_prohibited_never_charges(run, direction):
    result = run(10000, direction, allowed=False)
    assert result.status == ResultStatus.OFF
    assert result.reason == Reason.CHARGING_PROHIBITED


def test_up_above_maximum_is_unreachable(run):
    result = run(12000, Direction.UP)
    assert result.status == ResultStatus.UNREACHABLE
    assert result.reason == Reason.DIRECTION_UNREACHABLE
    assert result.point is None
    assert (result.min_power_w, result.max_power_w) == (0, 11040)
    assert result.suggested.offered_power_w == 11040


@pytest.mark.parametrize(
    "state", [s for s in EvidenceState if s != EvidenceState.VERIFIED]
)
def test_off_requires_verified_stop(run, capabilities, state):
    cap = replace(capabilities, stop=replace(capabilities.stop, state=state))
    assert run(500, Direction.DOWN, capabilities=cap).status == ResultStatus.UNREACHABLE
    assert run(500, capabilities=cap).point.current_a == 6
    for direction in Direction:
        result = run(9000, direction, allowed=False, capabilities=cap)
        assert result.point is None
        assert result.reason == Reason.STOP_UNVERIFIED
    assert run(0, Direction.UP, capabilities=cap).point.offered_power_w == 1380


@pytest.mark.parametrize("mode_index", [0, 1])
def test_single_mode_charger(run, capabilities, mode_index):
    envelope = capabilities.envelopes[mode_index]
    cap = replace(capabilities, envelopes=(envelope,))
    result = run(4000, Direction.UP, capabilities=cap)
    assert result.point.mode == envelope.mode
    assert result.point.current_a == (18 if mode_index == 0 else 6)
    assert result.point.offered_power_w == 4140


def test_current_mode_ties(run, one, three):
    for direction in Direction:
        assert run(4140, direction, current_mode=three).point.mode == three
        assert run(4140, direction, current_mode=one).point.mode == one
    # Equidistant across modes: current mode wins before the lower power.
    assert run(4025, current_mode=three).point.offered_power_w == 4140
    assert run(4025, current_mode=one).point.offered_power_w == 3910
    assert run(4025).point.offered_power_w == 3910
    # Near ties are not ties: a closer point wins even in the other mode.
    assert run(4024, current_mode=three).point.offered_power_w == 3910


def test_off_competes_with_minimum(run, one):
    assert run(690).status == ResultStatus.OFF
    assert run(690, current_mode=one).point.offered_power_w == 1380
    assert run(689, current_mode=one).status == ResultStatus.OFF


def test_secondary_lower_power_tie(run, one):
    assert run(4025, current_mode=one).point.current_a == 17


def test_final_ties_ignore_container_order(run, capabilities, one, three):
    for envelopes in permutations(capabilities.envelopes):
        for eligible in permutations((one, three)):
            result = run(
                4140,
                capabilities=replace(capabilities, envelopes=envelopes),
                eligible_modes=eligible,
            )
            assert result.point.mode == one
    # Equal count and power use canonical physical phase mapping.
    l2 = PhaseMode((Phase.L2,))
    second = replace(capabilities.envelopes[0], mode=l2)
    for envelopes in permutations((second, capabilities.envelopes[0])):
        result = run(
            1380,
            capabilities=replace(capabilities, envelopes=envelopes),
            eligible_modes=(l2, one),
        )
        assert result.point.mode == one


def test_ineligible_mode_cannot_win_tie(run, one, three):
    result = run(4140, current_mode=three, eligible_modes=(one,))
    assert result.point.mode == one
    assert run(4000, Direction.UP, eligible_modes=()).status == ResultStatus.UNREACHABLE


@pytest.mark.parametrize(
    "state", [s for s in EvidenceState if s != EvidenceState.VERIFIED]
)
def test_unverified_modes_and_overrides_do_not_grant_support(
    run, capabilities, state, three
):
    envelope = capabilities.envelopes[0]
    cap = replace(
        capabilities,
        envelopes=(
            replace(envelope, evidence=replace(envelope.evidence, state=state)),
            capabilities.envelopes[1],
        ),
        overrides=(CapabilityOverride("1p", True, "config", "operator_setting"),),
    )
    assert run(4000, Direction.UP, capabilities=cap).point.mode == three


def test_different_minimums_and_fractional_steps(run, capabilities, one, three):
    e1, e3 = capabilities.envelopes
    cap = replace(
        capabilities,
        envelopes=(
            replace(e1, min_current_a=6.1, current_step_a=0.2, max_current_a=6.65),
            replace(e3, min_current_a=8, current_step_a=2.5),
        ),
    )
    result = run(1495, Direction.UP, capabilities=cap)
    assert result.point.mode == one
    assert result.point.current_a == Fraction(13, 2)
    assert result.point.offered_power_w == 1495
    # Non-grid ceiling 6.65 A must not become a reachable step.
    assert run(1500, Direction.DOWN, capabilities=cap).point.current_a == Fraction(
        13, 2
    )
    result = run(7000, Direction.UP, capabilities=cap, eligible_modes=(three,))
    assert result.point.current_a == Fraction(21, 2)
    assert result.point.offered_power_w == 7245


def test_actual_unequal_non230_voltages(run, voltage, three):
    observation = replace(
        voltage,
        phases=tuple(
            replace(p, voltage_v=v)
            for p, v in zip(voltage.phases, (220, 240, 250), strict=True)
        ),
    )
    result = run(4000, Direction.UP, voltage=observation, eligible_modes=(three,))
    assert result.point.current_a == 6
    assert result.point.offered_power_w == 4260
    assert result.point.phase_voltages_v == (220, 240, 250)


def test_single_phase_mapping_uses_actual_phase(run, capabilities, voltage):
    l2 = PhaseMode((Phase.L2,))
    cap = replace(
        capabilities, envelopes=(replace(capabilities.envelopes[0], mode=l2),)
    )
    observation = replace(voltage, phases=(replace(voltage.phases[1], voltage_v=240),))
    assert (
        run(
            1400,
            Direction.UP,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(l2,),
        ).point.offered_power_w
        == 1440
    )


def test_missing_voltage_inhibits_without_inventing_phases(run, voltage, one):
    partial = replace(voltage, phases=voltage.phases[:1])
    assert run(4000, voltage=partial).point.mode == one
    assert (
        run(4000, voltage=partial, eligible_modes=(one,)).point.offered_power_w == 3910
    )
    assert (
        run(4000, voltage=replace(voltage, phases=())).reason
        == Reason.VOLTAGE_UNAVAILABLE
    )
    assert run(0, voltage=partial).status == ResultStatus.OFF
    assert run(4000, allowed=False, voltage=partial).status == ResultStatus.OFF


@pytest.mark.parametrize("offset", [-1, 30, 60])
def test_stale_or_future_voltage(run, now, offset):
    assert (
        run(4000, now=now + timedelta(seconds=offset)).reason
        == Reason.VOLTAGE_UNAVAILABLE
    )


def test_observation_scope_and_generation(run, voltage):
    for observation in (
        replace(voltage, scope=StationId("other")),
        replace(voltage, connection_generation=3),
    ):
        assert run(4000, voltage=observation).reason == Reason.OBSERVATION_MISMATCH


def test_installation_and_station_limits_intersect_on_device_grid(run, one, three):
    limits = (
        CurrentLimit(one, 6.5, 24, "installation"),
        CurrentLimit(one, 0, 17.5, "station_remaining"),
        CurrentLimit(three, 0, 0, "station_remaining"),
    )
    result = run(10000, Direction.DOWN, limits=limits)
    assert result.point.current_a == 17
    assert run(1400, Direction.UP, limits=limits).point.current_a == 7
    assert run(4000, Direction.UP, limits=limits).status == ResultStatus.UNREACHABLE


def test_no_modes_or_empty_limit_intersection(run, capabilities, one):
    cap = replace(
        capabilities,
        envelopes=(),
        stop=replace(capabilities.stop, state=EvidenceState.UNKNOWN),
    )
    assert run(4000, capabilities=cap).reason == Reason.NO_ELIGIBLE_MODE
    cap = replace(cap, envelopes=capabilities.envelopes[:1])
    result = run(4000, capabilities=cap, limits=(CurrentLimit(one, 0, 5, "site"),))
    assert result.reason == Reason.ELECTRICAL_LIMIT


def test_tiny_step_is_not_enumerated(run, capabilities, one):
    cap = replace(
        capabilities,
        envelopes=(
            replace(capabilities.envelopes[0], current_step_a=Fraction(1, 10**20)),
        ),
    )
    result = run(4000, Direction.DOWN, capabilities=cap, eligible_modes=(one,))
    assert 0 <= 4000 - result.point.offered_power_w < Fraction(230, 10**20)


@pytest.mark.parametrize("direction", list(Direction))
def test_derived_candidates_match_exhaustive_reference(
    run, capabilities, one, three, direction
):
    # Independent small-grid oracle checks targets around every point and midpoint.
    envelopes = (
        ChargingEnvelope(one, 6, 12, Fraction(1, 2), capabilities.stop),
        ChargingEnvelope(three, 3, 5, Fraction(1, 4), capabilities.stop),
    )
    cap = replace(capabilities, envelopes=envelopes)
    points = [(Fraction(0), None, None)]
    for e in envelopes:
        current = e.min_current_a
        while current <= e.max_current_a:
            points.append((current * e.mode.count * 230, e.mode, current))
            current += e.current_step_a
    powers = sorted({p[0] for p in points})
    targets = set(powers)
    targets.update(p + delta for p in powers for delta in (-1, 1) if p + delta >= 0)
    targets.update((a + b) / 2 for a, b in zip(powers, powers[1:], strict=False))
    for current_mode in (None, one, three):
        for target in sorted(targets):
            eligible = [
                p
                for p in points
                if direction == Direction.NEAREST
                or (direction == Direction.DOWN and p[0] <= target)
                or (direction == Direction.UP and p[0] >= target)
            ]
            result = run(target, direction, capabilities=cap, current_mode=current_mode)
            if not eligible:
                assert result.status == ResultStatus.UNREACHABLE
                continue
            expected = min(
                eligible,
                key=lambda p: (
                    abs(p[0] - target),
                    not (current_mode is not None and p[1] == current_mode),
                    p[0],
                    p[1].count if p[1] else 0,
                    p[1].phases if p[1] else (),
                    p[2] or 0,
                ),
            )
            assert (
                result.point.offered_power_w,
                result.point.mode,
                result.point.current_a,
            ) == expected


@pytest.fixture
def ac_modes(capabilities, voltage, one, three):
    """Independent grids and unequal observed voltages for 1P, 2P and 3P."""
    two = PhaseMode((Phase.L1, Phase.L2))
    cap = replace(
        capabilities,
        envelopes=(
            ChargingEnvelope(one, 6, 32, 2, capabilities.stop),
            ChargingEnvelope(two, 5, 25, Fraction(1, 2), capabilities.stop),
            ChargingEnvelope(three, 4, 16, 1, capabilities.stop),
        ),
    )
    observation = replace(
        voltage,
        phases=tuple(
            replace(p, voltage_v=v)
            for p, v in zip(voltage.phases, (220, 240, 260), strict=True)
        ),
    )
    return cap, observation, two


@pytest.mark.parametrize("only_two", [True, False])
@pytest.mark.parametrize(
    ("direction", "current"),
    [
        (Direction.DOWN, 7),
        (Direction.NEAREST, Fraction(15, 2)),
        (Direction.UP, Fraction(15, 2)),
    ],
)
def test_two_phase_rounding(run, ac_modes, only_two, direction, current):
    cap, observation, two = ac_modes
    if only_two:
        cap = replace(cap, envelopes=(cap.envelopes[1],))
        # No L3 voltage is needed by a 2P-only charger.
        observation = replace(observation, phases=observation.phases[:2])
    result = run(
        3400,
        direction,
        capabilities=cap,
        voltage=observation,
        eligible_modes=tuple(e.mode for e in cap.envelopes),
    )
    assert result.status == ResultStatus.FEASIBLE
    assert result.point.mode == two
    assert result.point.mode.count == 2
    assert result.point.current_a == current
    volts = tuple(p.voltage_v for p in observation.phases[:2])
    assert result.point.phase_voltages_v == volts
    assert result.point.offered_power_w == current * sum(volts)


@pytest.mark.parametrize("missing_phase", [Phase.L1, Phase.L2])
def test_two_phase_requires_both_voltages(run, ac_modes, missing_phase):
    cap, observation, two = ac_modes
    cap = replace(cap, envelopes=(cap.envelopes[1],))
    observation = replace(
        observation,
        phases=tuple(p for p in observation.phases if p.phase != missing_phase),
    )
    result = run(3400, capabilities=cap, voltage=observation, eligible_modes=(two,))
    assert result.status == ResultStatus.UNREACHABLE
    assert result.reason == Reason.VOLTAGE_UNAVAILABLE
    assert result.point is None


def test_two_phase_becomes_ineligible(run, ac_modes, one, three):
    cap, observation, two = ac_modes
    assert (
        run(
            3400,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(one, two, three),
        ).point.mode
        == two
    )
    for current_mode in (two, one, three):
        result = run(
            3400,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(one, three),
            current_mode=current_mode,
            actively_charging=True,
        )
        assert result.point.mode == one
        assert result.point.current_a == 16
        assert result.point.offered_power_w == 16 * observation.phases[0].voltage_v
    assert (
        run(
            10000,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(one, three),
        ).point.mode
        == three
    )


@pytest.mark.parametrize("direction", list(Direction))
def test_ties_across_one_two_three_phases(run, ac_modes, one, three, direction):
    cap, observation, two = ac_modes
    observation = replace(
        observation, phases=tuple(replace(p, voltage_v=220) for p in observation.phases)
    )
    target = 6 * sum(p.voltage_v for p in observation.phases[:2])
    # 1P/12 A, 2P/6 A and 3P/4 A all offer the same power.
    for envelopes in permutations(cap.envelopes):
        for current_mode in (None, one, two, three):
            result = run(
                target,
                direction,
                capabilities=replace(cap, envelopes=envelopes),
                voltage=observation,
                eligible_modes=tuple(e.mode for e in envelopes),
                current_mode=current_mode,
                actively_charging=True,
            )
            assert result.point.mode == (current_mode or one)
            assert result.point.offered_power_w == target
    # With 1P ineligible, deterministic final ordering chooses 2P over 3P.
    assert (
        run(
            target,
            direction,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(three, two),
        ).point.mode
        == two
    )


def test_two_phase_lower_power_tie(run, ac_modes):
    cap, observation, two = ac_modes
    cap = replace(cap, envelopes=(cap.envelopes[1],))
    voltage_sum = sum(p.voltage_v for p in observation.phases[:2])
    result = run(
        Fraction(29, 4) * voltage_sum,
        capabilities=cap,
        voltage=observation,
        eligible_modes=(two,),
        current_mode=two,
        actively_charging=True,
    )
    assert result.point.current_a == 7
    assert result.point.offered_power_w == 7 * voltage_sum


@pytest.mark.parametrize(
    "phases",
    [
        (Phase.L1,),
        (Phase.L2,),
        (Phase.L3,),
        (Phase.L1, Phase.L2),
        (Phase.L1, Phase.L3),
        (Phase.L2, Phase.L3),
        (Phase.L1, Phase.L2, Phase.L3),
    ],
)
def test_all_ac_phase_subsets_with_current_limit(run, ac_modes, phases):
    cap, observation, _ = ac_modes
    mode = PhaseMode(tuple(reversed(phases)))
    assert mode.phases == phases
    assert mode.count == len(phases)
    cap = replace(cap, envelopes=(ChargingEnvelope(mode, 5, 25, 2, cap.stop),))
    result = run(
        100000,
        Direction.DOWN,
        capabilities=cap,
        voltage=observation,
        eligible_modes=(mode,),
        limits=(CurrentLimit(mode, 0, 10, "site"),),
    )
    assert result.point.mode == mode
    assert result.point.current_a == 9  # Limit intersects the min-anchored grid.
    expected_volts = tuple(p.voltage_v for p in observation.phases if p.phase in phases)
    assert result.point.phase_voltages_v == expected_volts
    assert result.point.offered_power_w == 9 * sum(expected_volts)


def test_explicit_session_limit_does_not_change_wallbox_capability(run, ac_modes):
    cap, observation, _ = ac_modes
    cap = replace(
        cap, envelopes=tuple(replace(e, max_current_a=32) for e in cap.envelopes)
    )
    one, two, three = (e.mode for e in cap.envelopes)
    session_limit = CurrentLimit(three, 0, 16, "temporary_session_acceptance")
    for mode, expected_current in ((one, 32), (two, 32), (three, 16)):
        result = run(
            100000,
            Direction.DOWN,
            capabilities=cap,
            voltage=observation,
            eligible_modes=(mode,),
            limits=(session_limit,),
        )
        assert result.point.current_a == expected_current
    # Caller removal restores the original offer; the solver learns no state.
    result = run(
        100000,
        Direction.DOWN,
        capabilities=cap,
        voltage=observation,
        eligible_modes=(three,),
    )
    assert result.point.current_a == 32
    assert all(e.max_current_a == 32 for e in cap.envelopes)
    assert all(e.evidence.state == EvidenceState.VERIFIED for e in cap.envelopes)


@pytest.mark.parametrize("missing", [Phase.L2, Phase.L3])
@pytest.mark.parametrize("direction", list(Direction))
def test_unavailable_phase_excludes_only_affected_modes(
    run, ac_modes, missing, direction
):
    cap, observation, _ = ac_modes
    observation = replace(
        observation, phases=tuple(p for p in observation.phases if p.phase != missing)
    )
    result = run(
        3400,
        direction,
        capabilities=cap,
        voltage=observation,
        eligible_modes=tuple(e.mode for e in cap.envelopes),
    )
    assert result.point.charging
    assert missing not in result.point.mode.phases
    if direction == Direction.DOWN:
        assert result.point.offered_power_w <= 3400
    elif direction == Direction.UP:
        assert result.point.offered_power_w >= 3400


@pytest.mark.parametrize("bad_phase", [Phase.L2, Phase.L3])
def test_expired_phase_does_not_exclude_other_modes(run, ac_modes, now, bad_phase):
    cap, observation, _ = ac_modes
    observation = replace(
        observation,
        phases=tuple(
            replace(p, observed_at=now - timedelta(seconds=60), valid_until=now)
            if p.phase == bad_phase
            else p
            for p in observation.phases
        ),
    )
    result = run(
        2300,
        capabilities=cap,
        voltage=observation,
        eligible_modes=tuple(e.mode for e in cap.envelopes),
    )
    assert bad_phase not in result.point.mode.phases
    assert result.point.charging
