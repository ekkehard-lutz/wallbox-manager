"""Exact causal measured-import accounting; no hardware or timer sleeps."""

from fractions import Fraction

import pytest

from custom_components.wallbox_manager.pv_budget import UNCERTAINTY_W
from custom_components.wallbox_manager.pv_economy import (
    ECONOMIC_REASON,
    LIMIT_WS,
    RESTART_MODEL,
    EconomicState,
    GridImportPolicy,
    MeterSample,
    RestartReference,
    restart_capacity,
    restart_improved,
)
from custom_components.wallbox_manager.pv_regulators import GRID_DEADBAND_W


def sample(at, imported, *, source=None, generation=None):
    source = at if source is None else source
    return MeterSample(
        ("site-grid", str(source)) if generation is None else generation,
        source,
        at,
        imported,
    )


def observe(policy, at, imported, threshold=690, **changes):
    return policy.observe(sample(at, imported, **changes), threshold_w=threshold)


def active(energy_ws=0):
    return GridImportPolicy(energy_ws, active=True)


@pytest.mark.parametrize("imported", [0, 689, 690])
def test_below_and_equal_suspend_all_accumulation(imported):
    policy = active(36000)
    observe(policy, 0, imported)
    observe(policy, 10, imported)
    assert policy.energy_ws == 36000
    assert policy.below_seconds == 10
    assert policy.state == EconomicState.BELOW_RESET_PENDING


@pytest.mark.parametrize("imported", [691, 701, 2000])
def test_above_threshold_counts_entire_positive_import(imported):
    policy = active()
    observe(policy, 0, imported)
    observe(policy, 10, imported)
    assert policy.energy_ws == imported * 10


def test_exact_50wh_crossing_has_no_extra_confirmation_delay():
    policy = active()
    for seconds in range(0, 90, 10):
        observe(policy, seconds, 2000)
    before = observe(policy, Fraction(89999, 1000), 2000)
    assert before.energy_ws == Fraction(179998)
    assert not before.exhausted
    reached = observe(policy, 90, 2000)
    assert reached.energy_ws == LIMIT_WS
    assert reached.energy_wh == 50
    assert reached.exhausted
    assert reached.pause_reason == ECONOMIC_REASON
    assert reached.state == EconomicState.BUDGET_EXHAUSTED


def test_short_high_spike_preserves_energy_during_brief_below_period():
    policy = active()
    observe(policy, 0, 5000)
    observe(policy, 5, 0)
    assert policy.energy_ws == 25000
    observe(policy, 15, 0)
    assert policy.energy_ws == 25000
    assert policy.below_seconds == 10


def test_exact_15_seconds_does_not_reset_but_strictly_more_does():
    policy = active(36000)
    for at in (0, 10, 15):
        observe(policy, at, 690)
    assert policy.energy_ws == 36000
    assert policy.below_seconds == 15
    observe(policy, Fraction(15001, 1000), 690)
    assert policy.energy_ws == 0
    assert not policy.exhausted


@pytest.mark.parametrize(
    "duration,expected",
    [(Fraction(14999, 1000), 72000), (15, 72000), (Fraction(15001, 1000), 0)],
)
def test_old_below_interval_closes_before_simultaneous_above_sample(duration, expected):
    policy = active(72000)
    observe(policy, 0, 0)
    observe(policy, 10, 0)
    observe(policy, duration, 3600)
    assert policy.energy_ws == expected
    assert policy.below_seconds == 0


def test_repeated_short_below_intervals_do_not_renew_allowance():
    policy = active()
    observe(policy, 0, 3600)
    for burst in range(10):
        at = burst * 15
        observe(policy, at + 5, 0)
        if burst != 9:
            observe(policy, at + 15, 3600)
    assert policy.energy_ws == LIMIT_WS
    assert policy.exhausted


def test_threshold_increase_splits_closed_interval_without_retroactivity():
    policy = active()
    observe(policy, 0, 2000)
    policy.set_threshold(2070, at=4)
    assert policy.energy_ws == 0
    assert policy.below_seconds == 0
    observe(policy, 10, 2000, 2070)
    assert policy.energy_ws == 8000
    assert policy.below_seconds == 6


def test_threshold_decrease_splits_closed_interval_without_retroactivity():
    policy = active(36000)
    observe(policy, 0, 2000, 2070)
    policy.set_threshold(690, at=4)
    observe(policy, 10, 2000)
    assert policy.energy_ws == 48000
    assert policy.below_seconds == 0


def test_several_threshold_changes_preserve_one_budget():
    policy = active(144000)
    observe(policy, 0, 2000)
    policy.set_threshold(2070, at=2)
    policy.set_threshold(690, at=7)
    observe(policy, 10, 2000)
    assert policy.energy_ws == 154000
    observe(policy, 20, 2000)
    observe(policy, 23, 2000)
    assert policy.energy_ws == LIMIT_WS


def test_threshold_change_at_15_second_boundary_keeps_exact_equality():
    policy = active(36000)
    observe(policy, 0, 2000, 2070)
    observe(policy, 10, 2000, 2070)
    policy.set_threshold(690, at=15)
    observe(policy, 15, 2000)
    assert policy.energy_ws == 36000
    assert policy.below_seconds == 0


def test_threshold_change_after_supported_15_seconds_resets_before_new_classification():
    policy = active(36000)
    observe(policy, 0, 2000, 2070)
    observe(policy, 10, 2000, 2070)
    at = Fraction(15001, 1000)
    policy.set_threshold(690, at=at)
    observe(policy, at, 2000)
    assert policy.energy_ws == 0


def test_duplicate_has_no_effect_on_energy_or_first_acceptance_anchor():
    policy = active()
    first = sample(0, 2000)
    policy.observe(first, threshold_w=690)
    for at in (1, 2, 8, 9):
        duplicate = sample(at, 2000, source=0)
        assert policy.observe(duplicate, threshold_w=690).energy_ws == 0
    observe(policy, 10, 2000)
    assert policy.energy_ws == 20000


def test_identical_value_in_distinct_generation_closes_interval():
    policy = active()
    observe(policy, 0, 2000)
    observe(policy, 5, 2000)
    observe(policy, 10, 2000)
    assert policy.energy_ws == 20000


@pytest.mark.parametrize("changes", [{"imported": 2500}, {"source": 1}])
def test_conflicting_duplicate_breaks_coverage_and_preserves_energy(changes):
    policy = active(36000)
    observe(policy, 0, 2000, generation="same")
    imported = changes.get("imported", 2000)
    policy.observe(
        sample(5, imported, source=changes.get("source", 0), generation="same"),
        threshold_w=690,
    )
    assert policy.coverage_reason == "conflicting_meter_generation"
    assert policy.energy_ws == 36000
    observe(policy, 10, 2000)
    assert policy.energy_ws == 36000
    observe(policy, 20, 2000)
    assert policy.energy_ws == 56000


def test_previously_seen_generation_cannot_be_reused_with_new_timestamp():
    policy = active()
    observe(policy, 0, 2000, generation="g0")
    observe(policy, 5, 2000, generation="g1")
    observe(policy, 10, 2000, generation="g0")
    assert policy.energy_ws == 10000
    assert policy.coverage_reason == "conflicting_meter_generation"


@pytest.mark.parametrize(
    "source,acceptance,reason",
    [(4, 10, "out_of_order_source_timestamp"), (10, 4, "out_of_order_acceptance")],
)
def test_out_of_order_samples_break_proof_without_negative_or_unknown_energy(
    source, acceptance, reason
):
    policy = active(36000)
    observe(policy, 5, 2000)
    observe(policy, acceptance, 2000, source=source)
    assert policy.energy_ws == 36000
    assert policy.coverage_reason == reason
    observe(policy, 15, 2000)
    assert policy.energy_ws == 36000


def test_future_source_timestamp_is_invalid_without_comparing_two_clock_axes():
    policy = active(36000)
    observe(policy, 0, 2000, source=1000000)
    policy.observe(sample(5, 2000, source=1000005), threshold_w=690, source_now=1000004)
    assert policy.energy_ws == 36000
    assert policy.coverage_reason == "future_source_timestamp"


@pytest.mark.parametrize(
    "accepted,source,expected",
    [
        (10, 10, 20000),
        (Fraction(10001, 1000), 10, 0),
        (10, Fraction(10001, 1000), 0),
    ],
)
def test_both_source_and_first_acceptance_gaps_obey_exact_10_second_cap(
    accepted, source, expected
):
    policy = active(36000)
    observe(policy, 0, 2000)
    observe(policy, accepted, 2000, source=source)
    assert policy.energy_ws == 36000 + expected


def test_long_gap_discards_whole_interval_and_next_valid_endpoint_reanchors():
    policy = active(36000)
    observe(policy, 0, 2000)
    observe(policy, 30, 2000)
    assert policy.energy_ws == 36000
    observe(policy, 35, 2000)
    assert policy.energy_ws == 46000


def test_missing_meter_does_not_turn_downtime_into_a_reset():
    policy = active(36000)
    observe(policy, 0, 0)
    observe(policy, 10, 0)
    policy.invalidate("missing_grid_meter")
    assert policy.below_seconds == 0
    observe(policy, 300, 0)
    observe(policy, 310, 0)
    assert policy.energy_ws == 36000
    assert policy.below_seconds == 10
    observe(policy, 316, 0)
    assert policy.energy_ws == 0


def test_gap_during_reset_pending_breaks_continuity():
    policy = active(36000)
    observe(policy, 0, 0)
    observe(policy, 10, 0)
    observe(policy, 21, 0)
    assert policy.below_seconds == 0
    observe(policy, 31, 0)
    assert policy.energy_ws == 36000


def test_irregular_supported_intervals_use_actual_acceptance_elapsed_time():
    policy = active()
    for at in (0, 3, 8, 14, 20):
        observe(policy, at, 1800)
    assert policy.energy_ws == 36000
    assert policy.energy_wh == 10


def test_receipt_axis_sets_duration_source_axis_only_proves_coverage():
    policy = active()
    observe(policy, 100, 2000, source=1000000)
    observe(policy, 108, 2000, source=1000005)
    assert policy.energy_ws == 16000


def test_exhaustion_survives_missing_anchor_duplicate_and_invalid_interval():
    policy = active(LIMIT_WS)
    assert policy.snapshot.exhausted
    assert policy.state == EconomicState.BUDGET_EXHAUSTED
    observe(policy, 0, 0)
    policy.mark_off_pending()
    policy.invalidate("unknown_authority")
    assert policy.snapshot.exhausted
    assert policy.state == EconomicState.OFF_PENDING
    assert policy.pause_reason == ECONOMIC_REASON
    observe(policy, 500, 0, source=0)
    assert policy.last_import_w is None
    assert policy.snapshot.exhausted


def test_off_pending_rejection_preserves_energy_and_only_verified_reset_clears_it():
    policy = active(LIMIT_WS)
    policy.mark_off_pending()
    for at in (0, 10, 15):
        observe(policy, at, 0)
    assert policy.off_pending
    assert policy.exhausted
    observe(policy, Fraction(15001, 1000), 0)
    assert not policy.off_pending
    assert not policy.exhausted
    assert policy.pause_reason is None


def test_confirmed_off_splits_exposure_causally_and_stops_household_accumulation():
    policy = active(LIMIT_WS)
    observe(policy, 0, 3600)
    policy.mark_off_pending()
    policy.confirm_off(at=5)
    observe(policy, 10, 4000)
    assert policy.energy_ws == 198000
    observe(policy, 20, 4000)
    assert policy.energy_ws == 198000
    assert policy.paused
    assert not policy.active
    assert not policy.off_pending


def test_confirmed_pause_keeps_cause_after_reset_until_positive_confirmation():
    policy = active(LIMIT_WS)
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    assert not policy.exhausted
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.state == EconomicState.ECONOMIC_PAUSED
    policy.confirm_active(at=16)
    assert policy.active
    assert not policy.paused
    assert policy.pause_reason is None


def test_zero_actual_import_before_off_ack_does_not_make_an_instant_reset():
    policy = active(LIMIT_WS)
    policy.mark_off_pending()
    observe(policy, 0, 0)
    observe(policy, 5, 0)
    assert policy.exhausted
    assert policy.off_pending


def test_authorization_boundary_discards_unclosed_tail_but_preserves_proved_budget():
    policy = active(36000)
    observe(policy, 0, 5000)
    policy.set_active(False, at=4)
    policy.invalidate("authority_lost")
    observe(policy, 10, 5000)
    assert policy.energy_ws == 36000
    policy.set_active(True, at=12)
    observe(policy, 20, 5000)
    assert policy.energy_ws == 76000


def test_policy_event_clock_regression_breaks_coverage():
    policy = active(36000)
    observe(policy, 5, 2000)
    policy.set_threshold(2070, at=4)
    assert policy.energy_ws == 36000
    assert policy.coverage_reason == "out_of_order_policy_event"
    observe(policy, 10, 2000, 2070)
    assert policy.energy_ws == 36000


def test_zero_duration_acceptance_interval_has_no_energy():
    policy = active()
    observe(policy, 0, 2000, source=100)
    observe(policy, 0, 3000, source=101)
    assert policy.energy_ws == 0
    observe(policy, 5, 3000, source=106)
    assert policy.energy_ws == 15000


def test_serialization_is_exact_and_omits_every_process_anchor():
    policy = active(Fraction(360001, 7))
    observe(policy, 0, 0)
    observe(policy, 10, 0)
    record = policy.serialize()
    assert record == {
        "version": 2,
        "energy_ws": {"numerator": 360001, "denominator": 7},
        "active": True,
        "off_pending": False,
        "off_in_flight": False,
        "paused": False,
        "restart_required": False,
        "pause_reason": None,
        "restart_reference": None,
    }
    restored = GridImportPolicy.restore(record)
    assert restored.energy_ws == policy.energy_ws
    assert restored.below_seconds == 0
    assert restored.last_import_w is None
    assert restored.threshold_w is None
    assert restored.state == EconomicState.SUSPENDED_UNKNOWN
    observe(restored, 300, 0)
    observe(restored, 310, 0)
    assert restored.energy_ws == policy.energy_ws


def test_restored_exhausted_budget_is_immediately_eligible_without_fresh_meter():
    policy = active(LIMIT_WS + Fraction(1, 3))
    policy.mark_off_pending()
    restored = GridImportPolicy.restore(policy.serialize())
    assert restored.exhausted
    assert restored.off_pending
    assert restored.pause_reason == ECONOMIC_REASON
    assert restored.energy_ws == LIMIT_WS + Fraction(1, 3)
    assert restored.last_import_w is None
    restored.invalidate("recovery_reconciliation")
    assert restored.exhausted


def test_restored_confirmed_pause_keeps_history_but_not_reset_continuity():
    policy = active(LIMIT_WS)
    policy.confirm_off(at=0)
    observe(policy, 0, 0)
    observe(policy, 10, 0)
    restored = GridImportPolicy.restore(policy.serialize())
    assert restored.paused
    assert restored.pause_reason == ECONOMIC_REASON
    assert restored.below_seconds == 0
    for at in (300, 310, 315):
        observe(restored, at, 0)
    assert restored.exhausted
    observe(restored, 316, 0)
    assert restored.energy_ws == 0
    assert restored.paused


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 3},
        {"version": True},
        {"energy_ws": {"numerator": -1, "denominator": 1}},
        {"energy_ws": {"numerator": True, "denominator": 1}},
        {"energy_ws": {"numerator": 1, "denominator": 0}},
        {"energy_ws": {"numerator": 1, "denominator": -1}},
        {"energy_ws": {"numerator": 1.0, "denominator": 1}},
        {"active": 1},
        {"off_pending": "false"},
        {"paused": None},
        {"pause_reason": "technical_busy"},
        {"paused": True, "active": True},
        {"paused": True, "active": False, "off_pending": True},
    ],
)
def test_invalid_durable_state_never_becomes_a_fresh_zero_budget(changes):
    record = active(36000).serialize() | changes
    with pytest.raises(ValueError):
        GridImportPolicy.restore(record)


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True])
def test_invalid_measured_import_is_not_coerced_to_zero(value):
    with pytest.raises(ValueError):
        sample(0, value)


def test_scope_exit_clears_energy_identity_history_and_pending_reason():
    policy = active(LIMIT_WS)
    observe(policy, 0, 2000, generation="g0")
    policy.mark_off_pending()
    policy.clear_scope()
    assert policy.energy_ws == 0
    assert not policy.exhausted
    assert policy.pause_reason is None
    assert not policy.active
    policy.confirm_active(at=0)
    observe(policy, 0, 2000, generation="g0")
    observe(policy, 5, 2000)
    assert policy.energy_ws == 10000


def test_recent_generation_memory_is_bounded_and_source_watermark_prevents_recredit():
    policy = active()
    for at in range(100):
        observe(policy, at, 2000)
    assert len(policy._generations) == 32
    energy = policy.energy_ws
    observe(policy, 100, 2000, source=0)
    assert policy.energy_ws == energy
    assert policy.coverage_reason == "out_of_order_source_timestamp"


@pytest.mark.parametrize(
    "changes",
    [
        {"off_pending": True},
        {"paused": True, "active": False, "pause_reason": None},
    ],
)
def test_inconsistent_economic_lifecycle_is_not_restored(changes):
    with pytest.raises(ValueError):
        GridImportPolicy.restore(active(36000).serialize() | changes)


def capacity(**changes):
    inputs = dict(
        ev_w=3000,
        imported_w=2000,
        exported_w=0,
        reserve_w=UNCERTAINTY_W,
        discharge_w=1000,
        allowed_discharge_w=3500,
        pv_w=1000,
        load_w=4000,
    )
    return restart_capacity(**(inputs | changes))


def improved(reference, available, candidate=1380):
    return restart_improved(reference, available, candidate, deadband_w=GRID_DEADBAND_W)


def test_battery_capacity_preserves_coherent_off_and_battery_grid_relief_invariance():
    before = capacity()
    after = capacity(ev_w=0, imported_w=0, discharge_w=0, load_w=1000)
    assert before == after == 3400
    assert not improved(RestartReference(before, 1380), after)


def test_no_battery_capacity_preserves_coherent_off_invariance_without_battery_inputs():
    before = restart_capacity(3000, 2000, 0, reserve_w=UNCERTAINTY_W)
    after = restart_capacity(0, 0, 1000, reserve_w=UNCERTAINTY_W)
    assert before == after == 1000
    assert not improved(RestartReference(before, 1380), after)


@pytest.mark.parametrize("improvement", [0, 1, 99, 100, Fraction(100001, 1000)])
def test_meaningful_capacity_improvement_strictly_exceeds_existing_deadband(
    improvement,
):
    reference = RestartReference(3400, 1380)
    assert improved(reference, 3400 + improvement) is (improvement > GRID_DEADBAND_W)


def test_capacity_signed_deficit_survives_without_becoming_zero():
    assert capacity(allowed_discharge_w=0) == -100
    assert restart_capacity(0, 1000, 0, reserve_w=UNCERTAINTY_W) == -1000


def test_existing_fast_allocation_is_intended_support_and_balance_uses_zero():
    fast = capacity()
    balance = capacity(allowed_discharge_w=0)
    assert fast - balance == 3500
    assert improved(RestartReference(balance, 1380), fast)
    assert not improved(RestartReference(fast, 1380), balance)


def test_cheaper_point_already_available_at_stop_gives_no_restart_credit():
    # A3p10 point may have been acknowledged, but the reference cost must use
    # the already executable1p6 technicalminimum, not that higher point.
    reference = RestartReference(1000, 1380)
    assert not improved(reference, 1000, 1380)
    assert not improved(reference, 1000, 4140)


def test_new_cheaper_technical_minimum_gives_real_candidate_cost_improvement():
    reference = RestartReference(1000, 4140)
    assert improved(reference, 1000, 1380)
    assert not improved(reference, 1000, 4140)


@pytest.mark.parametrize("cost_drop", [99, 100, Fraction(100001, 1000)])
def test_voltage_driven_technical_cost_change_uses_the_same_existing_noise_bound(
    cost_drop,
):
    reference = RestartReference(1000, 4140)
    assert improved(reference, 1000, 4140 - cost_drop) is (cost_drop > GRID_DEADBAND_W)


def test_larger_restart_candidate_cost_cannot_be_hidden_by_small_capacity_gain():
    reference = RestartReference(1000, 1380)
    assert not improved(reference, 1200, 1600)
    assert not improved(reference, 1320, 1600)
    assert improved(reference, 1321, 1600)


def test_clock_source_generation_and_budget_reset_do_not_change_restart_comparison():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(1000, 1380)
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    assert policy.energy_ws == 0
    assert policy.restart_reference is reference
    assert not improved(policy.restart_reference, 1000)
    observe(policy, 26, 0)
    assert not improved(policy.restart_reference, 1000)
    restored = GridImportPolicy.restore(policy.serialize())
    assert not improved(restored.restart_reference, 1000)


def test_first_restart_reference_is_not_replaced_by_later_stop_input_or_candidate():
    policy = active(LIMIT_WS)
    first = policy.capture_restart_reference(-101, 4140)
    assert policy.capture_restart_reference(6000, 1380) is first
    assert policy.snapshot.restart_reference is first
    assert first.capacity_w == -101
    assert first.minimum_w == 4140


def test_restart_reference_needs_proved_exhaustion():
    with pytest.raises(ValueError):
        active(LIMIT_WS - 1).capture_restart_reference(1000, 1380)


def test_positive_acknowledgement_and_explicit_scope_exit_clear_restart_reference():
    policy = active(LIMIT_WS)
    policy.capture_restart_reference(1000, 1380)
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    policy.confirm_active(at=16)
    assert policy.restart_reference is None
    policy.energy_ws = LIMIT_WS
    policy.capture_restart_reference(1000, 1380)
    policy.clear_scope()
    assert policy.restart_reference is None


def test_restart_reference_serialization_preserves_signed_fraction_exactly():
    reference = RestartReference(Fraction(-101, 3), Fraction(8281, 2))
    record = reference.serialize()
    assert record == {
        "version": 1,
        "model": RESTART_MODEL,
        "capacity_w": {"numerator": -101, "denominator": 3},
        "minimum_w": {"numerator": 8281, "denominator": 2},
    }
    assert RestartReference.restore(record) == reference
    policy = active(LIMIT_WS)
    policy.capture_restart_reference(reference.capacity_w, reference.minimum_w)
    restored = GridImportPolicy.restore(policy.serialize())
    assert restored.restart_reference == reference
    assert restored.last_import_w is None
    assert restored.below_seconds == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"model": "unknown_restart_model"},
        {"capacity_w": {"numerator": True, "denominator": 1}},
        {"capacity_w": {"numerator": 1, "denominator": 0}},
        {"minimum_w": {"numerator": 0, "denominator": 1}},
        {"minimum_w": {"numerator": -1, "denominator": 1}},
        {"minimum_w": {"numerator": 1, "denominator": -1}},
    ],
)
def test_invalid_restart_reference_is_not_restored(changes):
    reference = RestartReference(1000, 1380).serialize() | changes
    with pytest.raises(ValueError):
        RestartReference.restore(reference)
    with pytest.raises(ValueError):
        GridImportPolicy.restore(
            active(LIMIT_WS).serialize() | {"restart_reference": reference}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"ev_w": None},
        {"ev_w": float("nan")},
        {"imported_w": -1},
        {"exported_w": float("inf")},
        {"discharge_w": None},
        {"allowed_discharge_w": None},
        {"pv_w": None},
        {"load_w": None},
        {"discharge_w": -1},
        {"reserve_w": -1},
    ],
)
def test_uncertain_capacity_inputs_do_not_grant_restart(changes):
    with pytest.raises(ValueError):
        capacity(**changes)


def test_missing_restart_reference_never_grants_automatic_restart():
    assert not improved(None, 10000)


def test_busy_off_reset_and_reexhaustion_capture_the_new_episode_baseline():
    policy = active(LIMIT_WS)
    first = policy.capture_restart_reference(1000, 1380)
    policy.mark_off_pending()  # BUSY: the old positive offer remains confirmed.
    for at in (0, 10, 15):
        observe(policy, at, 0)
    assert policy.exhausted and policy.off_pending
    assert policy.restart_reference is first  # Exactly15s cannot cancel OFF.
    observe(policy, 16, 0)
    assert policy.active and not policy.paused
    assert policy.energy_ws == 0
    assert not policy.off_pending
    assert policy.pause_reason is None
    assert policy.restart_reference is None
    # Continuing the same acknowledged positive point need not produce another
    # positive ACK. A later economic episode still requires a fresh reference.
    observe(policy, 20, 3600)
    for at in (30, 40, 50, 60, 70):
        observe(policy, at, 3600)
    assert policy.energy_ws == LIMIT_WS
    second = policy.capture_restart_reference(2000, 1380)
    assert second is not first
    assert second.capacity_w == 2000
    assert not improved(second, 2000)
    assert improved(first, 2000)  # Demonstrates the stale-reference cycle bug.


@pytest.mark.parametrize("confirmed_off", [False, True])
def test_reset_persistence_keeps_reference_only_for_confirmed_economic_pause(
    confirmed_off,
):
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(Fraction(-101, 3), 4140)
    policy.mark_off_pending()
    if confirmed_off:
        policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    expected = reference if confirmed_off else None
    assert policy.energy_ws == 0
    assert policy.restart_reference == expected
    assert policy.pause_reason == (ECONOMIC_REASON if confirmed_off else None)
    restored = GridImportPolicy.restore(policy.serialize())
    assert restored.energy_ws == 0
    assert restored.restart_reference == expected
    assert restored.paused is confirmed_off
    assert restored.active is not confirmed_off
    assert restored.below_seconds == 0
    assert restored.last_import_w is None


def test_sent_off_reset_retains_cause_and_reference_through_late_ack_and_recovery():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(3400, 1380)
    policy.mark_off_pending()
    policy.mark_off_in_flight()
    for at in (0, 10, 16):
        observe(policy, at, 0)
    assert policy.energy_ws == 0 and not policy.exhausted
    assert policy.off_pending and policy.off_in_flight
    assert not policy.paused and policy.active
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.restart_reference is reference
    assert policy.snapshot.off_in_flight
    assert policy.state == EconomicState.OFF_PENDING
    restored = GridImportPolicy.restore(policy.serialize())
    restored.invalidate("recovery_reconciliation")
    assert restored.off_pending and restored.off_in_flight
    assert restored.restart_reference == reference
    assert restored.last_import_w is None and restored.below_seconds == 0
    restored.confirm_off(at=300)
    assert restored.paused and not restored.active
    assert not restored.off_pending and not restored.off_in_flight
    assert restored.pause_reason == ECONOMIC_REASON
    assert restored.restart_reference == reference
    assert not improved(restored.restart_reference, 3400)


@pytest.mark.parametrize("partial_energy", [False, True])
def test_known_busy_after_sent_reset_cancels_old_cause_but_keeps_proved_energy(
    partial_energy,
):
    policy = active(LIMIT_WS)
    policy.capture_restart_reference(1000, 1380)
    policy.mark_off_pending()
    policy.mark_off_in_flight()
    for at in (0, 10, 16):
        observe(policy, at, 0)
    if partial_energy:
        observe(policy, 20, 3600)
        observe(policy, 30, 3600)
    expected = 36000 if partial_energy else 0
    assert policy.energy_ws == expected
    policy.mark_off_not_applied()
    assert policy.energy_ws == expected
    assert policy.active and not policy.paused
    assert not policy.off_pending and not policy.off_in_flight
    assert policy.pause_reason is None and policy.restart_reference is None
    assert GridImportPolicy.restore(policy.serialize()).energy_ws == expected


def test_known_busy_with_exhausted_budget_retains_stop_and_first_reference():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(1000, 1380)
    policy.mark_off_pending()
    policy.mark_off_in_flight()
    policy.mark_off_not_applied()
    assert policy.exhausted and policy.off_pending
    assert not policy.off_in_flight
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.restart_reference is reference


def test_unsent_canceled_off_never_acquires_a_wire_flight_flag():
    policy = active(LIMIT_WS)
    policy.capture_restart_reference(1000, 1380)
    policy.mark_off_pending()
    for at in (0, 10, 16):
        observe(policy, at, 0)
    policy.mark_off_not_applied()
    assert not policy.off_in_flight and not policy.off_pending
    assert policy.restart_reference is None
    with pytest.raises(ValueError):
        policy.mark_off_in_flight()


def test_late_positive_ack_keeps_new_wire_uncertainty_until_explicit_scope_exit():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(1000, 1380)
    policy.mark_off_pending()
    policy.mark_off_in_flight()
    for at in (0, 10, 16):
        observe(policy, at, 0)
    policy.confirm_active(at=16)
    assert policy.active and not policy.paused
    assert policy.off_in_flight and policy.off_pending
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.restart_reference is reference
    assert policy.energy_ws == 0
    durable = GridImportPolicy.restore(policy.serialize())
    assert durable.off_in_flight and durable.off_pending
    policy.clear_scope()
    assert not policy.off_in_flight and not policy.off_pending
    assert policy.energy_ws == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"off_in_flight": None},
        {"off_in_flight": 1},
        {"off_in_flight": True, "off_pending": False},
        {"off_in_flight": True, "off_pending": True, "paused": True, "active": False},
        {
            "energy_ws": {"numerator": 0, "denominator": 1},
            "off_in_flight": True,
            "off_pending": True,
            "pause_reason": None,
        },
    ],
)
def test_invalid_wire_flight_state_never_restores_as_a_fresh_allowance(changes):
    with pytest.raises(ValueError):
        GridImportPolicy.restore(active(LIMIT_WS).serialize() | changes)


def test_previous_policy_version_without_wire_state_is_not_silently_restored():
    record = active(LIMIT_WS).serialize()
    record["version"] = 1
    record.pop("off_in_flight")
    with pytest.raises(ValueError, match="policy version"):
        GridImportPolicy.restore(record)


def test_confirmed_pause_reassertion_keeps_restart_provenance_through_prewire_reset():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(3400, 1380)
    policy.confirm_off(at=0)
    assert policy.paused and policy.restart_required
    restored = GridImportPolicy.restore(policy.serialize())
    restored.mark_off_pending()  # Reassert OFF after uncertain live readback.
    assert restored.off_pending and not restored.paused
    assert restored.restart_required and not restored.off_in_flight
    for at in (0, 10, 16):
        observe(restored, at, 0)
    assert restored.energy_ws == 0
    assert restored.restart_required and restored.off_pending
    assert restored.restart_reference == reference
    assert restored.pause_reason == ECONOMIC_REASON
    restored.mark_off_not_applied()  # Known BUSY cannot undo the original pause.
    assert restored.restart_required
    assert restored.restart_reference == reference
    assert restored.pause_reason == ECONOMIC_REASON
    assert not improved(restored.restart_reference, 3400)
    durable = GridImportPolicy.restore(restored.serialize())
    assert durable.restart_required and durable.restart_reference == reference
    assert durable.energy_ws == 0 and durable.off_pending


def test_observed_positive_readback_does_not_replace_a_verified_economic_restart():
    policy = active(LIMIT_WS)
    reference = policy.capture_restart_reference(3400, 1380)
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    policy.set_active(True, at=16)
    assert policy.active and not policy.paused
    assert policy.restart_required
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.restart_reference is reference
    restored = GridImportPolicy.restore(policy.serialize())
    assert restored.active and restored.restart_required and not restored.paused
    assert restored.restart_reference == reference
    assert not improved(restored.restart_reference, 3400)
    restored.confirm_active(at=300)  # Only the authorized positive ACK clears it.
    assert restored.active and not restored.restart_required
    assert restored.restart_reference is None and restored.pause_reason is None


def test_constructor_pause_provenance_and_explicit_scope_exit_are_consistent():
    policy = GridImportPolicy(
        0,
        paused=True,
        pause_reason=ECONOMIC_REASON,
        restart_reference=RestartReference(3400, 1380),
    )
    assert policy.restart_required  # New constructor derives the explicit pause.
    policy.mark_off_pending()
    policy.clear_scope()
    assert not policy.restart_required
    assert policy.restart_reference is None and policy.pause_reason is None


@pytest.mark.parametrize(
    "changes",
    [
        {"restart_required": None},
        {"restart_required": 1},
        {"restart_required": "false"},
        {"restart_required": False, "paused": True, "active": False},
        {
            "restart_required": True,
            "energy_ws": {"numerator": 0, "denominator": 1},
            "pause_reason": None,
        },
    ],
)
def test_invalid_restart_provenance_cannot_restore_a_fresh_automatic_allowance(changes):
    with pytest.raises(ValueError):
        GridImportPolicy.restore(active(LIMIT_WS).serialize() | changes)


def test_missing_provenance_in_unreleased_version2_requires_explicit_recovery():
    record = active(LIMIT_WS).serialize()
    record.pop("restart_required")
    with pytest.raises(ValueError, match="provenance"):
        GridImportPolicy.restore(record)


def test_late_positive_ack_cannot_erase_a_new_exhausted_episode_reference():
    policy = GridImportPolicy(
        0,
        paused=True,
        pause_reason=ECONOMIC_REASON,
        restart_reference=RestartReference(1000, 1380),
    )
    policy.confirm_active(at=0)  # Guarded positive wire receives fresh EV response.
    assert policy.active and not policy.restart_required
    assert policy.restart_reference is None
    for at in (0, 10, 20, 30, 40, 50):
        observe(policy, at, 3600)
    assert policy.energy_ws == LIMIT_WS
    reference = policy.capture_restart_reference(2000, 1380)
    policy.mark_off_pending()
    policy.confirm_active(at=50)  # The earlier positive command is ACKed late.
    assert policy.energy_ws == LIMIT_WS
    assert policy.pause_reason == ECONOMIC_REASON
    assert policy.restart_reference is reference
    policy.confirm_off(at=50)
    for at in (60, 70, 76):
        observe(policy, at, 0)
    assert policy.energy_ws == 0
    assert policy.restart_required and policy.restart_reference is reference
    assert not improved(policy.restart_reference, 2000)


def test_authorized_positive_receipt_clears_only_old_historical_reassertion():
    policy = GridImportPolicy(
        0,
        paused=True,
        pause_reason=ECONOMIC_REASON,
        restart_reference=RestartReference(1000, 1380),
    )
    policy.mark_off_pending()
    assert policy.restart_required and policy.off_pending
    policy.confirm_active(at=0)
    assert policy.active and not policy.restart_required
    assert not policy.paused and not policy.off_pending and not policy.off_in_flight
    assert policy.pause_reason is None and policy.restart_reference is None
