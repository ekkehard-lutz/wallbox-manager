"""Physical FAST feedback below an offer must still be causal and fresh."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.pv_budget import PowerEvidence
from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator
from custom_components.wallbox_manager.solver.operating_point import OperatingPoint

START = datetime(2026, 10, 13, tzinfo=UTC)
VOLTAGE = Fraction(2059, 9)


def point(amps):
    if not amps:
        return OperatingPoint.off()
    return OperatingPoint(
        True, PhaseMode.canonical(1), amps, amps * VOLTAGE, (VOLTAGE,)
    )


def evidence(
    second, *, ev_second=None, stale_channel=None, stale_second=5, actual=1888
):
    times = [START + timedelta(seconds=second)] * 6
    if ev_second is not None:
        times[0] = START + timedelta(seconds=ev_second)
    if stale_channel is not None:
        times[stale_channel] = START + timedelta(seconds=stale_second)
    return PowerEvidence(tuple(times), Fraction(3000), Fraction(actual + 1000))


def request(
    regulator, second, *, actual=1888, confirmed=None, pending=False, sample=None
):
    return regulator.request(
        actual,
        1000,
        4000,
        0,
        0,
        now=second,
        interval=5,
        evidence=sample or evidence(second, actual=actual),
        confirmed=confirmed or point(9),
        pending=pending,
        step=VOLTAGE,
    )


def dispatch(regulator, accepted):
    # Keep the behavioral reproduction runnable against beta.13, which lacked
    # a dispatch snapshot. A missing snapshot must fail the power assertions.
    if dispatched := getattr(regulator, "dispatched", None):
        dispatched(accepted)


def charging_before_increase():
    regulator = FastDischargeRegulator()
    request(regulator, 0, actual=1260, confirmed=point(6))
    request(regulator, 5, actual=1260, confirmed=point(6))
    dispatch(regulator, point(9))
    regulator.acknowledge(point(9), 6)
    regulator.requested = point(9).offered_power_w
    return regulator


def test_lower_than_offered_physical_response_allows_evidence_driven_increase():
    regulator = charging_before_increase()
    power = request(regulator, 12, sample=evidence(12, ev_second=10))
    assert regulator.hard_max == 4788
    assert power == 3338
    assert regulator.response_observed
    assert regulator.reason == "new_evidence_increase"


def test_each_later_increase_requires_its_own_physical_response():
    regulator = charging_before_increase()
    power = request(regulator, 12)
    assert power > point(9).offered_power_w
    dispatch(regulator, point(14))
    regulator.acknowledge(point(14), 13)
    for second in (15, 35, 60, 4800):
        assert request(regulator, second, confirmed=point(14)) == power
        assert not regulator.response_observed
    assert regulator.reason == "unobserved_command_no_increase"


def test_ack_and_response_timeout_do_not_prove_an_unchanged_ev_responded():
    regulator = charging_before_increase()
    power = regulator.requested
    for second in (10, 15, 26, 60, 4800):
        assert request(regulator, second, actual=1260) == power
        assert not regulator.response_observed
    assert regulator.reason == "unobserved_command_no_increase"


@pytest.mark.parametrize("stale_channel", range(6))
def test_physical_movement_needs_every_source_to_cross_command(stale_channel):
    regulator = charging_before_increase()
    power = regulator.requested
    assert (
        request(regulator, 12, sample=evidence(12, stale_channel=stale_channel))
        == power
    )
    assert not regulator.response_observed
    assert request(regulator, 15) > power
    assert regulator.response_observed


def test_same_response_generation_cannot_be_spent_repeatedly():
    regulator = charging_before_increase()
    sample = evidence(12)
    power = request(regulator, 12, sample=sample)
    assert power > point(9).offered_power_w
    revision = regulator.revision
    for second in (17, 30, 60):
        assert request(regulator, second, sample=sample) == power
        assert regulator.revision == revision
        assert regulator.reason == "reused_evidence"


def test_response_before_delayed_ack_keeps_the_pre_dispatch_baseline():
    regulator = FastDischargeRegulator()
    request(regulator, 0, actual=1260, confirmed=point(6))
    request(regulator, 5, actual=1260, confirmed=point(6))
    dispatch(regulator, point(9))
    # The independent observer can already see charging rise while the ACK waits.
    request(regulator, 8, confirmed=point(6), pending=True)
    regulator.acknowledge(point(9), 15)
    regulator.requested = point(9).offered_power_w
    assert request(regulator, 16) == 3338
    assert regulator.response_observed


@pytest.mark.parametrize("actual", [0, 1160, 1260, 1360, 2300])
def test_noise_wrong_direction_or_power_above_offer_does_not_prove_response(actual):
    regulator = charging_before_increase()
    request(regulator, 30, actual=actual)
    assert not regulator.response_observed


def test_an_adopted_under_offer_without_causal_baseline_remains_conservative():
    regulator = FastDischargeRegulator()
    regulator.requested = point(9).offered_power_w
    for second in (0, 10, 30, 4800):
        assert request(regulator, second) == point(9).offered_power_w
        assert not regulator.response_observed


def test_under_offer_response_wait_does_not_delay_a_binding_budget_reduction():
    regulator = charging_before_increase()
    reduced = regulator.request(
        1260,
        4500,
        4000,
        0,
        0,
        now=7,
        interval=300,
        evidence=evidence(7, actual=1260),
        confirmed=point(9),
        pending=True,
        step=VOLTAGE,
    )
    assert reduced == regulator.hard_max == 660
    assert not regulator.response_observed


def test_partial_under_offer_response_preserves_existing_phase_settling_window():
    regulator = charging_before_increase()
    request(regulator, 12)
    three = OperatingPoint(True, PhaseMode.canonical(3), 6, 4140, (230,) * 3)
    dispatch(regulator, three)
    regulator.acknowledge(three, 13)
    regulator.requested = three.offered_power_w
    # A real rise to 3000 W cannot prove that a 3-phase transition has settled.
    for second in (15, 25, 32):
        request(regulator, second, actual=3000, confirmed=three)
        assert not regulator.response_observed
        assert regulator.response_settling(second)
    request(regulator, 33, actual=3000, confirmed=three)
    assert regulator.response_observed
    assert not regulator.response_settling(33)
