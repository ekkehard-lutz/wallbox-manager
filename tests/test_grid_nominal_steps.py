"""GRID-31–40: nominal planning, technical identities and draft-only navigation."""

import json
from fractions import Fraction
from unittest.mock import AsyncMock

import pytest
from conftest import config, entities, eventually
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from test_active_control import ready
from test_grid_profile import FakeClock, GridStation, ReviewedLockoutStation, ranges
from test_protocol import peer
from test_release_recovery import session

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
from custom_components.wallbox_manager.grid_point import rounded_kw


def entity(ha, entry, role="soll_power"):
    return next(e for e in entities(ha, entry) if e.role == role)


async def saved_entry(ha, values=None):
    entry = config()
    if values is not None:
        await Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles").async_save(
            {json.dumps(["Wallbox01", "1", "1"]): values}
        )
    return await ha.install(entry)


@pytest.mark.parametrize(
    "kw,mode,amps",
    [(1.4, 1, 6), (1.6, 1, 7), (4.6, 1, 20), (4.1, 3, 6), (4.8, 3, 7), (18.6, 3, 27)],
)
@pytest.mark.parametrize("direction", ["down", "up", "nearest"])
def test_rounded_technical_points_are_exact_in_user_domain(kw, mode, amps, direction):
    point = ranges(modes=(mode,)).resolve(Fraction(str(kw)) * 1000, direction)
    assert (point.phases, point.amps) == (mode, amps)
    assert point.watts == mode * 230 * amps
    assert point.selection() == {"power_kw": kw, "power_phases": mode}


@pytest.mark.parametrize(
    "kw,direction,amps",
    [
        (1.5, "down", 6),
        (1.5, "up", 7),
        (1.5, "nearest", 7),
        (1.7, "down", 7),
        (1.7, "up", 8),
        (1.7, "nearest", 8),
    ],
)
def test_direct_entries_compare_adjacent_rounded_offers(kw, direction, amps):
    assert ranges(modes=(1,)).resolve(Fraction(str(kw)) * 1000, direction).amps == amps


@pytest.mark.parametrize(
    "value,expected",
    [(1.38, 1.4), (1.61, 1.6), (4.14, 4.1), (4.83, 4.8), (18.63, 18.6), (1.45, 1.5)],
)
def test_decimal_half_up_is_deterministic(value, expected):
    assert rounded_kw(value) == expected


@pytest.mark.parametrize("mode", [1, 2, 3])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_navigation_advances_one_legal_current_step(mode, step):
    e = ranges(minimum=7, maximum=29, asymmetry=23, modes=(mode,), step=step)
    points = e.points(mode)
    for first, second in zip(points, points[1:], strict=False):
        assert second.amps - first.amps == step
        assert e.navigate(first.display_watts, mode, 1) == second
        assert e.navigate(second.display_watts, mode, -1) == first
    assert e.navigate(points[-1].display_watts, mode, 1) is None
    assert e.navigate(points[0].display_watts, mode, -1) is None


def test_monotonic_phase_boundaries_and_distinct_equal_power_identities():
    e = ranges()
    assert e.navigate(4400, 1, 1).selection() == {"power_kw": 4.6, "power_phases": 1}
    assert e.navigate(4600, 1, 1).selection() == {"power_kw": 4.8, "power_phases": 3}
    assert e.navigate(4800, 3, -1).selection() == {"power_kw": 4.1, "power_phases": 3}
    assert e.navigate(4100, 3, -1).selection() == {"power_kw": 3.9, "power_phases": 1}
    for phase, amps in ((1, 18), (3, 6)):
        assert (
            e.resolve(
                4100, "down", ongoing=3 - phase if phase == 1 else 1, selected=phase
            ).amps
            == amps
        )
        assert e.resolve(4100, "down", selected=phase).phases == phase
    assert e.resolve(4100, "down", ongoing=3).phases == 3
    assert e.resolve(4100, "down", ongoing=1).phases == 1
    assert e.resolve(4800, "down", selected=2).phases == 3  # unsupported hint ignored
    for invalid_hint in (True, False, "1", 0, 4):
        assert e.resolve(4100, ongoing=3, selected=invalid_hint).phases == 3


@pytest.mark.parametrize(
    "caps,modes,step",
    [
        ({1: 12, 3: 18}, (1, 3), 1),
        ({1: 19, 3: 12}, (1, 3), 2),
        ({1: 0, 2: 13, 3: 17}, (1, 2, 3), 2),
        ({1: 16, 2: 0, 3: 20}, (1, 2, 3), 1),
        ({1: 17, 2: 19, 3: 0}, (1, 2, 3), 3),
        ({1: 0, 2: 0, 3: 12}, (1, 2, 3), 1),
    ],
)
def test_each_enabled_phase_series_reaches_its_own_capped_endpoints(caps, modes, step):
    e = ranges(modes=modes, caps=caps, step=step)
    assert set(e.ranges) == {mode for mode in modes if caps[mode] > 0}
    for mode in e.ranges:
        series = e.points(mode)
        for index, point in enumerate(series):
            for direction in (-1, 1):
                next_index = index + direction
                actual = e.navigate(point.display_watts, mode, direction)
                if 0 <= next_index < len(series):
                    # Overlapping/closer points in other modes cannot cut a
                    # supported current series short in either direction.
                    assert actual == series[next_index]
                else:
                    others = [
                        candidate
                        for other in e.ranges
                        if other != mode
                        for candidate in e.points(other)
                        if (candidate.display_watts - point.display_watts) * direction
                        > 0
                    ]
                    expected = min(
                        others,
                        key=lambda candidate: (
                            abs(candidate.display_watts - point.display_watts),
                            candidate.phases,
                        ),
                        default=None,
                    )
                    assert actual == expected


def test_optional_two_phase_series_is_not_skipped_or_entered_early():
    e = ranges(modes=(1, 2, 3), caps={1: 20, 2: 12, 3: 27})
    # 2p/10 A is equally close to 4.6 kW, but 1p still has its final step.
    assert e.navigate(4400, 1, 1).selection() == {"power_kw": 4.6, "power_phases": 1}
    assert e.navigate(4600, 1, 1).selection() == {"power_kw": 4.8, "power_phases": 3}
    assert e.navigate(4600, 2, 1).selection() == {"power_kw": 5.1, "power_phases": 2}
    assert e.navigate(5500, 2, 1).selection() == {"power_kw": 6.2, "power_phases": 3}
    assert e.navigate(2800, 2, -1).selection() == {"power_kw": 2.5, "power_phases": 1}
    # A zero-cap 3p mode is completely absent, including at capped 1p's endpoint.
    e = ranges(modes=(1, 2, 3), caps={1: 12, 2: 12, 3: 0})
    assert e.navigate(2800, 1, 1).selection() == {"power_kw": 3.2, "power_phases": 2}


@pytest.mark.parametrize(
    "saved", [None, {"power_kw": 0}, {"power_kw": "invalid"}, {"power_kw": -1}]
)
async def test_missing_or_invalid_draft_initializes_once_without_approval_or_control(
    ha, saved
):
    entry = await saved_entry(ha, saved)
    settings = entry.runtime_data.settings
    power = entity(ha, entry)
    assert power.native_value is None and not power.available
    old_approval = dict(settings.values["grid_approved"])
    original = settings.persist
    settings.persist = AsyncMock(wraps=original)
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        await eventually(lambda: power.available)
        assert power.native_value == power.native_min_value == 1.4
        assert power.native_max_value == 18.6
        assert settings.get("power_phases") == 1
        assert settings.values["grid_approved"] == old_approval
        assert settings.get("charge_release") is False
        assert settings.approval_attributes()["grid_set_required"]
        assert not station.profiles and not station.writes
        assert (await settings.store.async_load())[settings.key]["power_kw"] == 1.4
        for _ in range(5):
            await settings.initialize_power()
        assert settings.persist.await_count == 1


@pytest.mark.parametrize("saved", [1.4, 4.1, 11, 11.04])
async def test_valid_existing_user_selection_is_preserved_without_initialization_write(
    ha, saved
):
    entry = await saved_entry(ha, {"power_kw": saved})
    settings = entry.runtime_data.settings
    settings.persist = AsyncMock(wraps=settings.persist)
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        await eventually(lambda: entity(ha, entry).available)
        await settings.initialize_power()
        assert settings.get("soll_power") == saved
        assert settings.persist.await_count == 0
        assert not station.profiles and not station.writes


async def test_voltage_reports_never_change_planning_readiness_or_equivalence(ha):
    entry = await saved_entry(ha, {"power_kw": 1.4})
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        await session(station)
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        await grid.action()
        await eventually(lambda: grid.task.done())
        power = entity(ha, entry)
        baseline = power.extra_state_attributes
        count = len(station.profiles)
        writes = []
        remove = entry.runtime_data.state.watch(
            ("grid", entry.runtime_data.state.binding, "soll_power"),
            lambda: writes.append(True),
        )
        for volts in (217, 240, 250, 230):
            for n in (1, 2, 3):
                entry.runtime_data.state.observe(
                    entry.runtime_data.state.generation,
                    ("observation", ("Wallbox01", "1", None), f"voltage_l{n}"),
                    volts,
                    "test",
                )
            assert power.extra_state_attributes == baseline
            assert grid.equivalent({**settings.values, "power_kw": 1.38})
            assert grid.draft_point().amps == 6
            assert (
                entry.runtime_data.state.value(
                    ("observation", ("Wallbox01", "1", None), "voltage_l1")
                )
                == volts
            )
        remove()
        assert not writes and len(station.profiles) == count
        await grid.action()
        await settings.set("soll_power", 1.4)
        ready_before = grid.action_state()
        for n in (1, 2, 3):
            entry.runtime_data.state.observe(
                entry.runtime_data.state.generation,
                ("observation", ("Wallbox01", "1", None), f"voltage_l{n}"),
                249,
                "test",
            )
        assert grid.action_state() == ready_before
        assert ready_before["charge_action_ready"] is True


async def test_ha_relative_service_roundtrip_phase_identity_and_stale_revisions(ha):
    from homeassistant.components import number as number_component

    entry = await saved_entry(ha, {"power_kw": 4.1, "power_phases": 3})
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        power, settings = entity(ha, entry), entry.runtime_data.settings
        revision = settings.values["grid_draft_revision"]
        data = {
            "entity_id": power.entity_id,
            "direction": -1,
            "revision": revision,
            "incarnation": entry.runtime_data.state.runtime_id,
        }
        await ha.services.async_call(
            "wallbox_manager", "step_power", data, blocking=True
        )
        assert settings.get("soll_power") == 3.9 and settings.get("power_phases") == 1
        assert power.extra_state_attributes["power_step_up"] == {
            "power_kw": 4.1,
            "power_phases": 1,
        }
        with pytest.raises(HomeAssistantError, match="revision"):
            await ha.services.async_call(
                "wallbox_manager", "step_power", data, blocking=True
            )
        data.update(
            revision=settings.values["grid_draft_revision"], incarnation="obsolete"
        )
        with pytest.raises(HomeAssistantError, match="runtime"):
            await ha.services.async_call(
                "wallbox_manager", "step_power", data, blocking=True
            )
        assert not station.profiles and not station.writes
        await settings.step_power(1, settings.values["grid_draft_revision"])
        assert settings.get("soll_power") == 4.1 and settings.get("power_phases") == 1
        assert await number_component.async_setup(ha, {})
        await ha.services.async_call(
            "number",
            "set_value",
            {"entity_id": power.entity_id, "value": 4.1},
            blocking=True,
        )
        assert settings.get("power_phases") is None  # same numeric edit clears hint
        await ha.services.async_call(
            "number",
            "set_value",
            {"entity_id": power.entity_id, "value": 4.15},
            blocking=True,
        )
        assert power.native_value == settings.get("soll_power") == 4.2
        assert not station.profiles and not station.writes


async def test_phase_only_draft_is_meaningful_and_survives_reload(ha):
    entry = await saved_entry(ha, {"power_kw": 4.1, "power_phases": 1})
    async with peer(entry, station_type=GridStation) as station:
        await ready(entry, station)
        settings = entry.runtime_data.settings
        await settings._approve_release()
        await settings.step_power(1, settings.values["grid_draft_revision"])
        await settings.step_power(1, settings.values["grid_draft_revision"])
        await settings.step_power(1, settings.values["grid_draft_revision"])
        await settings.step_power(-1, settings.values["grid_draft_revision"])
        assert settings.get("soll_power") == 4.1 and settings.get("power_phases") == 3
        assert settings.approved("power_phases") == 1
        assert settings.approval_attributes()["grid_set_required"]
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    assert entry.runtime_data.settings.get("power_phases") == 3
    assert entry.runtime_data.settings.approved("power_phases") == 1


@pytest.mark.parametrize(
    "direction,initial_kw,initial_phase,initial_amps,step,target_kw,target_phase,target_amps",
    [
        ("down", 4.1, 3, 6, -1, 3.9, 1, 17),
        ("up", 4.6, 1, 20, 1, 4.8, 3, 7),
    ],
)
async def test_explicit_technical_phase_transition_pauses_for_both_lockouts(
    ha,
    direction,
    initial_kw,
    initial_phase,
    initial_amps,
    step,
    target_kw,
    target_phase,
    target_amps,
):
    entry = await saved_entry(
        ha,
        {"power_kw": initial_kw, "power_phases": initial_phase, "direction": direction},
    )
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.phases = initial_phase
        station.values["PhaseRotation"] = "Rxx" if initial_phase == 1 else "RST"
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        await session(station)
        grid, settings = entry.runtime_data.grid, entry.runtime_data.settings
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await grid.action()
        assert station.amps == initial_amps
        station.locked = True
        count = len(station.profiles)
        await settings.step_power(step, settings.values["grid_draft_revision"])
        assert (
            settings.get("soll_power") == target_kw
            and settings.get("power_phases") == target_phase
        )
        assert len(station.profiles) == count  # draft-only
        await grid.action()
        assert station.amps == 0 and grid.requested.amps == target_amps
        assert grid.requested.phases == target_phase
        assert grid.confirmed.phases == initial_phase and grid.retry_at == 6
        assert settings.get("charge_release") is True
        assert station.profiles[-1][1]["charging_schedule"][0][
            "charging_schedule_period"
        ] == [{"start_period": 0, "limit": 0}]
        station.locked, station.reenable = False, True
        await clock.advance(6)
        await eventually(lambda: grid.task.done())
        assert grid.reason == "ReenableLockout" and grid.retry_at == 14
        station.reenable = False
        await clock.advance(14)
        await eventually(lambda: grid.status == "confirmed")
        assert (station.phases, station.amps) == (target_phase, target_amps)
        count = len(station.profiles)
        await clock.advance(600)
        assert len(station.profiles) == count
