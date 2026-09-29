"""A dispatched regulation snapshot survives data changes, never control changes."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import prepare
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements
from test_pv_transient_gaps import running
from test_voltage_command_fence import voltage

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)


@pytest.mark.parametrize(
    "change", ["pv", "load", "session_power", "voltage", "soc", "expiry", "activity"]
)
async def test_regulator_confirms_snapshot_then_uses_new_measurements(grid, change):
    p, t, (c, _, peer, *_), clock, tick = await running(grid)
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(tick())
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        point = c.pending_points[t]
        assert point.current_a == 10 and point.mode.count == 1
        generation, epoch = c.intent(t).generation, p.epochs[t]
        if change == "pv":
            measurements(p, t, pv=1840, load=0, actual=0, soc=96)
        elif change == "load":
            measurements(p, t, pv=2300, load=460, actual=0, soc=96)
        elif change == "session_power":
            measurements(p, t, pv=2300, load=0, actual=460, soc=96)
        elif change == "voltage":
            voltage(c, t, 220)
        elif change == "soc":
            measurements(p, t, pv=2300, load=0, actual=0, soc=89)
        elif change == "expiry":
            p.hass.states.async_set(
                "sensor.pv",
                2300,
                {
                    "unit_of_measurement": "W",
                    "valid_until": (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat(),
                },
            )
        else:
            now = datetime.now(UTC)
            c.runtime.observe(
                c.runtime.get(t.station).token,
                (
                    Observation(
                        Channel(t, Quantity.CHARGING_STATE),
                        State.CHARGING,
                        now,
                        now,
                        now + timedelta(seconds=60),
                        "test",
                    ),
                    Observation(
                        Channel(t, Quantity.POWER),
                        1000,
                        now,
                        now,
                        now + timedelta(seconds=60),
                        "test",
                    ),
                ),
            )
        await asyncio.sleep(0)
        assert c.intent(t).generation == generation and p.epochs[t] == epoch
        assert c.pending_points[t] == point
    finally:
        peer.release.set()
        await pending
    assert c.confirmed_point(t) == point
    assert c.intent(t).command_result.status == CommandStatus.APPLIED
    assert c.intent(t).fence_reason is None
    assert t not in c._unconfirmed_targets and t not in p.pv_retry_until
    count = len(peer.requests)
    await tick()
    if change == "soc":
        assert p.status[t] == "pv_stop_delay" and c.confirmed_point(t) == point
        clock[0] = 90
        await tick()
        assert not c.confirmed_point(t).charging
    elif change in ("expiry", "activity"):
        assert c.confirmed_point(t) == point and len(peer.requests) == count
    else:
        assert (
            c.confirmed_point(t).current_a
            == {
                "pv": 8,
                "load": 8,
                "session_power": 12,
                "voltage": 11,
            }[change]
        )
        assert len(peer.requests) == count + 1


@pytest.mark.parametrize(
    "change",
    [
        "authority",
        "off",
        "profile",
        "connection",
        "capability",
        "limit",
        "ownership",
        "generation",
        "cancel",
    ],
)
async def test_live_control_changes_still_fence_dispatched_command(grid, change):
    p, t, (c, bound, peer, _, source, _), _, _ = await running(grid)
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    p.pv_edit(t)
    peer.received.clear()
    peer.release.clear()
    pending = asyncio.create_task(c.apply_stored(t))
    followup = None
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        point = c.pending_points[t]
        if change == "authority":
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
                ),
            )
        elif change == "off":
            followup = asyncio.create_task(p.permission(t, False))
        elif change == "profile":
            followup = asyncio.create_task(p.select(t, "NETZ"))
        elif change == "connection":
            c.runtime.disconnect(bound.token)
            c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
        elif change == "capability":
            source.snapshot = replace(
                source.snapshot,
                envelopes=tuple(
                    e for e in source.snapshot.envelopes if e.mode != point.mode
                ),
            )
        elif change == "limit":
            source.permitted = (CurrentLimit(point.mode, 0, 8, "hard_limit"),)
        elif change == "ownership":
            c.profile_permitted = lambda _: False
        elif change == "generation":
            c._edit(t, {"target_w": 0})
        else:
            pending.cancel()
        await asyncio.sleep(0)
    finally:
        peer.release.set()
        result = (await asyncio.gather(pending, return_exceptions=True))[0]
        if followup:
            await followup
    if change == "cancel":
        assert isinstance(result, asyncio.CancelledError)
    else:
        assert result.status != CommandStatus.APPLIED
    assert c.confirmed_point(t) != point
    assert t not in c.pending_points


@pytest.mark.parametrize("change", ["pv", "voltage", "soc"])
async def test_invalidated_snapshot_before_wire_dispatch_is_still_rejected(
    grid, change
):
    p, t, (c, bound, peer, *_), _, _ = await running(grid)
    measurements(p, t, pv=2300, load=0, actual=0, soc=96)
    p.pv_edit(t)
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(c.apply_stored(t))
        await asyncio.sleep(0)
        if change == "pv":
            p.hass.states.async_set("sensor.pv", "unavailable")
        elif change == "voltage":
            voltage(c, t, 0, invalid="missing")
        else:
            p.setting(t)["pv_stop_delay"] = 0
            measurements(p, t, pv=2300, load=0, actual=0, soc=89)
        await asyncio.sleep(0)
    result = await pending
    assert result.reason == CommandReason.STALE
    assert c.intent(t).fence_reason.startswith("pre_dispatch_")
    assert len(peer.requests) == count


async def test_initial_enable_keeps_snapshot_through_permission_confirmation(grid):
    p, t, (c, _, peer, *_), _ = await prepare(grid, soc=96)
    p.setting(t)["pv_stop_delay"] = 90
    measurements(p, t, pv=2070, load=0, actual=0, soc=96)

    def response(_):
        measurements(p, t, pv=0, load=0, actual=0, soc=89)
        voltage(c, t, 228)
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    assert c.runtime.enabled(t) is True
    assert c.confirmed_point(t).current_a == 9
    assert c.confirmed_point(t).phase_voltages_v == (230,)
    assert p.pv_ongoing[t] and c.intent(t).fence_reason is None
    assert not p.pv_startups and t not in p.pv_retry_until
    assert t not in c.pending_points
    await asyncio.sleep(0)
    assert p.status[t] == "pv_stop_delay"
    assert c.confirmed_point(t).current_a == 9
