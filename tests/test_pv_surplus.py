"""PV policy boundaries plus real Grid runtime/solver integration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest
from test_control_runtime import manual as manual
from test_grid_profiles import grid as base_grid  # noqa: F401
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.profiles import GridProfiles
from custom_components.wallbox_manager.pv_surplus import PV_DEFAULTS, decision, reading


@pytest.fixture
async def grid(base_grid):  # noqa: F811
    _, _, (_, _, _, _, source, _) = base_grid
    source.snapshot = replace(
        source.snapshot,
        stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
    )
    profile, target, _ = base_grid
    profile.references.update(
        leistung_pv="sensor.pv", leistung_verbraucher="sensor.load"
    )
    # These beta.1 state-machine cases explicitly exercise immediate PV pauses.
    profile.setting(target)["pv_stop_delay"] = 0
    return base_grid


@pytest.mark.parametrize(
    "soc,ongoing,power,direction,status",
    [
        (96, False, 6000, Direction.UP, "actively_charging"),
        (95, False, 0, Direction.DOWN, "waiting_battery_soc"),
        (90, False, 0, Direction.DOWN, "waiting_battery_soc"),
        (89, False, 0, Direction.DOWN, "stopped_battery_soc"),
        (95, True, 6000, Direction.DOWN, "actively_charging"),
        (90, True, 6000, Direction.DOWN, "actively_charging"),
        (89, True, 0, Direction.DOWN, "stopped_battery_soc"),
        (96, True, 6000, Direction.UP, "actively_charging"),
    ],
)
def test_soc_boundaries(soc, ongoing, power, direction, status):
    assert decision(6000, soc, PV_DEFAULTS, ongoing) == (power, direction, status)


@pytest.mark.parametrize("approximation", ["up", "down"])
@pytest.mark.parametrize("power", [-3000, 0, 1, 6000])
def test_without_battery(approximation, power):
    result = decision(
        power, None, {**PV_DEFAULTS, "approximation": approximation}, False
    )
    assert result[:2] == (max(0, power), Direction(approximation))


def test_hysteresis_clamp_and_pause_restart():
    settings = {**PV_DEFAULTS, "soll_soc_speicher": 100, "soc_hysterese": 10}
    assert decision(6000, 99, settings, False)[0] == 0
    assert decision(6000, 100, settings, False)[0] == 6000
    assert decision(6000, 89, settings, True)[0] == 6000
    assert decision(6000, 88, settings, True)[0] == 0
    assert decision(-1, 96, PV_DEFAULTS, True)[2] == "paused_insufficient_pv"
    assert decision(6000, 95, PV_DEFAULTS, False)[0] == 0
    assert decision(6000, 96, PV_DEFAULTS, False)[0] == 6000


@pytest.mark.parametrize(
    "updates",
    [
        {"soll_soc_speicher": 100},
        {"soc_hysterese": 96},
        {"soc_hysterese": -1},
        {"regulation_interval": 0},
        {"regulation_interval": 301},
        {"approximation": "nearest"},
    ],
)
def test_invalid_settings(updates):
    with pytest.raises(ValueError):
        GridProfiles.validate_pv({**PV_DEFAULTS, **updates})


@pytest.mark.parametrize(
    "value,unit,expected",
    [("8", "kW", 8000), ("8000", "W", 8000), ("0.008", "MW", 8000)],
)
def test_power_normalization(value, unit, expected):
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state=value, attributes={"unit_of_measurement": unit}, last_updated=now
    )
    assert reading(state, now) == expected


@pytest.mark.parametrize(
    "value,unit,age",
    [
        ("nan", "W", 0),
        ("inf", "W", 0),
        ("unavailable", "W", 0),
        ("1", "Wh", 0),
        ("1", "W", 91),
        ("1", "W", -1),
    ],
)
def test_invalid_measurements(value, unit, age):
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state=value,
        attributes={"unit_of_measurement": unit},
        last_updated=now - timedelta(seconds=age),
    )
    with pytest.raises(ValueError):
        reading(state, now)


def measurements(profile, target, *, pv=8000, load=5000, actual=3000, soc=None):
    profile.references.update(
        leistung_pv="sensor.pv", leistung_verbraucher="sensor.load"
    )
    profile.hass.states.async_set("sensor.pv", pv, {"unit_of_measurement": "W"})
    profile.hass.states.async_set("sensor.load", load, {"unit_of_measurement": "W"})
    attrs = {
        "unit_of_measurement": "W",
        "wallbox_manager_role": "session_power",
        "wallbox_manager_entry": profile.entry_id,
        "station_id": target.station.value,
        "evse_id": target.evse.value,
        "connector_id": target.value,
        "runtime_incarnation": profile.control.runtime.runtime_id,
    }
    profile.hass.states.async_set("sensor.selected", actual, attrs)
    profile.hass.states.async_set(
        "sensor.other", 99999, {**attrs, "connector_id": "other"}
    )
    if soc is not None:
        profile.references["soc_speicher_aktuell"] = "sensor.soc"
        profile.hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": "%"})


async def test_scoped_actual_measurement(grid):
    p, t, _ = grid
    measurements(p, t)
    assert p.pv_measurements(t) == (6000, None)
    p.hass.states.async_set("sensor.selected", "unavailable")
    assert p.pv_request(t)[2] == "measurements_unavailable"


@pytest.mark.parametrize(
    "missing", ["leistung_pv", "leistung_verbraucher", "soc_speicher_aktuell"]
)
async def test_missing_configured_reference(grid, missing):
    p, t, _ = grid
    measurements(p, t, soc=96)
    p.references[missing] = "sensor.missing"
    assert p.pv_request(t)[0] == 0
    assert p.pv_request(t)[2] == "measurements_unavailable"


async def test_enable_below_threshold_and_repeat_no_commands(grid):
    p, t, (c, _, peer, *_) = grid
    measurements(p, t, soc=95)
    await p.select(t, "PV_SURPLUS")
    gate = asyncio.Event()
    p.wait = lambda seconds: gate.wait()
    await p.permission(t, True)
    assert c.runtime.enabled(t) is True
    assert c.intent(t).request.target_w == 0
    count = len(peer.requests)
    await asyncio.sleep(0)
    assert len(peer.requests) == count
    assert p.status[t] == "waiting_battery_soc"
    assert not p.pv_ongoing[t]


async def test_repeated_cycles_pause_and_restart(grid):
    p, t, (c, _, peer, *_) = grid
    measurements(p, t, soc=96)
    await p.select(t, "PV_SURPLUS")
    gate = asyncio.Event()
    cycles = []

    async def wait(seconds):
        assert seconds == 5
        cycles.append(len(peer.requests))
        if len(cycles) == 4:
            await gate.wait()

    async def debounce(seconds):
        assert seconds == 1

    p.wait, p.debounce_wait = wait, debounce
    await p.permission(t, True)
    await asyncio.sleep(0)
    assert len(cycles) == 4
    assert len(set(cycles)) == 1
    assert p.pv_ongoing[t]
    # Evaluate policy transitions through the same solver, retaining permission.
    measurements(p, t, pv=0, soc=95)
    result = p.pv_edit(t)
    assert not result.point.charging
    assert not p.pv_ongoing[t]
    await c.apply_stored(t)
    measurements(p, t, soc=95)
    assert not p.pv_edit(t).point.charging
    measurements(p, t, soc=96)
    assert p.pv_edit(t).point.charging
    assert c.runtime.enabled(t) is True


async def test_persistence_and_cancellation(grid):
    p, t, (c, _, peer, *_) = grid
    measurements(p, t)
    await p.select(t, "PV_SURPLUS")
    await p.set_value(t, "soc_hysterese", 7)
    await p.set_value(t, "regulation_interval", 12)
    await p.set_value(t, "approximation", "up")
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(t, True)
    task = p.tasks[t]
    await asyncio.sleep(0)
    await p.select(t, "NETZ")
    await asyncio.gather(task, return_exceptions=True)
    count = len(peer.requests)
    p.settings.clear()
    await p.load()
    assert p.setting(t)["soc_hysterese"] == 7
    assert p.setting(t)["regulation_interval"] == 12
    assert p.setting(t)["approximation"] == "up"
    assert len(peer.requests) == count
    assert not c.runtime.enabled(t)


@pytest.mark.parametrize("direction", ["up", "down"])
async def test_common_solver_minimum_power(grid, direction):
    p, t, _ = grid
    measurements(p, t, pv=1, load=0, actual=0)
    await p.select(t, "PV_SURPLUS")
    await p.set_value(t, "approximation", direction)
    result = p.pv_edit(t)
    assert result.point.charging is (direction == "up")
    if direction == "up":
        assert result.point.current_a >= Fraction(6)


@pytest.mark.parametrize(
    "value,unit",
    [("101", "%"), ("-1", "%"), ("0.95", None), ("95", "W"), ("unknown", "%")],
)
def test_invalid_soc_units_and_range(value, unit):
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        reading(
            SimpleNamespace(
                state=value, attributes={"unit_of_measurement": unit}, last_updated=now
            ),
            now,
            soc=True,
        )


async def test_expired_session_power_and_missing_reference(grid):
    p, t, _ = grid
    assert p.pv_request(t)[2] == "measurements_unavailable"
    measurements(p, t)
    state = p.hass.states.get("sensor.selected")
    p.hass.states.async_set(
        "sensor.selected",
        state.state,
        {
            **state.attributes,
            "valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert p.pv_request(t)[2] == "measurements_unavailable"


@pytest.mark.parametrize("change", ["authority", "deselection", "unload"])
async def test_pending_regulation_cancellation(grid, change):
    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, *_) = grid
    measurements(p, t)
    await p.select(t, "PV_SURPLUS")
    gate, reached = asyncio.Event(), asyncio.Event()

    async def wait(_):
        reached.set()
        await gate.wait()

    p.wait = wait
    await p.permission(t, True)
    task = p.tasks[t]
    await reached.wait()
    if change == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    elif change == "deselection":
        c.profile_permitted = lambda _: False
        p.invalidate(t)  # Same boundary used by installation ownership.activate.
    else:
        await p.close()
    count = len(peer.requests)
    gate.set()
    await asyncio.gather(task, return_exceptions=True)
    assert len(peer.requests) == count
    assert not p.pv_ongoing[t]


async def test_measurement_change_fences_queued_positive_command(grid):
    p, t, (c, bound, peer, *_) = grid
    measurements(p, t)
    await p.select(t, "PV_SURPLUS")
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(t, True)
    # Block the existing adapter's command serialization before changing target.
    p.invalidate(t)
    measurements(p, t, pv=10000)
    p.pv_edit(t)
    expected = p.pv_request(t)
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(
            c.apply_stored(t, fence=lambda: p.pv_request(t) == expected)
        )
        await asyncio.sleep(0)
        assert not pending.done()
        measurements(p, t, soc="unavailable")
    result = await pending
    assert result.status.value != "applied"
    # Invalid battery telemetry also requests immediate OFF through the regulator.
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests[count:]
    )


async def test_safety_stop_skips_debounce_and_preserves_permission(grid):
    p, t, (c, _, peer, *_) = grid
    measurements(p, t, soc=96)
    await p.select(t, "PV_SURPLUS")
    gate, first = asyncio.Event(), asyncio.Event()

    async def wait(_):
        first.set()
        await gate.wait()
        gate.clear()

    p.wait = wait
    await p.permission(t, True)
    await first.wait()
    first.clear()

    async def forbidden(_):
        raise AssertionError("OFF must not debounce")

    p.debounce_wait = forbidden
    measurements(p, t, soc=89)
    gate.set()
    await asyncio.wait_for(first.wait(), 1)
    assert p.status[t] == "stopped_battery_soc"
    assert not c.confirmed_point(t).charging
    assert c.runtime.enabled(t) is True


async def test_pv_phase_lockout_keeps_retry_deadline_and_fallback(grid):
    from test_phase_lockout import setup

    p, t, manual = grid
    c, _, peer, _, locked, _ = await setup(manual)
    p.setting(t).update(profile="PV_SURPLUS", approximation="up")
    measurements(p, t, pv=1700, load=0, actual=0)
    cycles = []
    gate, reached = asyncio.Event(), asyncio.Event()
    p.monotonic = lambda: 100

    async def wait(_):
        cycles.append(len(peer.requests))
        if len(cycles) == 4:
            reached.set()
            await gate.wait()

    async def debounce(_):
        pass

    p.wait, p.debounce_wait = wait, debounce
    p.launch(t)
    await asyncio.wait_for(reached.wait(), 1)
    assert len(set(cycles)) == 1
    assert c.intent(t).phase_retry
    assert p.pv_retry_until[t] == 160
    assert c.confirmed_point(t).mode.count == 3
    # Expiry may retry the phase change but not resend an identical fallback.
    p.monotonic = lambda: 161
    retried = asyncio.Event()

    async def next_wait(_):
        retried.set()
        await asyncio.Event().wait()

    p.wait = next_wait
    count = len(peer.requests)
    gate.set()
    await asyncio.wait_for(retried.wait(), 1)
    assert len(peer.requests) == count + 1
    assert p.pv_retry_until[t] == 221
    assert c.confirmed_point(t).mode.count == 3
    locked[0] = False


async def test_reload_restores_profile_only(grid):
    p, t, (c, _, peer, *_) = grid
    await p.select(t, "PV_SURPLUS")
    await p.set_value(t, "soc_hysterese", 9)
    clone = GridProfiles(
        p.hass,
        SimpleNamespace(
            entry_id=p.entry_id, options={"soc_speicher_aktuell": "sensor.soc"}
        ),
        c,
        p.battery,
    )
    count = len(peer.requests)
    try:
        await clone.load()
        assert clone.setting(t)["profile"] == "PV_SURPLUS"
        assert clone.setting(t)["soc_hysterese"] == 9
        assert clone.references["soc_speicher_aktuell"] == "sensor.soc"
        assert not clone.tasks
        assert not clone.pv_ongoing
        assert len(peer.requests) == count
    finally:
        await clone.close()


async def test_new_session_requires_full_soc_start_rule(grid, monkeypatch):
    p, t, (c, _, _, *_) = grid
    measurements(p, t, soc=96)
    p.pv_request(t)
    p.pv_ongoing[t] = True
    measurements(p, t, soc=93)
    assert p.pv_request(t)[0] == 6000
    original = c.runtime.sessions.get
    session = original(t)
    monkeypatch.setattr(
        c.runtime.sessions,
        "get",
        lambda scope: (
            replace(session, session_id="new-session")
            if scope == t
            else original(scope)
        ),
    )
    assert p.pv_request(t)[0] == 0
    assert not p.pv_ongoing[t]
