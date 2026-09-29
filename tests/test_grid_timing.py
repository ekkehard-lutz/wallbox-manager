"""Relative schedules exercise the real solver and normal control boundary."""

import asyncio
from dataclasses import replace

import pytest
from test_control_runtime import manual as manual
from test_grid_profiles import grid as base_grid  # noqa: F401
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.grid_timing import (
    duration_seconds,
    duration_text,
)


@pytest.fixture
async def grid(base_grid):  # noqa: F811
    p, t, (c, bound, peer, _, source, _) = base_grid
    source.snapshot = replace(
        source.snapshot,
        stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
    )
    p.wall_time = lambda: 1000
    yield base_grid


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("", None),
        ("00:05", 300),
        ("01:30", 5400),
        ("24:00", 86400),
        ("120:15", 432900),
        ("00:00", 0),
    ],
)
def test_duration_parse(text, seconds):
    assert duration_seconds(text) == seconds
    assert duration_text(seconds) == text


@pytest.mark.parametrize(
    "text", ["1:5", "01:60", "-01:00", "abc", " 01:00", "01:00 ", "１:００", None]
)
def test_duration_reject(text):
    with pytest.raises(ValueError):
        duration_seconds(text)


@pytest.mark.parametrize(
    "delay,duration,initial,deadline",
    [
        ("", "", "active", None),
        ("00:01", "", "waiting", 1060),
        ("", "00:02", "active", 1120),
        ("00:01", "00:02", "waiting", 1060),
    ],
)
async def test_four_schedules(grid, delay, duration, initial, deadline):
    p, t, (c, *_) = grid
    await p.set_value(t, "grid_start_delay", delay)
    await p.set_value(t, "grid_duration", duration)
    await p.permission(t, True)
    assert p.grid_phase(t) == (initial, deadline)
    assert c.intent(t).request.target_w == (0 if initial == "waiting" else 11000)
    assert c.runtime.enabled(t) is True
    if duration:
        start = 1000 + (duration_seconds(delay) or 0)
        p.wall_time = lambda: start
        assert p.grid_phase(t) == ("active", start + 120)
        p.wall_time = lambda: start + 120
        assert p.grid_phase(t) == ("expired", None)


async def test_timer_dispatches_start_and_expiry_through_control(grid):
    p, t, (c, _, peer, *_) = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.set_value(t, "grid_duration", "00:02")
    gates = asyncio.Queue()
    deadlines = asyncio.Queue()

    async def wait(seconds):
        await deadlines.put(seconds)
        await gates.get()

    p.timer_wait = wait
    await p.permission(t, True)
    assert await deadlines.get() == 60
    count = len(peer.requests)
    p.wall_time = lambda: 1060
    await gates.put(True)
    assert await deadlines.get() == 120
    assert c.intent(t).request.target_w == 11000
    assert len(peer.requests) > count
    timer = p.grid_timers[t]
    p.wall_time = lambda: 1180
    await gates.put(True)
    await timer
    assert c.intent(t).request.target_w == 0
    assert p.status[t] == "grid_expired"
    assert c.runtime.enabled(t) is False
    assert not p.setting(t).get("grid_request")
    await p.permission(t, True)
    assert c.runtime.enabled(t) is True
    assert p.grid_phase(t) == ("active", None)
    assert c.intent(t).request.target_w == 11000


@pytest.mark.parametrize("action", ["off", "select", "authority"])
async def test_old_generation_cannot_dispatch(grid, action):
    p, t, (c, bound, peer, *_) = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.permission(t, True)
    epoch = p.epochs[t]
    if action == "off":
        await p.permission(t, False)
    elif action == "select":
        p.references.update(leistung_pv="sensor.pv", leistung_verbraucher="sensor.load")
        await p.select(t, "PV_SURPLUS")
        p.wall_time = lambda: 1040
        await p.select(t, "NETZ")
        assert p.grid_phase(t) == ("active", None)
    else:
        from datetime import UTC, datetime

        from custom_components.wallbox_manager.core.authority import (
            AuthorityObservation,
            ControlAuthority,
        )

        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    count = len(peer.requests)
    p.wall_time = lambda: 2000
    await p.grid_timer(t, epoch)
    assert len(peer.requests) == count


async def test_reload_preserves_absolute_schedule(grid):
    p, t, _ = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.set_value(t, "grid_duration", "00:02")
    await p.permission(t, True)
    await p.save()
    p.settings.clear()
    p.wall_time = lambda: 1100
    await p.load()
    assert p.grid_phase(t) == ("active", 1180)
    p.wall_time = lambda: 1200
    assert p.grid_phase(t) == ("expired", None)


@pytest.mark.parametrize("elapsed", [90, 300])
async def test_slow_initial_permission_reconciles_elapsed_deadline(grid, elapsed):
    p, t, (c, _, _, *_) = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.set_value(t, "grid_duration", "00:02")
    original = c.request_enabled

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        p.wall_time = lambda: 1000 + elapsed
        return result

    c.request_enabled = delayed
    parked = asyncio.Queue()
    gate = asyncio.Event()

    async def wait(seconds):
        parked.put_nowait(seconds)
        if seconds > 0:
            await gate.wait()

    p.timer_wait = wait
    await p.permission(t, True)
    assert await parked.get() == 0
    await asyncio.sleep(0)
    assert c.intent(t).request.target_w == (11000 if elapsed == 90 else 0)
    if elapsed == 90:
        assert await parked.get() == 90


async def test_ownership_loss_fences_scheduled_start(grid):
    p, t, (c, _, peer, *_) = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.permission(t, True)
    epoch = p.epochs[t]
    c.profile_permitted = lambda target: False
    p.wall_time = lambda: 2000
    count = len(peer.requests)
    await p.grid_timer(t, epoch)
    assert len(peer.requests) == count


@pytest.mark.parametrize("value", ["", "00:00", "01:30", "24:00", "120:15"])
@pytest.mark.parametrize("guard", ["permission_off", "local", "inactive"])
async def test_duration_entity_configuration_round_trips_without_commands(
    grid, value, guard
):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )
    from custom_components.wallbox_manager.text import ProfileDuration

    p, t, (c, bound, peer, *_) = grid
    if guard == "local":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    elif guard == "inactive":
        c.profile_permitted = lambda target: False
    count = len(peer.operations)
    for key in ("grid_start_delay", "grid_duration"):
        entity = ProfileDuration(c, p.entry_id, t, key)
        assert entity.available
        assert entity.native_value == ""
        await entity.async_set_value(value)
        assert entity.native_value == value
        assert p.setting(t).get(key) == duration_seconds(value)
    assert len(peer.operations) == count
    assert not p.grid_timers
    await p.save()
    p.settings.clear()
    await p.load()
    assert ProfileDuration(c, p.entry_id, t, "grid_duration").native_value == value


@pytest.mark.parametrize("value", [0, 40.0, 99, 40.5, -1, 100])
async def test_target_soc_entity_whole_percent_edits(grid, value):
    from custom_components.wallbox_manager.number import ProfileNumber

    p, t, (c, _, peer, *_) = grid
    entity = ProfileNumber(c, p.entry_id, t, "soll_soc_speicher")
    count = len(peer.operations)
    if value in (0, 40, 99):
        await entity.async_set_native_value(value)
        assert entity.native_value == value
    else:
        with pytest.raises(ValueError):
            await entity.async_set_native_value(value)
        assert entity.native_value == 95
    assert len(peer.operations) == count


@pytest.mark.parametrize(
    "delay,duration", [("", ""), ("00:01", ""), ("", "00:02"), ("00:01", "00:02")]
)
async def test_pending_values_arm_only_on_permission_and_are_consumed_once(
    grid, delay, duration
):
    p, t, (c, *_) = grid
    await p.set_value(t, "power_kw", 9.9)
    await p.set_value(t, "min_soc", 40)
    await p.set_value(t, "grid_start_delay", delay)
    await p.set_value(t, "grid_duration", duration)
    assert p.grid_phase(t) == ("active", None)
    p.wall_time = lambda: 2000
    await p.permission(t, True)
    request = p.setting(t).get("grid_request")
    if delay or duration:
        assert request["activated_at"] == 2000
        assert request["start_at"] == 2000 + (duration_seconds(delay) or 0)
        assert p.setting(t)["grid_start_delay"] is None
        assert p.setting(t)["grid_duration"] is None
    await p.permission(t, False)
    await p.permission(t, True)
    assert p.grid_phase(t) == ("active", None)
    assert not p.setting(t).get("grid_request")
    assert c.intent(t).request.target_w == 9900
    assert p.setting(t)["min_soc"] == 40
    await p.permission(t, False)
    await p.set_value(t, "grid_start_delay", "00:03")
    await p.permission(t, True)
    assert p.grid_phase(t) == ("waiting", 2180)


@pytest.mark.parametrize("now", [1020, 1100])
@pytest.mark.parametrize("action", ["off", "profile"])
async def test_cancel_consumes_both_and_survives_reload(grid, now, action):
    p, t, _ = grid
    await p.set_value(t, "power_kw", 9.9)
    await p.set_value(t, "min_soc", 40)
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.set_value(t, "grid_duration", "00:02")
    await p.permission(t, True)
    p.wall_time = lambda: now
    if action == "off":
        await p.permission(t, False)
    else:
        p.references.update(leistung_pv="sensor.pv", leistung_verbraucher="sensor.load")
        await p.select(t, "PV_SURPLUS")
        await p.select(t, "NETZ")
    assert p.grid_attributes(t)["grid_timing_state"] == "cancelled"
    p.settings.clear()
    await p.load()
    await p.permission(t, True)
    assert p.grid_phase(t) == ("active", None)
    assert p.setting(t)["power_kw"] == 9.9
    assert p.setting(t)["min_soc"] == 40


@pytest.mark.parametrize("delay", ["", "00:00", "01:30"])
async def test_zero_duration_disables_immediately_without_positive_command(grid, delay):
    from unittest.mock import AsyncMock

    from custom_components.wallbox_manager.switch import ChargingEnabled

    p, t, (c, *_) = grid
    await p.set_value(t, "grid_start_delay", delay)
    await p.set_value(t, "grid_duration", "00:00")
    c.request_enabled = AsyncMock(wraps=c.request_enabled)
    await p.permission(t, True)
    assert [call.args[1] for call in c.request_enabled.call_args_list] == [False]
    assert ChargingEnabled(c, "grid", t).is_on is False
    assert c.intent(t).request.target_w == 0
    assert p.grid_attributes(t)["grid_timing_state"] == "consumed"
    await p.permission(t, True)
    assert p.grid_phase(t) == ("active", None)
    assert ChargingEnabled(c, "grid", t).is_on is True


@pytest.mark.parametrize("delay", ["00:00", "00:01"])
async def test_delay_only_consumes_at_start(grid, delay):
    p, t, (c, *_) = grid
    await p.set_value(t, "grid_start_delay", delay)
    gate = asyncio.Event()

    async def wait(seconds):
        await gate.wait()

    p.timer_wait = wait
    await p.permission(t, True)
    timer = p.grid_timers[t]
    p.wall_time = lambda: 1060
    gate.set()
    await timer
    assert c.intent(t).request.target_w == 11000
    assert not p.setting(t).get("grid_request")
    assert p.grid_attributes(t)["grid_timing_state"] == "consumed"


@pytest.mark.parametrize("field", ["grid_start_delay", "grid_duration"])
@pytest.mark.parametrize("value", ["", "00:00", "00:05"])
async def test_armed_timing_cannot_be_edited(grid, field, value):
    p, t, _ = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.permission(t, True)
    request = dict(p.setting(t)["grid_request"])
    epoch = p.epochs[t]
    with pytest.raises(ValueError, match="armed"):
        await p.set_value(t, field, value)
    assert p.setting(t)["grid_request"] == request
    assert p.epochs[t] == epoch


@pytest.mark.parametrize(
    "now,phase,deadline",
    [(1020, "waiting", 1060), (1100, "active", 1180), (1200, "expired", None)],
)
async def test_restart_reconstructs_deadlines_and_processes_downtime_expiry(
    grid, now, phase, deadline
):
    p, t, (c, *_) = grid
    await p.set_value(t, "grid_start_delay", "00:01")
    await p.set_value(t, "grid_duration", "00:02")
    await p.permission(t, True)
    p.invalidate(t)
    p.settings.clear()
    p.wall_time = lambda: now
    await p.load()
    assert p.grid_phase(t) == (phase, deadline)
    attrs = p.grid_attributes(t)
    assert attrs["grid_start_deadline"] == 1060
    assert attrs["grid_end_deadline"] == 1180
    assert attrs["grid_armed_duration_seconds"] == 120
    if phase == "expired":
        await p.recover(t, {"enabled_intent": True})
        assert c.runtime.enabled(t) is False
        assert not p.setting(t).get("grid_request")
        p.settings.clear()
        await p.load()
        await p.permission(t, True)
        assert p.grid_phase(t) == ("active", None)
    else:
        # Recovery when hardware needs enabling must reuse, not re-arm, the request.
        await p.permission(t, True, _resume=True)
        assert p.grid_phase(t) == (phase, deadline)


async def test_beta15_migration_discards_old_clock_preserves_pending_once(grid):
    p, t, _ = grid
    p.setting(t).update(
        power_kw=9.9,
        min_soc=40,
        grid_start_delay=60,
        grid_duration=120,
        grid_activated_at=1,
    )
    await p.save()
    p.settings.clear()
    await p.load()
    assert "grid_activated_at" not in p.setting(t)
    assert p.grid_phase(t) == ("active", None)
    assert p.setting(t)["grid_start_delay"] == 60
    assert p.setting(t)["grid_duration"] == 120
    await p.permission(t, True)
    assert p.grid_phase(t) == ("waiting", 1060)
    await p.permission(t, False)
    await p.permission(t, True)
    assert p.grid_phase(t) == ("active", None)
    assert p.setting(t)["power_kw"] == 9.9
    assert p.setting(t)["min_soc"] == 40


async def test_failed_expiry_retains_confirmed_permission_and_retries_canonical_off(
    grid,
):
    from unittest.mock import AsyncMock

    from custom_components.wallbox_manager.control.commands import (
        CommandReason,
        CommandResult,
        CommandStatus,
        ControlArea,
    )
    from custom_components.wallbox_manager.switch import ChargingEnabled

    p, t, (c, *_) = grid
    await p.set_value(t, "grid_duration", "00:01")
    await p.permission(t, True)
    p.invalidate(t)
    p.wall_time = lambda: 1060
    original = c.request_enabled
    c.request_enabled = AsyncMock(
        return_value=CommandResult(
            CommandStatus.TEMPORARILY_REJECTED,
            ControlArea.CHARGING_PERMISSION,
            CommandReason.NO_AUTHORITY,
        )
    )
    gates, waits = asyncio.Queue(), asyncio.Queue()

    async def wait(seconds):
        waits.put_nowait(seconds)
        await gates.get()

    p.timer_wait = wait
    await p.permission(t, False, _grid_expiry=True)
    assert ChargingEnabled(c, "grid", t).is_on is True
    assert p.grid_attributes(t)["grid_timing_state"] == "stopping"
    assert await waits.get() == 60
    timer = p.grid_timers[t]
    c.request_enabled = original
    gates.put_nowait(True)
    await timer
    assert ChargingEnabled(c, "grid", t).is_on is False
    assert p.grid_attributes(t)["grid_timing_state"] == "consumed"


@pytest.mark.parametrize("guard", ["new_authorization", "ownership", "authority"])
async def test_stale_expiry_cannot_revoke_newer_or_unowned_permission(grid, guard):
    from datetime import UTC, datetime

    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, *_) = grid
    await p.set_value(t, "grid_duration", "00:01")
    await p.permission(t, True)
    epoch = p.epochs[t]
    if guard == "new_authorization":
        await p.permission(t, False)
        await p.permission(t, True)
    elif guard == "ownership":
        c.profile_permitted = lambda target: False
    else:
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    count = len(peer.operations)
    p.wall_time = lambda: 1200
    await p.grid_timer(t, epoch)
    assert len(peer.operations) == count
    assert c.runtime.enabled(t) is True


async def test_beta15_recovery_does_not_arm_pending_values(grid):
    p, t, _ = grid
    p.setting(t).update(grid_start_delay=60, grid_duration=120, grid_activated_at=1)
    await p.save()
    p.settings.clear()
    await p.load()
    await p.recover(t, {"enabled_intent": True})
    assert p.grid_phase(t) == ("active", None)
    assert p.setting(t)["grid_start_delay"] == 60
    assert p.setting(t)["grid_duration"] == 120


@pytest.mark.parametrize("initial_enabled", [False, True])
async def test_failed_initial_enable_still_expires_through_permission_off(
    grid, initial_enabled
):
    from unittest.mock import AsyncMock

    from custom_components.wallbox_manager.control.commands import (
        CommandReason,
        CommandResult,
        CommandStatus,
        ControlArea,
    )

    p, t, (c, *_) = grid
    if initial_enabled:
        await p.permission(t, True)
    await p.set_value(t, "grid_duration", "00:01")
    original = c.request_enabled
    c.request_enabled = AsyncMock(
        return_value=CommandResult(
            CommandStatus.TEMPORARILY_REJECTED,
            ControlArea.CHARGING_PERMISSION,
            CommandReason.NO_AUTHORITY,
        )
    )
    gate = asyncio.Event()

    async def wait(seconds):
        await gate.wait()

    p.timer_wait = wait
    await p.permission(t, True)
    timer = p.grid_timers[t]
    c.request_enabled = AsyncMock(wraps=original)
    p.wall_time = lambda: 1060
    gate.set()
    await timer
    assert c.request_enabled.call_args.args[1] is False
    assert c.runtime.enabled(t) is False
    assert not p.setting(t).get("grid_request")
