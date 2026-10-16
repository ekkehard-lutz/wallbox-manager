"""Measured economics through real HA observations, solver and OCPP peers."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from unittest.mock import AsyncMock

import pytest
from ocpp.exceptions import InternalError
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta12 import authority, voltages
from test_pv_optimum_hold import configure, evaluate, period
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import power_measurement
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import ControlAuthority
from custom_components.wallbox_manager.core.capabilities import (
    CurrentLimit,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.pv_economy import ECONOMIC_REASON, LIMIT_WS
from custom_components.wallbox_manager.pv_economy_storage import EconomicStorage


def cohort(
    p,
    t,
    clock,
    second,
    *,
    source=None,
    actual=2300,
    imported=0,
    exported=0,
    discharge=1000,
    pv=4000,
    load=2800,
    ev_source=None,
):
    """One explicit site generation; update import last for its observer."""
    clock[0] = second
    source = source or p._economic_test_source + timedelta(seconds=float(second))
    states = (
        ("sensor.selected", actual),
        (p.references["leistung_pv"], pv),
        (p.references["leistung_verbraucher"], load),
        (p.references["storage_discharge_power"], discharge),
        (p.references["grid_export_power"], exported),
        (p.references["grid_import_power"], imported),
    )
    for entity, value in states:
        old = p.hass.states.get(entity)
        at = ev_source if entity == "sensor.selected" and ev_source else source
        if entity == "sensor.selected":
            power_measurement(p, t, value, at=at)
            continue
        p.hass.states.async_set(
            entity,
            value,
            {**old.attributes, "observed_at": at.isoformat()},
        )


async def prepared(grid, *, phases=1, current=10, imported=0):
    p, t, context, clock = configure(grid)
    c, _, _, _, source, _ = context
    await p.economic_end_scope(t)
    p.setting(t)["profile"] = "NETZ"
    source.mode = PhaseMode.canonical(phases)
    c.restore(t, allowed_current_1p=32, allowed_current_3p=32)
    assert (
        await c.change(t, target_w=230 * phases * current, allowed=True)
    ).status == CommandStatus.APPLIED
    p._economic_test_source = datetime.now(UTC) - timedelta(seconds=60)
    # These cohorts use a simulated source clock, including samples before wall time.
    ledger = c.runtime.sessions
    session = ledger.get(t)
    ledger.clear_live(t.station)
    ledger._records[session.session_id] = replace(
        session,
        started_at=p._economic_test_source - timedelta(seconds=1),
        power_at=None,
        current_power_w=None,
    )
    cohort(p, t, clock, 0, actual=230 * phases * current, imported=imported)
    p.setting(t)["profile"] = "PV_OPTIMUM"
    p.optimum_initialize(t)
    p.optimum_modes[t] = "FAST_DISCHARGE"
    p.status[t] = "actively_charging"
    p.pv_ongoing[t] = True
    assert await p.economic_prepare(t, resume=False)
    assert p.economic_policies[t].active
    return p, t, context, clock


def lock_current_phase(c, t):
    state = c.runtime.get(t.station)
    c._phase_restrictions[t] = (
        state.token,
        state.authority_revision,
        c.inputs(t).current_mode,
    )
    assert c.phase_restricted(t)


async def exhausted(grid, *, phases=1):
    p, t, context, clock = await prepared(grid, phases=phases, imported=1300)
    policy = p.economic_policies[t]
    capacity, _ = p.economic_capacity(t)
    policy.energy_ws = LIMIT_WS
    policy.capture_restart_reference(
        capacity, p.economic_minimum(t).point.offered_power_w
    )
    return p, t, context, clock


async def dispatch_economic_off(p, t):
    """Exercise the shared runtime hook separately from planner migration.

    The test chooses verified OFF while preserving the exhausted policy. NETZ
    supplies its unrestricted OFF validation; this does not exercise the PV
    planner's automatic STOP selection, which has its own separate test.
    """
    profile = p.setting(t)["profile"]
    p.setting(t)["profile"] = "NETZ"
    try:
        p.control._edit(t, {"target_w": Fraction(0)})
        p.control.intent(t).policy_pause = True
        p.status[t] = ECONOMIC_REASON
        return await p.control.apply_stored(t)
    finally:
        p.setting(t)["profile"] = profile


@pytest.mark.parametrize("phases,expected", [(1, 1380), (3, 4140)])
async def test_technical_minimum_uses_available_phase_domain_without_economic_h(
    grid, phases, expected
):
    p, t, (c, _, peer, *_), _ = await prepared(grid, phases=phases)
    if phases == 3:
        lock_current_phase(c, t)
    c.intent(t).hard_max_w = Fraction(100)
    minimum = p.economic_minimum(t)
    assert minimum.point.offered_power_w == expected
    assert minimum.point.charging
    assert len(peer.requests) == 1


async def test_technical_minimum_respects_current_grid_and_vehicle_user_limits(grid):
    p, t, (c, _, peer, _, source, _), _ = await prepared(grid, phases=3)
    lock_current_phase(c, t)
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, min_current_a=7, current_step_a=2)
            for e in source.snapshot.envelopes
        ),
    )
    source.permitted = (CurrentLimit(source.mode, 8, 12, "vehicle"),)
    c.restore(t, allowed_current_3p=10)
    voltage(c, t, 220)
    minimum = p.economic_minimum(t).point
    assert minimum.current_a == 9
    assert minimum.offered_power_w == 9 * (220 + 230 + 230)
    before = len(peer.requests)
    source.permitted = (CurrentLimit(source.mode, 8, 8, "vehicle"),)
    minimum = p.economic_minimum(t)
    assert minimum is None or minimum.point is None or not minimum.point.charging
    assert len(peer.requests) == before


@pytest.mark.parametrize(
    "missing", ["caps", "advertised", "voltage", "phase", "authority", "owner"]
)
async def test_no_technical_floor_is_invented_without_genuine_input_or_permission(
    grid, missing
):
    p, t, (c, _, peer, _, source, _), _ = await prepared(grid)
    before = len(peer.requests)
    if missing == "caps":
        source.snapshot = None
    elif missing == "advertised":
        source.snapshot = replace(
            source.snapshot,
            envelopes=tuple(
                replace(e, evidence=replace(e.evidence, state=EvidenceState.ADVERTISED))
                for e in source.snapshot.envelopes
            ),
        )
    elif missing == "voltage":
        state = c.runtime.get(t.station)
        c.runtime._publish(replace(state, observations=()))
    elif missing == "phase":
        source.mode = None
    elif missing == "authority":
        authority(c, t, ControlAuthority.LOCAL)
    else:
        c.profile_permitted = lambda _: False
    minimum = p.economic_minimum(t)
    assert minimum is None or minimum.point is None or not minimum.point.charging
    assert len(peer.requests) == before


async def test_probe_deadline_alone_does_not_reclassify_locked_threshold(grid):
    p, t, (c, *_), clock = await prepared(grid, phases=3)
    lock_current_phase(c, t)
    p.economic_observe(t)
    assert p.economic_policies[t].threshold_w == 2070
    p.pv_retry_until[t] = 60
    p.pv_phase_retry[t] = True
    clock[0] = 60
    p.economic_observe(t)
    assert p.economic_policies[t].threshold_w == 2070
    assert c.phase_restricted(t)


async def test_actual_state_reported_same_value_unique_source_counts_once(grid):
    p, t, _, clock = await prepared(grid, imported=2000)
    policy = p.economic_policies[t]
    for second in (1, 2, 8, 9):
        clock[0] = second
        state = p.hass.states.get(p.references["grid_import_power"])
        p.hass.states.async_set(state.entity_id, state.state, dict(state.attributes))
        await p.hass.async_block_till_done()
        assert policy.energy_ws == 0
    cohort(p, t, clock, 10, imported=2000)
    await p.hass.async_block_till_done()
    assert policy.energy_ws == 20000
    # Dispatch/planning reads of the same measurement cannot spend another10s.
    p.economic_observe(t)
    p.economic_observe(t)
    assert policy.energy_ws == 20000


@pytest.mark.parametrize("elapsed,expected", [(10, 20000), (10.001, 0)])
async def test_actual_meter_interval_10s_boundary_and_gap_reanchors(
    grid, elapsed, expected
):
    p, t, _, clock = await prepared(grid, imported=2000)
    cohort(p, t, clock, elapsed, imported=2000)
    await p.hass.async_block_till_done()
    assert p.economic_policies[t].energy_ws == expected
    cohort(p, t, clock, elapsed + 5, imported=2000)
    await p.hass.async_block_till_done()
    assert p.economic_policies[t].energy_ws == expected + 10000


async def test_audit_87w_economic_shortfall_keeps_exact_locked_floor(grid):
    p, t, (c, _, peer, *_), clock = await prepared(grid, phases=3, current=6)
    lock_current_phase(c, t)
    p.setting(t)["optimum_max_discharge_w"] = 4500
    voltages(c, t, (229.444351, 229.372330, 228.048340))
    cohort(
        p,
        t,
        clock,
        1,
        actual=Fraction("3996.944"),
        discharge=4363,
        imported=Fraction("1078.7"),
        pv=Fraction("2833.5"),
        load=Fraction("7917.7"),
    )
    before = len(peer.requests)
    result = await evaluate(p, t)
    assert result is None or result.status == CommandStatus.APPLIED
    assert c.intent(t).hard_max_w == Fraction("4033.944")
    floor = p.economic_minimum(t).point
    assert floor.offered_power_w == Fraction("4121.190126")
    assert floor.offered_power_w > c.intent(t).hard_max_w
    assert c.confirmed_point(t).current_a == 6
    assert c.confirmed_point(t).mode.count == 3
    assert p.economic_policies[t].energy_ws == 0
    assert p.economic_policies[t].last_import_w < floor.offered_power_w / 2
    assert all(period(request)["limit"] > 0 for request in peer.requests[before:])


@pytest.mark.parametrize("outcome", ["Accepted", "Busy", "failure"])
async def test_exhausted_off_uses_command_boundary_flush_ack_and_retry_state(
    grid, monkeypatch, outcome
):
    p, t, (c, _, peer, *_), _ = await exhausted(grid)
    policy = p.economic_policies[t]
    actual_flush = p.economic_store.flush
    flushed = []

    async def flush():
        result = await actual_flush()
        flushed.append(policy.serialize())
        return result

    monkeypatch.setattr(p.economic_store, "flush", flush)
    if outcome == "Busy":
        peer.profile_response = lambda _: call_result.SetChargingProfile(
            status="Rejected", status_info={"reason_code": "Busy"}
        )
    elif outcome == "failure":
        peer.error = InternalError("simulated protocol failure")
    before = len(peer.requests)
    result = await dispatch_economic_off(p, t)
    assert flushed and flushed[0]["off_pending"]
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0
    assert policy.energy_ws == LIMIT_WS
    assert policy.exhausted
    if outcome == "Accepted":
        assert result.status == CommandStatus.APPLIED
        assert policy.paused and not policy.active
        assert not c.confirmed_point(t).charging
    elif outcome == "Busy":
        assert result.status == CommandStatus.TEMPORARILY_REJECTED
        assert policy.active and policy.off_pending
        assert c.confirmed_point(t).charging
    else:
        assert result.status == CommandStatus.FAILED
        assert policy.active and policy.off_pending
    assert policy.pause_reason == ECONOMIC_REASON


@pytest.mark.parametrize("enabled", [False, True])
async def test_reload_exhausted_budget_precedes_any_positive_preparation(grid, enabled):
    p, t, (c, bound, peer, *_), _ = await exhausted(grid)
    p.economic_checkpoint(t)
    assert await p.economic_store.flush()
    await p.economic_store.close()
    p.economic_store = EconomicStorage(p.hass, p.entry_id)
    await p.economic_store.load()
    p.economic_policies.pop(t)
    p.economic_identities.pop(t)
    peer.enabled = enabled
    await bound.read_enabled()
    before = len(peer.requests)
    result = await p.permission(t, True, _resume=True)
    assert result.status == CommandStatus.APPLIED  # Permission is independent.
    assert p.economic_policies[t].exhausted
    assert all(period(request)["limit"] == 0 for request in peer.requests[before:])
    assert c.runtime.enabled(t) is True


async def paused_reset(grid):
    p, t, context, clock = await prepared(grid, imported=1300)
    cohort(
        p, t, clock, 1, actual=2300, discharge=1000, imported=1300, pv=1000, load=3300
    )
    await p.hass.async_block_till_done()
    policy = p.economic_policies[t]
    capacity, _ = p.economic_capacity(t)
    assert capacity == 3400
    policy.energy_ws = LIMIT_WS
    policy.capture_restart_reference(
        capacity, p.economic_minimum(t).point.offered_power_w
    )
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    assert policy.paused
    for second in (1, 11, 17):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            pv=1000,
            load=1000,
        )
        await p.hass.async_block_till_done()
    assert policy.energy_ws == 0 and policy.paused
    # The economic adapter independently requires fresh actual OFF evidence.
    p.optimum_regulators.pop(t, None)
    return p, t, context, clock


@pytest.mark.parametrize("improvement", [0, 1, 100, 101])
async def test_fresh_off_and_noise_bound_allow_only_meaningful_model_improvement(
    grid, improvement
):
    p, t, (_, _, peer, *_), clock = await paused_reset(grid)
    cohort(
        p,
        t,
        clock,
        18,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        imported=0,
        exported=improvement,
        pv=1000 + improvement,
        load=1000,
    )
    await p.hass.async_block_till_done()
    point = p.economic_minimum(t).point
    before = len(peer.requests)
    assert p.economic_restart_permits(t, point) is (improvement > 100)
    assert len(peer.requests) == before
    if improvement <= 100:
        assert p.economic_reasons[t] == "economic_conditions_unchanged"


@pytest.mark.parametrize("bad", ["old_ev_generation", "residual_ev"])
async def test_new_site_channels_do_not_authorize_restart_without_fresh_actual_off(
    grid, bad
):
    p, t, (_, _, peer, *_), clock = await paused_reset(grid)
    off_at = p.economic_off_at[t]
    cohort(
        p,
        t,
        clock,
        18,
        source=datetime.now(UTC),
        actual=3000 if bad == "residual_ev" else 0,
        discharge=0,
        imported=0,
        exported=1000,
        pv=2000,
        load=1000,
        ev_source=off_at - timedelta(microseconds=1)
        if bad == "old_ev_generation"
        else None,
    )
    await p.hass.async_block_till_done()
    before = len(peer.requests)
    assert not p.economic_restart_permits(t, p.economic_minimum(t).point)
    assert p.economic_reasons[t] == "economic_restart_waiting_off_response"
    assert len(peer.requests) == before


async def test_missing_stop_reference_cannot_be_filled_from_later_off_measurements(
    grid,
):
    p, t, _, clock = await paused_reset(grid)
    p.economic_policies[t].restart_reference = None
    cohort(
        p,
        t,
        clock,
        18,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        imported=0,
        exported=1000,
        pv=2000,
        load=1000,
    )
    await p.hass.async_block_till_done()
    assert not p.economic_restart_permits(t, p.economic_minimum(t).point)
    assert p.economic_reasons[t] == "economic_restart_reference_unknown"


async def test_failed_durability_inhibits_positive_but_does_not_suppress_exhausted_off(
    grid, monkeypatch
):
    p, t, (c, _, peer, *_), _ = await exhausted(grid)
    monkeypatch.setattr(p.economic_store, "flush", AsyncMock(return_value=False))
    p.economic_store.failure_reason = "economic_history_write_failed"
    before = len(peer.requests)
    result = await dispatch_economic_off(p, t)
    assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0
    assert not c.confirmed_point(t).charging


async def test_exhausted_budget_selects_off_before_normal_pv_target(grid):
    p, t, (c, _, peer, *_), _ = await exhausted(grid)
    before = len(peer.requests)
    result = await evaluate(p, t)
    assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0
    assert not c.confirmed_point(t).charging
    assert p.economic_policies[t].paused


@pytest.mark.parametrize("gap", ["unavailable", "expired"])
async def test_critical_meter_gap_inhibits_preserves_budget_and_recovers_without_reset(
    grid, gap
):
    p, t, (c, _, peer, *_), clock = await prepared(grid, imported=2000)
    cohort(p, t, clock, 10, imported=2000)
    await p.hass.async_block_till_done()
    policy = p.economic_policies[t]
    assert policy.energy_ws == 20000
    state = p.hass.states.get(p.references["grid_import_power"])
    attributes = dict(state.attributes)
    if gap == "expired":
        attributes["valid_until"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    p.hass.states.async_set(
        state.entity_id,
        "unavailable" if gap == "unavailable" else state.state,
        attributes,
    )
    await p.hass.async_block_till_done()
    assert policy.energy_ws == 20000
    assert policy.below_seconds == 0
    before = len(peer.requests)
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert all(period(request)["limit"] == 0 for request in peer.requests[before:])
    assert policy.energy_ws == 20000
    # A resumed meter first establishes a new anchor. The unavailable interval
    # contributes neither charging exposure nor evidence for a budget reset.
    valid = p.hass.states.get(state.entity_id)
    fresh = dict(valid.attributes)
    fresh.pop("valid_until", None)
    fresh["observed_at"] = datetime.now(UTC).isoformat()
    clock[0] = 40
    p.hass.states.async_set(state.entity_id, 0, fresh)
    await p.hass.async_block_till_done()
    assert policy.energy_ws == 20000
    assert policy.below_seconds == 0
    cohort(
        p,
        t,
        clock,
        45,
        source=datetime.now(UTC),
        actual=0,
        imported=0,
        discharge=0,
        pv=1000,
        load=1000,
    )
    await p.hass.async_block_till_done()
    assert policy.energy_ws == 20000
    assert policy.below_seconds == 5
    assert t not in p.economic_reasons


@pytest.mark.parametrize("allocation,positive", [(1500, False), (3000, True)])
async def test_startup_floor_preserves_response_reserve_and_battery_support(
    grid, allocation, positive
):
    p, t, (c, _, peer, *_), clock = await prepared(grid)
    await p.permission(t, False)
    p.setting(t)["optimum_max_discharge_w"] = allocation
    cohort(
        p,
        t,
        clock,
        1,
        source=datetime.now(UTC),
        actual=0,
        imported=0,
        discharge=0,
        pv=1000,
        load=1000,
    )
    before = len(peer.requests)
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert bool(c.confirmed_point(t) and c.confirmed_point(t).charging) is positive
    if positive:
        # PV generation only covers the household. The normal FAST battery
        # allocation legitimately supports this startup, without a PV-only gate.
        assert c.confirmed_point(t).current_a == 6
        assert any(period(request)["limit"] > 0 for request in peer.requests[before:])
    else:
        assert p.status[t] == "startup_response_reserve"
        assert all(period(request)["limit"] == 0 for request in peer.requests[before:])
        assert p.economic_policies[t].energy_ws == 0


@pytest.mark.parametrize("phases,imported", [(1, 2000), (3, 3000)])
async def test_delayed_off_ack_keeps_active_exposure_and_physical_threshold(
    grid, phases, imported
):
    p, t, (c, _, peer, *_), clock = await prepared(
        grid, phases=phases, imported=imported
    )
    if phases == 3:
        lock_current_phase(c, t)
    policy = p.economic_policies[t]
    policy.energy_ws = LIMIT_WS - imported * 5
    cohort(p, t, clock, 5, actual=2300 * phases, imported=imported)
    await p.hass.async_block_till_done()
    assert policy.exhausted
    assert policy.threshold_w == 690 * phases
    peer.received.clear()
    peer.release.clear()
    task = asyncio.create_task(evaluate(p, t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
        assert policy.off_pending and policy.active and not policy.paused
        cohort(p, t, clock, 10, actual=2300 * phases, imported=imported)
        await p.hass.async_block_till_done()
        assert policy.energy_ws == LIMIT_WS + imported * 5
        assert policy.threshold_w == 690 * phases
        peer.release.set()
        assert (await asyncio.wait_for(task, 2)).status == CommandStatus.APPLIED
        assert policy.paused and not policy.active
        proved = policy.energy_ws
        cohort(p, t, clock, 15, actual=0, imported=imported)
        await p.hass.async_block_till_done()
        assert policy.energy_ws == proved  # Household import after known OFF.
        assert not c.confirmed_point(t).charging
    finally:
        peer.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_busy_exhausted_off_retries_without_phase_threshold_or_budget_rearming(
    grid,
):
    p, t, (c, _, peer, *_), clock = await prepared(grid, phases=3, imported=3000)
    lock_current_phase(c, t)
    policy = p.economic_policies[t]
    p.economic_observe(t)
    policy.energy_ws = LIMIT_WS
    peer.profile_response = lambda _: call_result.SetChargingProfile(
        status="Rejected", status_info={"reason_code": "Busy"}
    )
    result = await evaluate(p, t)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert policy.exhausted and policy.active and policy.off_pending
    assert policy.threshold_w == 2070
    assert c.phase_restricted(t)
    count = len(peer.requests)
    cohort(p, t, clock, 60, source=datetime.now(UTC), actual=6900, imported=3000)
    await p.hass.async_block_till_done()
    assert policy.energy_ws == LIMIT_WS  # Unknown gap is not partially backfilled.
    assert policy.threshold_w == 2070
    peer.profile_response = None
    result = await evaluate(p, t)
    assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == count + 1
    assert all(period(request)["limit"] == 0 for request in peer.requests[count:])
    assert policy.exhausted and policy.paused
    assert policy.threshold_w == 2070 and c.phase_restricted(t)


async def test_unchanged_conditions_cannot_purchase_repeated_50wh_charging_cycles(grid):
    p, t, (c, _, peer, *_), clock = await paused_reset(grid)
    before = len(peer.requests)
    reference = p.economic_policies[t].restart_reference
    for second in (18, 20, 26, 32):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            pv=1000,
            load=1000,
        )
        await p.hass.async_block_till_done()
        await evaluate(p, t)
        assert not c.confirmed_point(t).charging
        assert p.economic_policies[t].restart_reference == reference
        assert p.economic_policies[t].energy_ws == 0
    assert len(peer.requests) == before
    assert p.status[t] == "economic_restart_inhibited"


async def test_meaningfully_improved_model_allows_battery_assisted_automatic_restart(
    grid,
):
    p, t, (c, _, peer, *_), clock = await paused_reset(grid)
    before = len(peer.requests)
    for second in (18, 19):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            exported=1001,
            pv=2001,
            load=1000,
        )
        await p.hass.async_block_till_done()
        await evaluate(p, t)
    assert c.confirmed_point(t).charging
    # The available PV-only surplus is1001W, below1p6. Intended FAST battery
    # support remains valid and the model improvement exceeds noise/point cost.
    assert c.confirmed_point(t).offered_power_w > 1001
    assert any(period(request)["limit"] > 0 for request in peer.requests[before:])
    assert p.economic_policies[t].restart_reference is None
    assert p.economic_policies[t].active and not p.economic_policies[t].paused
    assert not peer.authority_requests


async def test_elapsed_phase_probe_does_not_buy_cheaper_economic_restart_minimum(grid):
    p, t, (c, _, peer, *_), clock = await prepared(
        grid, phases=3, current=6, imported=3140
    )
    lock_current_phase(c, t)
    cohort(
        p,
        t,
        clock,
        1,
        actual=4140,
        discharge=1000,
        imported=3140,
        pv=1000,
        load=5140,
    )
    await p.hass.async_block_till_done()
    policy = p.economic_policies[t]
    capacity, _ = p.economic_capacity(t)
    assert capacity == 3400
    policy.energy_ws = LIMIT_WS
    policy.capture_restart_reference(
        capacity, p.economic_minimum(t).point.offered_power_w
    )
    assert policy.restart_reference.minimum_w == 4140
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    for second in (1, 11, 17):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            pv=1000,
            load=1000,
        )
        await p.hass.async_block_till_done()
    assert policy.energy_ws == 0 and policy.paused
    p.optimum_regulators.pop(t, None)
    p.pv_retry_until[t] = 60
    p.pv_phase_retry[t] = True
    clock[0] = 60
    count = len(peer.requests)
    with c.phase_probe(t, enabled=True):
        probe_candidate = c.technical_minimum(t).point
        assert probe_candidate.mode.count == 1
        assert p.economic_minimum(t).point.offered_power_w == 4140
        assert not p.economic_restart_permits(t, probe_candidate)
    assert policy.threshold_w == 2070
    assert c.phase_restricted(t)
    assert len(peer.requests) == count
    # Mere probe time earned no cheaper reference cost above. Real coherent
    # capacity improvement may authorize that existing probe while M/T still
    # use the currently proved3p domain.
    cohort(
        p,
        t,
        clock,
        61,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        imported=0,
        exported=101,
        pv=1101,
        load=1000,
    )
    await p.hass.async_block_till_done()
    with c.phase_probe(t, enabled=True):
        assert p.economic_restart_permits(t, probe_candidate)
        assert p.economic_minimum(t).point.offered_power_w == 4140
    assert policy.threshold_w == 2070
    assert c.phase_restricted(t)
    assert len(peer.requests) == count


async def pending_restart(grid, *, busy):
    p, t, context, clock = await paused_reset(grid)
    _, _, peer, *_ = context
    for second in (18, 19):
        cohort(
            p,
            t,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            exported=1001,
            pv=2001,
            load=1000,
        )
        await p.hass.async_block_till_done()
        if second == 18:
            await evaluate(p, t)
    peer.received.clear()
    peer.release.clear()
    if busy:
        peer.profile_response = lambda _: call_result.SetChargingProfile(
            status="Rejected", status_info={"reason_code": "Busy"}
        )
    pending = asyncio.create_task(evaluate(p, t))
    try:
        await asyncio.wait_for(peer.received.wait(), 2)
    except BaseException:
        peer.release.set()
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        raise
    assert t in p.economic_positive_dispatches
    return p, t, context, clock, pending


@pytest.mark.parametrize("residual", [1, 100])
async def test_residual_ev_before_busy_reply_preserves_economic_pause(grid, residual):
    p, t, (c, _, peer, *_), clock, pending = await pending_restart(grid, busy=True)
    policy = p.economic_policies[t]
    reference = policy.restart_reference
    assert policy.restart_required and not policy.active
    try:
        cohort(
            p,
            t,
            clock,
            20,
            source=datetime.now(UTC),
            actual=residual,
            discharge=0,
            imported=0,
            exported=1001,
            pv=2001,
            load=1000,
        )
        await p.hass.async_block_till_done()
        assert policy.restart_required and policy.restart_reference is reference
        assert not policy.active
        peer.release.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.status == CommandStatus.TEMPORARILY_REJECTED
        assert t not in p.economic_positive_dispatches
        assert policy.restart_required and policy.restart_reference is reference
        assert not c.confirmed_point(t).charging
        # Reverting to the original coherent capacity cannot grant another cycle.
        cohort(
            p,
            t,
            clock,
            21,
            source=datetime.now(UTC),
            actual=0,
            discharge=0,
            imported=0,
            pv=1000,
            load=1000,
        )
        await p.hass.async_block_till_done()
        p.optimum_regulators.pop(t, None)
        assert not p.economic_restart_permits(t, p.economic_minimum(t).point)
        assert p.economic_reasons[t] == "economic_conditions_unchanged"
    finally:
        peer.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("edit", ["lifecycle", "direct_control"])
async def test_edit_revokes_a_pending_positive_response_marker(grid, edit):
    p, t, (c, _, peer, *_), clock, pending = await pending_restart(grid, busy=False)
    policy = p.economic_policies[t]
    reference = policy.restart_reference
    try:
        if edit == "lifecycle":
            p.invalidate(t)
            assert t not in p.economic_positive_dispatches
        else:
            # Public control.change uses this edit boundary without calling
            # profiles.invalidate. The old wire response is no longer current.
            c._edit(t, {"allowed_current_1p": 16})
        cohort(
            p,
            t,
            clock,
            20,
            source=datetime.now(UTC),
            actual=2300,
            discharge=1000,
            imported=1300,
            pv=1000,
            load=3300,
        )
        await p.hass.async_block_till_done()
        assert t not in p.economic_positive_dispatches
        assert policy.restart_required and policy.restart_reference is reference
        assert not policy.active
        peer.release.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.status == CommandStatus.TEMPORARILY_REJECTED
        assert t not in p.economic_positive_dispatches
        assert policy.restart_required and policy.restart_reference is reference
        assert t in c._unconfirmed_targets
    finally:
        peer.release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
