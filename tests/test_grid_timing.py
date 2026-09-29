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
    assert (
        c.runtime.enabled(t) is True
    )  # Expiry changes profile intent, not permission.


@pytest.mark.parametrize("action", ["off", "select", "edit", "authority"])
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
        assert p.grid_phase(t)[1] == 1100
    elif action == "edit":
        p.wall_time = lambda: 1040
        await p.set_value(t, "grid_start_delay", "00:02")
        assert p.grid_phase(t)[1] == 1160
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
