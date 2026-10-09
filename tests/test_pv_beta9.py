"""Beta.9 budgets, asynchronous evidence, import exceptions and stop continuity."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_power_solver import run as run
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements
from test_pv_transient_gaps import running

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.pv_budget import PowerEvidence, battery_budget
from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator
from custom_components.wallbox_manager.solver.operating_point import OperatingPoint

START = datetime(2026, 10, 9, tzinfo=UTC)


def point(amps, phases=1):
    if not amps:
        return OperatingPoint.off()
    mode = PhaseMode.canonical(phases)
    return OperatingPoint(True, mode, amps, amps * phases * 230, (230,) * phases)


def evidence(ev_time, site_time=None, *, pv=0, load=3000):
    site_time = ev_time if site_time is None else site_time
    return PowerEvidence(
        (START + timedelta(seconds=ev_time),)
        + (START + timedelta(seconds=site_time),) * 5,
        Fraction(pv),
        Fraction(load),
    )


@pytest.mark.parametrize("direction", list(Direction))
@pytest.mark.parametrize("fallback", [False, True])
def test_solver_hard_limit_applies_to_every_direction_and_floor(
    run, direction, fallback
):
    for watts in (0, 1000, 1379, 1380, 1800, 3000, 4139, 4140):
        result = run(8000, direction, minimum_positive=fallback, hard_max_w=watts)
        if result.point:
            assert result.point.offered_power_w <= watts
        if watts < 1380:
            assert result.point == OperatingPoint.off()


def test_no_three_phase_fallback_above_budget(run, three):
    result = run(1000, eligible_modes=(three,), minimum_positive=True, hard_max_w=3500)
    assert not result.point.charging


def test_budget_has_no_minimum_import_bonus():
    # EV=2300, household=1100; D=3400. Only 100 W headroom, reserved for uncertainty.
    e = evidence(0, load=3400)
    assert battery_budget(2300, 3400, 3500, 0, 0, evidence=e, ev_floor=2300) == 2300
    # Independent battery bound cannot grow when an import allowance is requested.
    assert battery_budget(2300, 3400, 3500, 1500, 0, evidence=e, ev_floor=2300) == 2300
    assert battery_budget(2300, 5000, 3500, 0, 0, evidence=e, ev_floor=2300) == 700


def test_unchanged_measurements_cannot_ramp_and_new_pair_can():
    r = FastDischargeRegulator()
    first = r.request(
        2300, 2800, 3500, 0, 0, now=0, interval=5, evidence=evidence(0, load=2800)
    )
    for tick in range(1, 61):
        assert (
            r.request(
                2300,
                2800,
                3500,
                0,
                0,
                now=tick,
                interval=5,
                evidence=evidence(0, load=2800),
            )
            == first
        )
    assert r.revision == 1
    assert (
        r.request(
            2300,
            1800,
            3500,
            0,
            0,
            now=65,
            interval=5,
            evidence=evidence(65, pv=1000, load=1800),
        )
        > first
    )


def test_ack_is_not_physical_response_but_safety_bypasses_wait():
    r = FastDischargeRegulator()
    r.request(
        1380,
        2000,
        3500,
        0,
        0,
        now=0,
        interval=5,
        evidence=evidence(0, load=2000),
        confirmed=point(6),
    )
    initial = r.requested
    for tick in (5, 10, 15):
        assert (
            r.request(
                1380,
                2000,
                3500,
                0,
                0,
                now=tick,
                interval=5,
                evidence=evidence(tick, load=2000),
                confirmed=point(10),
            )
            <= initial
        )
    reduced = r.request(
        1380,
        4500,
        3500,
        0,
        0,
        now=16,
        interval=5,
        evidence=evidence(15, 16, load=4500),
        confirmed=point(10),
    )
    assert reduced <= 280
    assert r.hard_max == 280


def test_new_ev_with_old_site_cannot_spend_headroom_twice():
    r = FastDischargeRegulator()
    r.request(
        1380, 2000, 3500, 0, 0, now=0, interval=5, evidence=evidence(0, load=2000)
    )
    cap = r.hard_max
    r.request(
        2300, 2000, 3500, 0, 0, now=10, interval=5, evidence=evidence(10, 0, load=2000)
    )
    assert r.hard_max == cap


@pytest.mark.parametrize("phases", [1, 3])
def test_minimum_exception_uses_site_import_and_bounded_confirmation(phases):
    r = FastDischargeRegulator()
    minimum = point(6, phases)
    watts = minimum.offered_power_w
    r.evidence = evidence(0)
    r.hard_max = watts
    r.net_import = watts / 2
    for t in (0, 10, 60):
        assert r.minimum_allowed(minimum, now=t, actual=watts)
    r.net_import += 1
    assert r.minimum_allowed(minimum, now=61, actual=watts)
    for t in range(62, 71):
        assert r.minimum_allowed(minimum, now=t, actual=watts)
    # Reused evidence cannot extend the bounded grace deadline.
    assert not r.minimum_allowed(minimum, now=71, actual=watts)
    r.net_import = watts / 2 - 50
    assert not r.minimum_allowed(minimum, now=72, actual=watts)
    r.net_import = watts / 2 - 100
    assert r.minimum_allowed(minimum, now=73, actual=watts)
    r.hard_max = watts - 1
    assert not r.minimum_allowed(minimum, now=74, actual=watts)


@pytest.mark.parametrize("gaps", [[20], [20, 40, 80], [89], [89, 91, 110]])
async def test_stop_deadline_survives_gaps_and_expires_without_fresh_sample(grid, gaps):
    p, target, (c, _, peer, *_), clock, tick = await running(grid)
    measurements(p, target, pv=0, load=0, actual=0, soc=96)
    await tick()
    assert p.pv_stop_since[target] == 0
    count = len(peer.requests)
    for t in gaps:
        clock[0] = t
        p.hass.states.async_set("sensor.pv", "unknown")
        await tick()
        if t < 90:
            assert p.pv_stop_since[target] == 0
            assert c.confirmed_point(target).charging
        else:
            assert not c.confirmed_point(target).charging
    clock[0] = max(90, gaps[-1])
    await tick()
    assert not c.confirmed_point(target).charging
    assert len(peer.requests) == count + 1
    await tick()
    assert len(peer.requests) == count + 1


async def test_wire_dedup_voltage_then_transaction_and_rejection(manual):
    from test_ocpp21_control import transaction
    from test_voltage_command_fence import voltage

    from custom_components.wallbox_manager.control.commands import CommandStatus

    c, bound, peer, _, source, _ = manual
    t = bound.target
    source.mode = PhaseMode.canonical(1)
    await c.change(t, target_w=2300, allowed=True, direction=Direction.DOWN)
    assert c.confirmed_point(t).current_a == 10
    count = len(peer.requests)
    for volts in (229, 228, 230):
        voltage(c, t, volts)
        c._edit(t, {"target_w": Fraction(volts * 10)})
        result = await c.apply_stored(t, reuse_applied=True)
        assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == count
    await transaction(peer, identity="replacement", connector=1)
    result = await c.apply_stored(t, reuse_applied=True)
    assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == count + 1
    peer.status = "Rejected"
    c._edit(t, {"target_w": Fraction(2530)})
    for _ in range(2):
        result = await c.apply_stored(t, reuse_applied=True)
        assert result.status == CommandStatus.TEMPORARILY_REJECTED
    assert len(peer.requests) == count + 3
    peer.status = "Accepted"
    await c.apply_stored(t, reuse_applied=True)
    assert len(peer.requests) == count + 4
    assert c.confirmed_point(t).current_a == 11


@pytest.mark.parametrize("violation", ["import", "battery"])
async def test_locked_three_phase_floor_pauses_on_excess(grid, violation):
    from test_pv_optimum_hold import evaluate, phase_start, samples

    p, t, (c, _, peer, *_), clock, _ = await phase_start(grid)
    samples(p, t, actual=4140, discharge=3200, imported=1800)
    await evaluate(p, t)
    assert c.phase_restricted(t)
    assert c.confirmed_point(t).mode.count == 3
    assert c.confirmed_point(t).current_a == 6
    before = len(peer.requests)
    clock[0] = 30
    samples(
        p,
        t,
        actual=4140,
        discharge=4000 if violation == "battery" else 3200,
        imported=2100 if violation == "import" else 1800,
    )
    await evaluate(p, t)
    if violation == "import":
        assert c.confirmed_point(t).charging
        clock[0] = 40
        await evaluate(p, t)
    assert not c.confirmed_point(t).charging
    assert len(peer.requests) == before + 1
    await evaluate(p, t)
    assert len(peer.requests) == before + 1


async def test_lockout_release_reconsiders_one_phase(grid):
    from test_pv_optimum_hold import evaluate, phase_start, samples

    p, t, (c, _, peer, _, source, _), clock, locked = await phase_start(grid)
    samples(p, t, actual=4140, discharge=3200, imported=1800)
    await evaluate(p, t)
    assert c.confirmed_point(t).mode.count == 3
    clock[0] = 60
    samples(p, t, actual=4140, discharge=3200, imported=1800)
    locked[0] = False
    with c.phase_probe(t, enabled=True):
        await evaluate(p, t)
    assert c.confirmed_point(t).mode.count == 1
    assert c.confirmed_point(t).current_a == 10
    assert not c.phase_restricted(t)
    source.mode = PhaseMode.canonical(1)
    assert c.minimum_positive(t).point.offered_power_w == 1380


@pytest.mark.parametrize("change", ["limit", "missing", "source_stale"])
async def test_live_hard_budget_fences_a_queued_positive_command(grid, change):
    import asyncio

    from test_pv_optimum_hold import configure, samples

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, bound, peer, *_), clock = configure(grid)
    samples(p, t, actual=2300, discharge=3400)
    await p.permission(t, True)
    p.pv_edit(t)
    before = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(c.apply_stored(t))
        await asyncio.sleep(0)
        if change == "limit":
            p.setting(t)["optimum_max_discharge_w"] = 500
        elif change == "missing":
            p.hass.states.async_remove("sensor.storage_discharge_power")
        else:
            state = p.hass.states.get("sensor.selected")
            p.hass.states.async_set(
                "sensor.selected",
                state.state,
                {
                    **state.attributes,
                    "observed_at": (
                        datetime.now(UTC) - timedelta(seconds=91)
                    ).isoformat(),
                },
            )
    result = await pending
    assert result.status != CommandStatus.APPLIED
    assert len(peer.requests) == before


async def test_rejected_equivalent_attempt_is_retried_not_reused(manual):
    from custom_components.wallbox_manager.control.commands import CommandStatus

    c, bound, peer, _, source, _ = manual
    source.mode = PhaseMode.canonical(1)
    await c.change(bound.target, target_w=2300, allowed=True, direction=Direction.DOWN)
    count = len(peer.requests)
    peer.status = "Rejected"
    result = await c.apply_stored(bound.target, reuse_applied=False)
    assert result.status == CommandStatus.TEMPORARILY_REJECTED
    peer.status = "Accepted"
    result = await c.apply_stored(bound.target, reuse_applied=True)
    assert result.status == CommandStatus.APPLIED
    assert len(peer.requests) == count + 2


def test_import_correction_does_not_wait_for_long_upward_interval():
    r = FastDischargeRegulator()
    r.request(
        2300, 2800, 3500, 0, 0, now=0, interval=300, evidence=evidence(0, load=2800)
    )
    power = r.request(
        2300, 2800, 3500, 500, 0, now=10, interval=300, evidence=evidence(10, load=3300)
    )
    assert power == 1800


async def test_voltage_gap_cannot_hold_offer_above_hard_budget(grid):
    from test_pv_optimum_hold import configure, evaluate, samples
    from test_voltage_command_fence import voltage

    p, t, (c, _, _, *_), _ = configure(grid)
    samples(p, t, actual=2300, discharge=3400)
    await p.permission(t, True)
    assert c.confirmed_point(t).current_a == 10
    samples(p, t, actual=2300, discharge=5000)
    voltage(c, t, 230, invalid="expired")
    await evaluate(p, t)
    assert not c.confirmed_point(t).charging


def test_minimum_start_cannot_bypass_unobserved_response_reserve():
    r = FastDischargeRegulator()
    r.evidence = evidence(0)
    r.hard_max = 1900
    r.net_import = 0
    for second in (0, 10, 30, 90):
        assert not r.minimum_allowed(point(6), now=second, actual=0)
        assert r.reason == "minimum_response_budget"
    # An actually measured continuing minimum remains permissible.
    assert r.minimum_allowed(point(6), now=100, actual=1380)
    r.hard_max = 2760
    assert r.minimum_allowed(point(6), now=110, actual=0)


def test_source_expiry_schedules_pause_even_if_ha_keeps_reporting():
    from types import SimpleNamespace

    from custom_components.wallbox_manager.pv_surplus import power_valid_for, reading

    now = datetime.now(UTC)
    state = SimpleNamespace(
        state="1380",
        last_updated=now,
        last_reported=now,
        attributes={
            "unit_of_measurement": "W",
            "observed_at": (now - timedelta(seconds=80)).isoformat(),
        },
    )
    assert reading(state, now) == 1380
    assert power_valid_for(state, now) == 10
    with pytest.raises(ValueError, match="stale source"):
        reading(state, now + timedelta(seconds=11))


async def test_balance_wait_is_bounded_by_battery_source_expiry(grid):
    from test_pv_optimum import setup_optimum

    p, t, _ = grid
    setup_optimum(p, t, soc=80.5)
    now = datetime.now(UTC)
    p.hass.states.async_set(
        "sensor.storage_discharge_power",
        1000,
        {
            "unit_of_measurement": "W",
            "observed_at": (now - timedelta(seconds=80)).isoformat(),
        },
    )
    p.optimum_request(t, policy=({}, "PV_BALANCE", now + timedelta(seconds=90)))
    assert p.pv_expiry[t] == now + timedelta(seconds=10)
