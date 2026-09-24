"""Grid service path, trailing-edge timing and real solver/command regressions."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_control_runtime import manual as manual
from test_grid_profiles import grid as grid
from test_ocpp21_control import connected as connected
from test_profile_ownership import site as site

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)
from custom_components.wallbox_manager.number import ProfileNumber


class Clock:
    def __init__(self):
        self.ms = 0
        self.waiters = []

    async def wait(self, seconds):
        assert seconds == 1
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.ms + 1000, future))
        await future

    async def advance(self, milliseconds):
        self.ms += milliseconds
        for deadline, future in self.waiters:
            if deadline <= self.ms and not future.done():
                future.set_result(None)
        await asyncio.sleep(0)


async def prepare(profile, target):
    clock = Clock()
    profile.debounce_wait = clock.wait
    profile.wait = lambda _: asyncio.Event().wait()
    await profile.permission(target, True)
    return clock


@pytest.mark.parametrize(
    "values", [[(0, 4.1)], [(0, 4.1), (200, 4.2), (300, 4.3), (300, 4.4)]]
)
async def test_trailing_edge_entity_writes_visible_persisted_final_only(grid, values):
    profile, target, (control, _, peer, *_) = grid
    clock = await prepare(profile, target)
    entity = ProfileNumber(control, "grid", target, "soll_power")
    before = len(peer.requests)
    apply = control.apply_stored = AsyncMock(wraps=control.apply_stored)
    for delay, value in values:
        await clock.advance(delay)
        await entity.async_set_native_value(value)
        assert entity.native_value == value
        stored = await profile.store.async_load()
        assert next(iter(stored.values()))["power_kw"] == value
        assert not apply.await_count
        assert len(peer.requests) == before
    pending = profile.debounce_tasks[target]
    await clock.advance(999)
    assert not apply.await_count and len(peer.requests) == before
    await clock.advance(1)
    await pending
    assert apply.await_count == 1 and len(peer.requests) == before + 1
    assert float(control.intent(target).request.target_w) == values[-1][1] * 1000
    await clock.advance(10000)
    assert apply.await_count == 1


@pytest.mark.parametrize(
    "event", ["off", "profile", "authority", "disconnect", "unload", "generation"]
)
async def test_pending_edit_cancelled_on_invalidating_transitions(grid, event):
    profile, target, (control, bound, peer, *_) = grid
    clock = await prepare(profile, target)
    await profile.set_value(target, "power_kw", 4)
    pending = profile.debounce_tasks[target]
    if event == "off":
        await profile.permission(target, False)
        assert not control.runtime.enabled(target)
    elif event == "profile":
        await profile.select(target, "NETZ")
        assert not control.runtime.enabled(target)
    elif event == "authority":
        control.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                target.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    elif event == "disconnect":
        control.runtime.disconnect(bound.token)
    elif event == "unload":
        await profile.close()
    else:
        control.intent(target).generation += 1
    before = list(peer.operations)
    await clock.advance(1000)
    await asyncio.gather(pending, return_exceptions=True)
    assert peer.operations == before
    assert target not in profile.debounce_tasks


async def test_enable_applies_latest_without_wait_or_duplicate(grid):
    profile, target, (control, _, peer, *_) = grid
    clock = await prepare(profile, target)
    await profile.set_value(target, "power_kw", 4.4)
    pending = profile.debounce_tasks[target]
    count = len(peer.requests)
    await profile.permission(target, True)
    assert len(peer.requests) == count + 1
    assert control.intent(target).request.target_w == 4400
    await clock.advance(1000)
    await asyncio.gather(pending, return_exceptions=True)
    assert len(peer.requests) == count + 1


async def test_switch_cancels_old_wallbox_debounce(site):
    owner, (a, b), _ = site
    control, bound, peer, profile, key = a
    await owner.activate(key)
    clock = await prepare(profile, bound.target)
    await profile.set_value(bound.target, "power_kw", 4)
    pending = profile.debounce_tasks[bound.target]
    await owner.activate(b[4])
    before = list(peer.operations)
    await clock.advance(1000)
    await asyncio.gather(pending, return_exceptions=True)
    assert peer.operations == before and not peer.enabled
    assert not b[2].requests


def charging(control, bound):
    now = datetime.now(UTC)
    control.runtime.observe(
        bound.token,
        tuple(
            Observation(
                Channel(bound.target, quantity),
                value,
                now,
                now,
                now + timedelta(seconds=60),
                "test",
            )
            for quantity, value in [
                (Quantity.CHARGING_STATE, State.CHARGING),
                (Quantity.POWER, 6000),
            ]
        ),
    )


@pytest.mark.parametrize(
    "watts,direction,tolerance,phases",
    [
        (4000, Direction.NEAREST, 5, 3),
        (4000, Direction.NEAREST, 3, 1),
        (4000, Direction.DOWN, 5, 1),
        (4300, Direction.UP, 5, 1),
        (4300, Direction.UP, 15, 3),
    ],
)
async def test_grid_uses_primitive_tolerance_after_direction_filter(
    grid, watts, direction, tolerance, phases
):
    profile, target, (control, bound, _, _, source, _) = grid
    source.mode = PhaseMode.canonical(3)
    control.restore(target, phase_switch_deviation_pct=tolerance, direction=direction)
    clock = await prepare(profile, target)
    charging(control, bound)
    await profile.set_value(target, "power_kw", watts / 1000)
    pending = profile.debounce_tasks[target]
    await clock.advance(1000)
    await pending
    point = control.intent(target).solver_result.point
    assert point.mode.count == phases
    if direction == Direction.DOWN:
        assert point.offered_power_w <= watts
    if direction == Direction.UP:
        assert point.offered_power_w >= watts


@pytest.mark.parametrize("off", ["permission", "zero"])
async def test_disabled_or_zero_point_cannot_retain_stale_phase(grid, off):
    profile, target, (control, bound, peer, _, source, _) = grid
    source.mode = PhaseMode.canonical(3)
    clock = await prepare(profile, target)
    charging(control, bound)
    if off == "permission":
        await profile.permission(target, False)
        before = len(peer.requests)
        await profile.set_value(target, "power_kw", 4)
        assert target not in profile.debounce_tasks
        assert len(peer.requests) == before
        await profile.permission(target, True)
    else:
        from dataclasses import replace

        from custom_components.wallbox_manager.core.capabilities import EvidenceState

        source.snapshot = replace(
            source.snapshot,
            stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
        )
        profile.debounce_wait = AsyncMock(
            side_effect=AssertionError("zero stop must be immediate")
        )
        await profile.set_value(target, "power_kw", 0)
        if pending := profile.debounce_tasks.get(target):
            await pending
        profile.debounce_wait.assert_not_awaited()
        profile.debounce_wait = clock.wait
        assert not control.intent(target).solver_result.point.charging
        # Positive telemetry still exists; the applied OFF point is authoritative.
        await profile.set_value(target, "power_kw", 4)
        pending = profile.debounce_tasks[target]
        await clock.advance(1000)
        await pending
    assert control.intent(target).solver_result.point.mode.count == 1


async def test_tolerances_are_isolated_between_wallboxes(site):
    owner, peers, _ = site
    for tolerance, (control, bound, _peer, profile, key) in zip(
        (5, 3), peers, strict=True
    ):
        await owner.activate(key)
        original = control.inputs
        from dataclasses import replace

        control.inputs = lambda target, original=original: replace(
            original(target), current_mode=PhaseMode.canonical(3)
        )
        control.restore(bound.target, phase_switch_deviation_pct=tolerance)
        clock = await prepare(profile, bound.target)
        charging(control, bound)
        await profile.set_value(bound.target, "power_kw", 4)
        pending = profile.debounce_tasks[bound.target]
        await clock.advance(1000)
        await pending
        assert control.intent(bound.target).solver_result.point.mode.count == (
            3 if tolerance == 5 else 1
        )
    assert [p[0].intent(p[1].target).phase_switch_deviation_pct for p in peers] == [
        5,
        3,
    ]


async def test_backend_limit_fractional_input_and_reserve_validation(grid):
    profile, target, (control, _, _, _, source, _) = grid
    power = ProfileNumber(control, "grid", target, "soll_power")
    assert power.native_max_value == 22.08
    assert power.extra_state_attributes["technical_max_kw"] == 22.08
    await power.async_set_native_value(11.25)
    assert power.native_value == 11.25
    with pytest.raises(ValueError, match="limit"):
        await power.async_set_native_value(22.1)
    for value in (-1, 100.1, 50.5):
        with pytest.raises(ValueError):
            await profile.set_value(target, "min_soc", value)
    source.snapshot = None
    assert power.extra_state_attributes["technical_max_kw"] is None
    with pytest.raises(ValueError, match="invalid profile"):
        await power.async_set_native_value(101)


async def test_no_intermediate_solve_even_for_entity_attributes(grid, monkeypatch):
    import custom_components.wallbox_manager.control.runtime as module

    profile, target, (control, _, _, *_) = grid
    clock = await prepare(profile, target)
    from unittest.mock import Mock

    solve = Mock(wraps=module.solve)
    monkeypatch.setattr(module, "solve", solve)
    entity = ProfileNumber(control, "grid", target, "soll_power")
    for value in (4.1, 4.2, 4.3):
        await entity.async_set_native_value(value)
        assert entity.extra_state_attributes["technical_max_kw"] == 22.08
        assert entity.native_value == value
    assert not solve.call_count
    pending = profile.debounce_tasks[target]
    await clock.advance(1000)
    await pending
    assert solve.call_count > 0
    assert entity.extra_state_attributes["execution_blocked_reason"] is None


async def test_backend_ceiling_shares_exact_grid_and_configured_limits(grid):
    from dataclasses import replace
    from fractions import Fraction

    profile, target, (control, _, _, _, source, _) = grid
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, min_current_a=7, max_current_a=16, current_step_a=2)
            for e in source.snapshot.envelopes
        ),
    )
    control.restore(target, allowed_current_1p=9, allowed_current_3p=8)
    # 3p maximum 8 A lies between the device's 7 and 9 A steps.
    assert control.power_ceiling(target) == 7 * 690
    await profile.set_value(target, "power_kw", Fraction(483, 100))
    with pytest.raises(ValueError, match="limit"):
        await profile.set_value(target, "power_kw", 4.84)


async def test_failed_debounced_apply_clears_pending_diagnostics(grid):
    profile, target, (control, _, _, *_) = grid
    clock = await prepare(profile, target)
    control.apply_stored = AsyncMock(side_effect=ValueError("test failure"))
    await profile.set_value(target, "power_kw", 4)
    pending = profile.debounce_tasks[target]
    await clock.advance(1000)
    await pending
    assert target not in profile.debounce_tasks
    assert profile.status[target] == "error"
    assert (
        control.attributes(target)["execution_blocked_reason"] != "power_edit_pending"
    )
