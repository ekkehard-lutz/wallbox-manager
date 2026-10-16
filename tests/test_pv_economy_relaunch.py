"""Observation-only events preserve economic history until the next control cycle."""

import asyncio
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_ocpp21_control import transaction
from test_pv_beta12 import authority
from test_pv_economy_control import cohort, prepared
from test_pv_optimum_hold import evaluate, period
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import power_measurement
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import ControlAuthority
from custom_components.wallbox_manager.pv_economy import ECONOMIC_REASON


async def observation_fixture(grid, *, imported=800, current=10):
    """Park automatic power control; accounting still consumes real HA reports."""
    p, target, context, clock = await prepared(grid, current=current, imported=imported)
    p.tasks[target] = asyncio.create_task(asyncio.Event().wait())
    return p, target, context, clock, (p, target)


async def control_cycle(context, second):
    p, target = context
    assert p.monotonic() >= second
    await evaluate(p, target)


async def publish(p, target, clock, second, **values):
    cohort(p, target, clock, second, **values)
    await p.hass.async_block_till_done()


def zero_cohort_without_pv(p, target, clock, second, *, exported=0):
    """Valid post-OFF acquisition; leave the unavailable PV source untouched."""
    clock[0] = second
    source = datetime.now(UTC).isoformat()
    power_measurement(p, target, 0, at=source)
    for entity, value in (
        ("sensor.selected", 0),
        (p.references["leistung_verbraucher"], 1000),
        (p.references["storage_discharge_power"], 0),
        (p.references["grid_export_power"], exported),
        (p.references["grid_import_power"], 0),
    ):
        state = p.hass.states.get(entity)
        p.hass.states.async_set(
            entity, value, {**state.attributes, "observed_at": source}
        )


def pv_gap(p, target):
    state = p.hass.states.get(p.references["leistung_pv"])
    p.hass.states.async_set(state.entity_id, "unavailable", state.attributes)
    plan = p.pv_edit(target)
    assert plan is not None and not plan.point.charging
    assert p.status[target] == "measurements_unavailable"
    assert not p.pv_ongoing[target]


async def recover_pv(p, target, parked, *, pv=4000, source=None):
    """Recovery events leave the producer/intent untouched until a control cycle."""
    old_task, epoch = p.tasks[target], p.epochs.get(target, 0)
    generation = p.control.intent(target).generation
    state = p.hass.states.get(p.references["leistung_pv"])
    p.hass.states.async_set(
        state.entity_id,
        pv,
        {**state.attributes, "observed_at": (source or datetime.now(UTC)).isoformat()},
    )
    await p.hass.async_block_till_done()
    assert p.epochs.get(target, 0) == epoch
    assert p.control.intent(target).generation == generation
    assert p.tasks[target] is old_task
    await control_cycle(parked, p.monotonic())


async def test_import_energy_crosses_clocked_input_recovery_without_lost_interval(
    grid,
):
    p, target, (c, _, peer, *_), clock, parked = await observation_fixture(grid)
    await publish(p, target, clock, 5, imported=800)
    policy = p.economic_policies[target]
    identity = p.economic_identities[target]
    assert policy.energy_ws == 4000
    assert policy.active and c.confirmed_point(target).charging
    count = len(peer.requests)
    # A critical-input OFF is prepared while the last positive point remains
    # physically confirmed. New PV arrives before that OFF can be dispatched.
    pv_gap(p, target)
    await recover_pv(p, target, parked)
    assert p.economic_policies[target] is policy
    assert p.economic_identities[target] == identity
    await publish(p, target, clock, 10, imported=800)
    await control_cycle(parked, 10)
    assert policy.energy_ws == 8000
    assert policy.coverage_reason is None
    assert all(period(request)["limit"] > 0 for request in peer.requests[count:])
    assert not policy.exhausted and not policy.off_pending


async def test_causal_voltage_threshold_event_survives_clocked_input_recovery(grid):
    p, target, (c, _, peer, *_), clock, parked = await observation_fixture(grid)
    await publish(p, target, clock, 5, imported=650)
    policy = p.economic_policies[target]
    assert policy.energy_ws == 4000 and policy.threshold_w == 690
    clock[0] = 7
    voltage(c, target, 200)
    assert policy.threshold_w == 600
    clock[0] = 8
    count = len(peer.requests)
    pv_gap(p, target)
    await recover_pv(p, target, parked)
    await publish(p, target, clock, 10, imported=650)
    await control_cycle(parked, 10)
    # The held 650 W sample is below T during 5..7, then counts entirely during
    # 7..10. An input recovery at t=8 cannot discard either causal segment.
    assert policy.energy_ws == 4000 + 650 * 3
    assert policy.threshold_w == 600 and policy.below_seconds == 0
    assert all(period(request)["limit"] > 0 for request in peer.requests[count:])


async def test_below_threshold_reset_continuity_survives_protective_off_recovery(grid):
    p, target, (c, _, peer, *_), clock, parked = await observation_fixture(
        grid, imported=2000
    )
    await publish(p, target, clock, 5, imported=2000)
    p.pv_request(target)  # Establish response state before the real protective OFF.
    policy = p.economic_policies[target]
    assert policy.energy_ws == 10000
    pv_gap(p, target)
    assert (
        await c.apply_stored(target, reuse_applied=True)
    ).status == CommandStatus.APPLIED
    p.pv_confirm(target)
    assert not c.confirmed_point(target).charging
    assert not policy.active and not policy.restart_required
    for second in (5, 10, 15):
        zero_cohort_without_pv(p, target, clock, second)
        await p.hass.async_block_till_done()
    assert policy.below_seconds == 10
    count = len(peer.requests)
    await recover_pv(p, target, parked)
    assert policy.below_seconds == 10
    assert policy.energy_ws == 10000
    assert c.confirmed_point(target).charging
    assert len(peer.requests) == count + 1
    for second in (20, Fraction("20.001")):
        await publish(
            p,
            target,
            clock,
            second,
            source=datetime.now(UTC),
            actual=1610,
            imported=0,
            exported=1390,
            discharge=0,
            pv=4000,
            load=2610,
        )
        await control_cycle(parked, second)
        assert policy.below_seconds == second - 5
        assert policy.energy_ws == (10000 if second == 20 else 0)
    assert not policy.off_pending and not policy.restart_required


@pytest.mark.parametrize("delayed_relaunch", [False, True])
async def test_confirmed_economic_stop_keeps_cause_reference_after_meter_reset(
    grid, delayed_relaunch
):
    p, target, (c, _, peer, *_), clock, parked = await observation_fixture(
        grid, imported=1300
    )
    policy = p.economic_policies[target]
    # Close real supported meter intervals rather than installing an exhausted
    # policy. Source acquisitions stay fresh; acceptance uses the explicit clock.
    for second in range(0, 141, 10):
        await publish(
            p,
            target,
            clock,
            second,
            source=datetime.now(UTC),
            actual=2300,
            imported=1300,
            discharge=1000,
            pv=1000,
            load=3300,
        )
    assert policy.energy_ws == 182000 and policy.exhausted
    assert policy.restart_reference is not None
    reference = policy.restart_reference
    assert (await evaluate(p, target)).status == CommandStatus.APPLIED
    assert policy.paused and not policy.active
    assert not c.confirmed_point(target).charging
    assert policy.pause_reason == ECONOMIC_REASON and policy.restart_required
    count = len(peer.requests)
    for second in (140, 150, 155, Fraction("155.001")):
        await publish(
            p,
            target,
            clock,
            second,
            source=datetime.now(UTC),
            actual=0,
            imported=0,
            discharge=0,
            pv=1000,
            load=1000,
        )
    assert policy.energy_ws == 0 and policy.below_seconds == Fraction("15.001")
    assert policy.restart_reference is reference
    assert policy.paused and policy.restart_required
    assert policy.pause_reason == ECONOMIC_REASON
    if delayed_relaunch:
        # BALANCE becomes normally eligible with its configured start delay.
        # That existing delay precedes the economic model comparison, so the
        # next control cycle prepares a delayed start without granting restart.
        p.setting(target)["pv_start_delay"] = 30
        pv_gap(p, target)
        p.hass.states.async_set("sensor.soc", 81, {"unit_of_measurement": "%"})
        zero_cohort_without_pv(p, target, clock, Fraction("155.001"), exported=3000)
        await recover_pv(p, target, parked, pv=4000)
        assert p.status[target] == "pv_start_delay"
        assert policy.paused and policy.restart_required
        assert policy.pause_reason == ECONOMIC_REASON
        assert policy.restart_reference is reference and policy.energy_ws == 0
        assert len(peer.requests) == count
        await publish(
            p,
            target,
            clock,
            Fraction("185.001"),
            source=datetime.now(UTC),
            actual=0,
            imported=0,
            exported=3000,
            discharge=0,
            pv=4000,
            load=1000,
        )
        await control_cycle(parked, Fraction("185.001"))
        assert p.status[target] == "economic_restart_inhibited"
    else:
        # An ordinary callback must not bypass the unchanged-condition gate.
        pv_gap(p, target)
        epoch = p.epochs.get(target, 0)
        state = p.hass.states.get(p.references["leistung_pv"])
        p.hass.states.async_set(
            state.entity_id,
            1000,
            {**state.attributes, "observed_at": datetime.now(UTC).isoformat()},
        )
        await p.hass.async_block_till_done()
        assert p.epochs.get(target, 0) == epoch
        await control_cycle(parked, p.monotonic())
    assert not p.economic_restart_permits(target, p.economic_minimum(target).point)
    assert p.economic_reasons[target] == "economic_conditions_unchanged"
    assert p.economic_policies[target] is policy
    assert policy.restart_reference is reference and policy.restart_required
    assert policy.pause_reason == ECONOMIC_REASON
    assert not c.confirmed_point(target).charging and len(peer.requests) == count


@pytest.mark.parametrize(
    "gap", ["missing", "invalid", "source_gap", "acceptance_gap", "duplicate"]
)
async def test_real_meter_coverage_gaps_and_duplicates_preserve_proved_energy(
    grid, gap
):
    p, target, (_, _, peer, *_), clock, parked = await observation_fixture(grid)
    await publish(p, target, clock, 5, imported=800)
    policy = p.economic_policies[target]
    pv_gap(p, target)
    await recover_pv(p, target, parked)
    entity = p.references["grid_import_power"]
    state = p.hass.states.get(entity)
    clock[0] = 10
    if gap == "missing":
        p.hass.states.async_remove(entity)
    elif gap == "invalid":
        p.hass.states.async_set(entity, "unavailable", state.attributes)
    elif gap == "source_gap":
        p.hass.states.async_set(
            entity,
            800,
            {**state.attributes, "observed_at": datetime.now(UTC).isoformat()},
        )
    elif gap == "acceptance_gap":
        clock[0] = 16  # Eleven seconds beyond the last accepted sample at t=5.
        p.hass.states.async_set(
            entity,
            800,
            {
                **state.attributes,
                "observed_at": (
                    p._economic_test_source + timedelta(seconds=10)
                ).isoformat(),
            },
        )
    else:
        # Same acquisition generation, including repeated actual HA reports.
        for _ in range(3):
            p.hass.states.async_set(entity, state.state, state.attributes)
    await p.hass.async_block_till_done()
    assert policy.energy_ws == 4000 and policy.below_seconds == 0
    if gap == "duplicate":
        await publish(p, target, clock, 10, imported=800)
        await control_cycle(parked, 10)
        assert policy.energy_ws == 8000
    elif gap in ("missing", "invalid"):
        assert policy.coverage_reason is not None
    else:
        # A supported latest value establishes a new anchor after the unknown
        # interval; it cannot backfill energy for that acquisition/receipt gap.
        assert policy.last_import_w == 800
        next_second = 15 if gap == "source_gap" else 21
        next_source = (
            datetime.now(UTC)
            if gap == "source_gap"
            else p._economic_test_source + timedelta(seconds=15)
        )
        await publish(p, target, clock, next_second, imported=800, source=next_source)
        await control_cycle(parked, next_second)
        assert policy.energy_ws == 8000
    assert not policy.exhausted and not policy.off_pending
    assert not peer.authority_requests


@pytest.mark.parametrize("gap", ["invalid", "acceptance_gap", "duplicate"])
async def test_reset_proof_breaks_on_real_gap_but_not_duplicate_reports(grid, gap):
    p, target, _, clock, parked = await observation_fixture(grid)
    await publish(p, target, clock, 5, imported=800)
    policy = p.economic_policies[target]
    pv_gap(p, target)
    await recover_pv(p, target, parked)
    for second in (6, 11):
        await publish(p, target, clock, second, imported=0)
        await control_cycle(parked, second)
    assert policy.energy_ws == 4800 and policy.below_seconds == 5
    entity = p.references["grid_import_power"]
    state = p.hass.states.get(entity)
    if gap == "invalid":
        clock[0] = 12
        p.hass.states.async_set(entity, "unavailable", state.attributes)
        await p.hass.async_block_till_done()
        assert policy.energy_ws == 4800 and policy.below_seconds == 0
        await publish(p, target, clock, 16, imported=0)
        assert policy.below_seconds == 0
    elif gap == "acceptance_gap":
        await publish(
            p,
            target,
            clock,
            22,
            imported=0,
            source=p._economic_test_source + timedelta(seconds=16),
        )
        await control_cycle(parked, 22)
        assert policy.energy_ws == 4800 and policy.below_seconds == 0
    else:
        clock[0] = 16
        for _ in range(3):
            p.hass.states.async_set(entity, state.state, state.attributes)
        await p.hass.async_block_till_done()
        assert policy.energy_ws == 4800 and policy.below_seconds == 5
        await publish(p, target, clock, 21, imported=0)
        await control_cycle(parked, 21)
        assert policy.energy_ws == 4800 and policy.below_seconds == 15
        await publish(p, target, clock, Fraction("21.001"), imported=0)
        await control_cycle(parked, Fraction("21.001"))
        assert policy.energy_ws == 0


@pytest.mark.parametrize("boundary", ["off", "ended", "authority", "connection"])
async def test_real_lifecycle_boundary_differs_from_clocked_input_recovery(
    grid, boundary
):
    p, target, (c, bound, peer, *_), clock, parked = await observation_fixture(grid)
    await publish(p, target, clock, 5, imported=800)
    policy = p.economic_policies[target]
    pv_gap(p, target)
    await recover_pv(p, target, parked)
    assert policy.energy_ws == 4000
    if boundary == "off":
        await p.permission(target, False)
    elif boundary == "ended":
        identity = c.runtime.sessions.get(target).external_transaction_id
        await transaction(
            peer, identity=identity, connector=1, kind="Ended", at=datetime.now(UTC)
        )
        await p.hass.async_block_till_done()
        if p.economic_lifecycle_tasks:
            await asyncio.gather(*tuple(p.economic_lifecycle_tasks))
    elif boundary == "authority":
        authority(c, target, ControlAuthority.LOCAL)
    else:
        c.runtime.disconnect(bound.token)
    if boundary in ("off", "ended"):
        assert target not in p.economic_policies
        assert target not in p.economic_identities
    else:
        assert p.economic_policies[target] is policy
        assert policy.energy_ws == 4000
        assert policy.below_seconds == 0 and policy.coverage_reason is not None
        assert not p.can_control(target)
    assert not peer.authority_requests
