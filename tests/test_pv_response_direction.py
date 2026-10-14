"""Physical response direction follows the changed current, not offered watts."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.pv_budget import PowerEvidence
from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator
from custom_components.wallbox_manager.solver.operating_point import OperatingPoint

START = datetime(2026, 10, 13, tzinfo=UTC)


def point(amps):
    volts = Fraction(230)
    return OperatingPoint(True, PhaseMode.canonical(1), amps, amps * volts, (volts,))


def request(regulator, second, actual, confirmed):
    sample = PowerEvidence(
        (START + timedelta(seconds=second),) * 6,
        Fraction(3000),
        Fraction(actual + 1000),
    )
    return regulator.request(
        actual,
        1000,
        4000,
        0,
        0,
        now=second,
        interval=5,
        evidence=sample,
        confirmed=confirmed,
        step=230,
    )


def reduced_under_consuming_ev():
    regulator = FastDischargeRegulator(requested=point(12).offered_power_w)
    request(regulator, 0, 2300, point(12))
    if dispatched := getattr(regulator, "dispatched", None):
        dispatched(point(11))
    regulator.acknowledge(point(11), 1)
    regulator.requested = point(11).offered_power_w
    return regulator


def test_true_downward_under_offer_response_allows_later_controlled_progress():
    regulator = reduced_under_consuming_ev()
    # The current decreases 12 A -> 11 A, and measured consumption decreases
    # 2300 W -> 2110 W. Both samples remain below even the lower 2530 W offer.
    power = request(regulator, 5, 2110, point(11))
    assert regulator.response_observed
    assert power > point(11).offered_power_w
    assert regulator.reason == "new_evidence_increase"


def test_upward_power_movement_does_not_prove_a_downward_current_command():
    regulator = reduced_under_consuming_ev()
    for second in (5, 30, 300, 4800):
        # An unrelated 110 W rise points toward the offered watts, but against
        # the actual current command and outside the existing near-offer proof.
        power = request(regulator, second, 2410, point(11))
        assert not regulator.response_observed
        assert power == point(11).offered_power_w
    assert regulator.reason == "unobserved_command_no_increase"


def test_balance_fresh_off_response_restores_start_without_another_ramp_interval():
    regulator = FastDischargeRegulator()

    def start(source_second, confirmed):
        sample = PowerEvidence(
            (START + timedelta(seconds=source_second),) * 6,
            Fraction(8000),
            Fraction(2000),
        )
        return regulator.balance_request(
            6000,
            0,
            0,
            0,
            0,
            6000,
            now=0,
            interval=5,
            evidence=sample,
            confirmed=confirmed,
        )

    assert start(0, None) == 5900
    # The configured start delay can select OFF before a positive dispatch.
    regulator.dispatched(OperatingPoint.off())
    regulator.acknowledge(OperatingPoint.off(), 0)
    assert regulator.requested == 0
    # New sources prove no load. Restoring the initial preference must retain
    # the existing start-delay semantics at this same regulation-clock instant.
    assert start(1, OperatingPoint.off()) == 5900
    assert regulator.response_observed
