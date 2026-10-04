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


@pytest.mark.parametrize(
    "change", ["pv", "load", "pv_down", "load_up", "soc", "voltage"]
)
async def test_startup_snapshot_survives_safe_measurement_before_dispatch(
    grid, monkeypatch, change
):
    p, t, (c, bound, peer, *_), _ = await prepare(grid, soc=96)
    measurements(p, t, pv=2530, load=230, actual=0, soc=96)
    assert p.setting(t)["profile"] == "PV_SURPLUS"
    assert c.runtime.enabled(t) is False and c.confirmed_point(t) is None
    assert c.runtime.authority(t.station) == ControlAuthority.REMOTE
    assert c.runtime.get(t.station).connected and p.can_control(t)
    import custom_components.wallbox_manager.control.runtime as module

    prepared = asyncio.Event()
    real_apply = module.apply_operating_point

    async def apply(*args, **kwargs):
        prepared.set()
        return await real_apply(*args, **kwargs)

    monkeypatch.setattr(module, "apply_operating_point", apply)
    # Inspect startup confirmation before the newly launched regulation cycle.
    launch = p.launch
    monkeypatch.setattr(p, "launch", lambda _: None)
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(p.permission(t, True))
        await asyncio.wait_for(prepared.wait(), 1)
        point = c.pending_points[t]
        assert (point.mode.count, point.current_a) == (1, 10)
        assert len(peer.requests) == count
        if change == "pv":
            measurements(p, t, pv=2760, load=230, actual=0, soc=96)
        elif change == "load":
            measurements(p, t, pv=2530, load=0, actual=0, soc=96)
        elif change == "pv_down":
            measurements(p, t, pv=2300, load=230, actual=0, soc=96)
        elif change == "load_up":
            measurements(p, t, pv=2530, load=460, actual=0, soc=96)
        elif change == "soc":
            measurements(p, t, pv=2530, load=230, actual=0, soc=97)
        else:
            voltage(c, t, 231)
    result = await pending
    assert result.status == CommandStatus.APPLIED, c.intent(t).fence_reason
    assert c.runtime.enabled(t) is True and c.confirmed_point(t) == point
    assert c.intent(t).fence_reason is None
    assert not p.pv_startups and t not in p.pv_retry_until
    assert len(peer.requests) == count + 1

    parked = asyncio.Event()

    async def wait(_):
        parked.set()
        await asyncio.Event().wait()

    p.wait = wait
    launch(t)
    await asyncio.wait_for(parked.wait(), 1)
    assert (
        c.confirmed_point(t).current_a
        == {"pv": 11, "load": 11, "pv_down": 9, "load_up": 9, "soc": 10, "voltage": 10}[
            change
        ]
    )
    if change == "voltage":
        assert c.confirmed_point(t).phase_voltages_v == (231,)
    assert len(peer.requests) <= count + 2
    assert not p.pv_startups and t not in p.pv_retry_until


@pytest.mark.parametrize(
    "change",
    [
        "authority",
        "connection",
        "off",
        "profile",
        "ownership",
        "generation",
        "capability",
        "limit",
        "transaction",
    ],
)
async def test_startup_live_fences_reject_before_wire_dispatch(
    grid, monkeypatch, change
):
    p, t, (c, bound, peer, _, source, _), _ = await prepare(grid, soc=96)
    measurements(p, t, pv=2530, load=0, actual=0, soc=96)
    import custom_components.wallbox_manager.control.runtime as module

    prepared = asyncio.Event()
    real_apply = module.apply_operating_point

    async def apply(*args, **kwargs):
        prepared.set()
        return await real_apply(*args, **kwargs)

    monkeypatch.setattr(module, "apply_operating_point", apply)
    count = len(peer.requests)
    followup = None
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(p.permission(t, True))
        await asyncio.wait_for(prepared.wait(), 1)
        point = c.pending_points[t]
        if change == "authority":
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
                ),
            )
        elif change == "connection":
            c.runtime.disconnect(bound.token)
            c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
        elif change == "off":
            followup = asyncio.create_task(p.permission(t, False))
            await asyncio.sleep(0)
        elif change == "profile":
            followup = asyncio.create_task(p.select(t, "NETZ"))
            await asyncio.sleep(0)
        elif change == "ownership":
            c.profile_permitted = lambda _: False
        elif change == "generation":
            c._edit(t, {"target_w": 0})
        elif change == "capability":
            source.snapshot = replace(
                source.snapshot, revision=source.snapshot.revision + 1
            )
        elif change == "limit":
            source.permitted = (CurrentLimit(point.mode, 0, 8, "hard_limit"),)
        else:
            inputs = c.inputs
            monkeypatch.setattr(
                c,
                "inputs",
                lambda target: replace(inputs(target), transaction_id="new"),
            )
    result = await pending
    if followup:
        await followup
    assert result.status != CommandStatus.APPLIED
    assert c.confirmed_point(t) is None
    assert c.runtime.enabled(t) is not True
    assert len(peer.requests) == count


@pytest.mark.parametrize("amps", [13, 11])
@pytest.mark.parametrize("change", ["pv", "load", "session_power", "voltage", "soc"])
async def test_regulator_snapshot_survives_measurement_before_dispatch(
    grid, monkeypatch, amps, change
):
    p, t, (c, bound, peer, *_), _, tick = await running(grid)
    measurements(p, t, pv=2760, load=0, actual=0, soc=96)
    await tick()
    assert c.confirmed_point(t).current_a == 12
    measurements(p, t, pv=amps * 230, load=0, actual=0, soc=96)
    import custom_components.wallbox_manager.control.runtime as module

    prepared = asyncio.Event()
    real_apply = module.apply_operating_point

    async def apply(*args, **kwargs):
        prepared.set()
        return await real_apply(*args, **kwargs)

    monkeypatch.setattr(module, "apply_operating_point", apply)
    count = len(peer.requests)
    # Unlike the acknowledgement test above, change samples BEFORE the wire
    # call: peer.received would already be past the faulty caller fence.
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(tick())
        await asyncio.wait_for(prepared.wait(), 1)
        point = c.pending_points[t]
        assert (point.mode.count, point.current_a) == (1, amps)
        request = p.pv_request(t)
        generation, epoch = c.intent(t).generation, p.epochs[t]
        assert len(peer.requests) == count
        if change == "pv":
            measurements(p, t, pv=(amps + 1) * 230, load=0, actual=0, soc=96)
        elif change == "load":
            measurements(p, t, pv=amps * 230, load=230, actual=0, soc=96)
        elif change == "session_power":
            measurements(p, t, pv=amps * 230, load=0, actual=230, soc=96)
        elif change == "voltage":
            voltage(c, t, 231)
        else:
            measurements(p, t, pv=amps * 230, load=0, actual=0, soc=97)
        if change in ("pv", "load", "session_power"):
            assert p.pv_request(t) != request
        assert c.intent(t).generation == generation and p.epochs[t] == epoch
    await pending
    assert c.intent(t).command_result.status == CommandStatus.APPLIED, c.intent(
        t
    ).fence_reason
    assert c.intent(t).fence_reason is None
    assert c.confirmed_point(t) == point
    assert len(peer.requests) == count + 1
    assert t not in p.pv_retry_until
    await tick()
    expected = amps + {"pv": 1, "load": -1, "session_power": 1}.get(change, 0)
    assert c.confirmed_point(t).current_a == expected
    if change == "voltage":
        assert c.confirmed_point(t).phase_voltages_v == (231,)
    assert t not in p.pv_retry_until
    count = len(peer.requests)
    await tick()
    assert len(peer.requests) == count


@pytest.mark.parametrize(
    "change",
    [
        "epoch",
        "closed",
        "profile",
        "authority",
        "ownership",
        "connection",
        "permission",
        "limit",
        "capability",
        "transaction",
        "adapter",
        "off",
        "intent",
    ],
)
async def test_regulator_live_changes_fence_before_dispatch(grid, monkeypatch, change):
    p, t, (c, bound, peer, _, source, _), _, tick = await running(grid)
    measurements(p, t, pv=2990, load=0, actual=0, soc=96)
    import custom_components.wallbox_manager.control.runtime as module

    prepared, completed = asyncio.Event(), asyncio.Event()
    real_apply = module.apply_operating_point
    outcomes = []

    async def apply(*args, **kwargs):
        prepared.set()
        try:
            result = await real_apply(*args, **kwargs)
            outcomes.append(result)
            return result
        finally:
            completed.set()

    monkeypatch.setattr(module, "apply_operating_point", apply)
    count = len(peer.requests)
    followup = None
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(tick())
        await asyncio.wait_for(prepared.wait(), 1)
        point = c.pending_points[t]
        assert point.current_a == 13
        if change == "epoch":
            p.invalidate(t)
        elif change == "closed":
            monkeypatch.setattr(p, "closed", True)
        elif change == "profile":
            followup = asyncio.create_task(p.select(t, "NETZ"))
            await asyncio.sleep(0)
        elif change == "authority":
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
                ),
            )
        elif change == "ownership":
            c.profile_permitted = lambda _: False
        elif change == "connection":
            c.runtime.disconnect(bound.token)
            c.runtime.connect(t.station, protocol="ocpp", protocol_version="2.1")
        elif change == "permission":
            c.runtime.observe_enabled(
                bound.token,
                replace(
                    c.runtime.enabled_observation(t),
                    enabled=False,
                    observed_at=datetime.now(UTC),
                ),
            )
        elif change == "limit":
            source.permitted = (CurrentLimit(point.mode, 0, 8, "hard_limit"),)
        elif change == "capability":
            source.snapshot = replace(
                source.snapshot, revision=source.snapshot.revision + 1
            )
        elif change == "transaction":
            inputs = c.inputs
            monkeypatch.setattr(
                c,
                "inputs",
                lambda target: replace(inputs(target), transaction_id="new"),
            )
        elif change == "adapter":
            monkeypatch.setattr(c, "blocker", lambda _: "adapter_unavailable")
        elif change == "off":
            followup = asyncio.create_task(p.permission(t, False))
            await asyncio.sleep(0)
        else:
            c._edit(t, {"target_w": 0})
    try:
        await asyncio.wait_for(completed.wait(), 1)
        if followup:
            await followup
        assert all(result.status != CommandStatus.APPLIED for result in outcomes)
        assert c.confirmed_point(t) != point
        assert len(peer.requests) == count
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
