"""beta.2 configuration, profile availability and asymmetric-delay contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import product
from unittest.mock import AsyncMock

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_profile_ownership import site as site
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.pv_surplus import PV_DEFAULTS
from custom_components.wallbox_manager.select import ChargingProfile


@pytest.mark.parametrize("pv,load,soc,reserve", list(product([False, True], repeat=4)))
async def test_backend_profile_options_depend_only_on_power_mappings(
    grid, pv, load, soc, reserve
):
    p, t, _ = grid
    p.references = {
        key: entity
        for key, entity, enabled in [
            ("leistung_pv", "sensor.pv", pv),
            ("leistung_verbraucher", "sensor.load", load),
            ("soc_speicher_aktuell", "sensor.soc", soc),
            ("min_soc_speicher", "number.reserve", reserve),
        ]
        if enabled
    }
    options = ChargingProfile(p.control, p.entry_id, t).options
    assert options == (["NETZ", "PV_SURPLUS"] if pv and load else ["NETZ"])
    if not (pv and load):
        with pytest.raises(ValueError):
            await p.select(t, "PV_SURPLUS")
    else:
        p.hass.states.async_set("sensor.pv", "unavailable")
        assert p.available_profiles(t) == options
        assert p.pv_plan(t)[0] == 0


@pytest.mark.parametrize(
    "authority",
    [ControlAuthority.LOCAL, ControlAuthority.UNKNOWN, ControlAuthority.REMOTE],
)
@pytest.mark.parametrize("profile", ["NETZ", "PV_SURPLUS"])
async def test_configuration_without_control_never_dispatches(grid, authority, profile):
    p, t, (c, bound, peer, *_) = grid
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(t.station, authority, datetime.now(UTC), "test"),
    )
    if authority == ControlAuthority.REMOTE:
        c.profile_permitted = lambda _: False
    count = len(peer.operations)
    await p.select(t, profile)
    for field, value in [
        ("power_kw", 5),
        ("min_soc", 30),
        ("soll_soc_speicher", 90),
        ("soc_hysterese", 7),
        ("regulation_interval", 8),
        ("pv_start_delay", 12),
        ("pv_stop_delay", 75),
        ("approximation", "up"),
    ]:
        await p.set_value(t, field, value)
        assert p.setting(t)[field] == value
    await p.permission(t, True)
    assert len(peer.operations) == count
    assert not p.tasks and not p.debounce_tasks
    p.settings.clear()
    await p.load()
    assert p.setting(t)["profile"] == profile
    assert p.setting(t)["pv_stop_delay"] == 75
    assert p.setting(t)["power_kw"] == 5


async def test_explicit_takeover_keeps_offline_configuration_and_permission_off(site):
    owner, peers, _ = site
    c, bound, peer, p, key = peers[0]
    p.references.update(leistung_pv="sensor.pv", leistung_verbraucher="sensor.load")
    await p.select(bound.target, "PV_SURPLUS")
    await p.set_value(bound.target, "pv_start_delay", 17)
    assert not peer.authority_requests
    assert (await owner.activate(key)).status.value == "applied"
    assert p.setting(bound.target)["profile"] == "PV_SURPLUS"
    assert p.setting(bound.target)["pv_start_delay"] == 17
    assert not c.runtime.enabled(bound.target)
    assert not p.tasks


async def prepare(grid, *, soc=None):
    p, t, manual = grid
    p.setting(t).update(PV_DEFAULTS)
    measurements(p, t, soc=soc)
    await p.select(t, "PV_SURPLUS")
    clock = [0.0]
    p.monotonic = lambda: clock[0]
    p.wait = lambda _: asyncio.Event().wait()
    p.debounce_wait = AsyncMock()
    return p, t, manual, clock


async def apply(p, t):
    result = p.pv_edit(t)
    command = await p.control.apply_stored(t, reuse_applied=True)
    p.pv_confirm(t)
    assert command.status.value == "applied"
    return result.point


async def test_default_zero_start_is_immediate_and_stop_default_is_sixty(grid):
    p, t, (c, _, _, *_), clock = await prepare(grid)
    await p.permission(t, True)
    assert c.confirmed_point(t).charging
    p.debounce_wait.assert_not_awaited()
    assert p.setting(t)["pv_start_delay"] == 0
    assert p.setting(t)["pv_stop_delay"] == 60
    measurements(p, t, pv=0)
    point = await apply(p, t)
    assert point.charging and point.current_a == 6
    assert p.status[t] == "pv_stop_delay"
    clock[0] = 59
    assert (await apply(p, t)).charging
    clock[0] = 60
    assert not (await apply(p, t)).charging
    assert c.runtime.enabled(t) is True


async def test_start_delay_requires_continuous_eligible_surplus(grid):
    p, t, (c, _, _, *_), clock = await prepare(grid, soc=96)
    await p.set_value(t, "pv_start_delay", 10)
    await p.permission(t, True)
    assert not c.confirmed_point(t).charging
    assert p.pv_start_since[t] == 0
    clock[0] = 9
    assert not (await apply(p, t)).charging
    measurements(p, t, pv=0, soc=96)
    await apply(p, t)
    assert t not in p.pv_start_since
    clock[0] = 10
    measurements(p, t, soc=96)
    assert not (await apply(p, t)).charging
    clock[0] = 20
    assert (await apply(p, t)).charging


async def test_surplus_recovery_resets_stop_delay_and_no_duplicate_minimum_commands(
    grid,
):
    p, t, (c, _, peer, *_), clock = await prepare(grid)
    await p.permission(t, True)
    measurements(p, t, pv=0)
    await apply(p, t)
    count = len(peer.requests)
    for seconds in (5, 10, 15):
        clock[0] = seconds
        await apply(p, t)
    assert len(peer.requests) == count
    measurements(p, t)
    await apply(p, t)
    assert t not in p.pv_stop_since
    clock[0] = 30
    measurements(p, t, pv=0)
    await apply(p, t)
    assert p.pv_stop_since[t] == 30
    clock[0] = 89
    assert (await apply(p, t)).charging
    clock[0] = 90
    assert not (await apply(p, t)).charging


@pytest.mark.parametrize(
    "safety", ["soc", "invalid", "stale", "explicit_off", "authority"]
)
async def test_safety_bypasses_stop_delay(grid, safety):
    p, t, (c, bound, peer, *_), clock = await prepare(grid, soc=96)
    await p.permission(t, True)
    measurements(p, t, pv=0, soc=96)
    await apply(p, t)
    assert p.status[t] == "pv_stop_delay"
    if safety == "explicit_off":
        await p.permission(t, False)
        assert c.runtime.enabled(t) is False
    elif safety == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
        count = len(peer.requests)
        clock[0] = 100
        await asyncio.sleep(0)
        assert len(peer.requests) == count and not p.tasks
    else:
        if safety == "soc":
            measurements(p, t, pv=0, soc=89)
        elif safety == "invalid":
            p.hass.states.async_set("sensor.pv", "unavailable")
        else:
            state = p.hass.states.get("sensor.pv")
            p.hass.states.async_set(
                "sensor.pv",
                state.state,
                {
                    **state.attributes,
                    "valid_until": (
                        datetime.now(UTC) - timedelta(seconds=1)
                    ).isoformat(),
                },
            )
        assert not (await apply(p, t)).charging
        assert c.runtime.enabled(t) is True
    assert not p.pv_ongoing.get(t, False)


async def test_removed_power_mapping_stops_before_fallback_and_keeps_settings(grid):
    p, t, (c, _, peer, *_), _ = await prepare(grid, soc=96)
    await p.set_value(t, "soc_hysterese", 7)
    await p.permission(t, True)
    p.references.pop("leistung_pv")
    assert not p.permits_point(t, c.confirmed_point(t))
    await p.reconcile_availability()
    assert c.runtime.enabled(t) is False
    assert p.setting(t)["profile"] == "NETZ"
    assert p.setting(t)["soc_hysterese"] == 7
    assert not p.tasks
    count = len(peer.requests)
    await asyncio.sleep(0)
    assert len(peer.requests) == count


async def test_removed_mapping_waits_for_confirmed_off_without_authority(grid):
    p, t, (c, bound, peer, *_), _ = await prepare(grid)
    await p.permission(t, True)
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    p.references.pop("leistung_verbraucher")
    count = len(peer.operations)
    await p.reconcile_availability()
    assert len(peer.operations) == count
    assert p.setting(t)["profile"] == "PV_SURPLUS"
    assert p.status[t] == "profile_unavailable"
    assert c.intent(t).request.target_w == 0


async def test_battery_mapping_visibility_does_not_erase_settings(grid):
    p, t, _ = grid
    p.references["soc_speicher_aktuell"] = "sensor.soc"
    await p.select(t, "PV_SURPLUS")
    await p.set_value(t, "soll_soc_speicher", 92)
    await p.set_value(t, "soc_hysterese", 8)
    assert p.attributes(t)["battery_configured"]
    p.references.pop("soc_speicher_aktuell")
    assert not p.attributes(t)["battery_configured"]
    assert p.available_profiles(t) == ["NETZ", "PV_SURPLUS"]
    await p.save()
    p.settings.clear()
    await p.load()
    assert p.setting(t)["soll_soc_speicher"] == 92
    assert p.setting(t)["soc_hysterese"] == 8


async def test_start_timer_fences_queued_command_and_minimum_stop_deadline(grid):
    p, t, (c, bound, peer, *_), clock = await prepare(grid, soc=96)
    p.setting(t)["pv_start_delay"] = 10
    await p.permission(t, True)
    clock[0] = 10
    assert p.pv_edit(t).point.charging
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(c.apply_stored(t))
        await asyncio.sleep(0)
        measurements(p, t, soc=95)
        await asyncio.sleep(0)
    result = await pending
    assert result.status.value != "applied"
    assert all(
        r[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for r in peer.requests[count:]
    )
    assert not p.pv_ongoing[t]


async def test_stop_delay_fences_queued_minimum_after_expiry(grid):
    p, t, (c, bound, peer, *_), clock = await prepare(grid)
    await p.permission(t, True)
    measurements(p, t, pv=0)
    p.pv_edit(t)
    count = len(peer.requests)
    async with bound.adapter._call_lock:
        pending = asyncio.create_task(c.apply_stored(t))
        await asyncio.sleep(0)
        clock[0] = 60
    result = await pending
    assert result.status.value != "applied"
    assert len(peer.requests) == count
    assert not (await apply(p, t)).charging


async def test_phase_lockout_retry_never_extends_pv_stop_delay(grid):
    from test_phase_lockout import setup

    p, t, manual = grid
    c, _, peer, _, _, _ = await setup(manual)
    p.setting(t).update(PV_DEFAULTS, profile="PV_SURPLUS", approximation="up")
    measurements(p, t, pv=1700, load=0, actual=0, soc=96)
    clock = [0.0]
    p.monotonic = lambda: clock[0]
    p.wait = lambda _: asyncio.Event().wait()
    await apply(p, t)
    assert c.intent(t).phase_retry
    measurements(p, t, pv=0, load=0, actual=0, soc=96)
    assert (await apply(p, t)).charging
    assert p.status[t] == "pv_stop_delay"
    clock[0] = 60
    assert not (await apply(p, t)).charging
    measurements(p, t, soc=95)
    assert not p.pv_edit(t).point.charging
    assert c.runtime.enabled(t) is True


async def test_fresh_measurement_wakes_zero_delay_start_without_periodic_tick(grid):
    p, t, (c, _, _, *_), _ = await prepare(grid)
    measurements(p, t, pv=0)
    await p.permission(t, True)
    await asyncio.sleep(0)
    assert not c.confirmed_point(t).charging
    done = asyncio.Event()

    def changed(_):
        point = c.confirmed_point(t)
        if point and point.charging:
            done.set()

    unsubscribe = c.subscribe(changed)
    try:
        measurements(p, t)
        await asyncio.wait_for(done.wait(), 1)
        p.debounce_wait.assert_not_awaited()
    finally:
        unsubscribe()


async def test_timer_wake_uses_earliest_policy_or_measurement_deadline(grid):
    p, t, (c, _, _, *_), clock = await prepare(grid)
    p.setting(t).update(pv_start_delay=10, regulation_interval=300)
    await p.permission(t, True)
    assert p.pv_wait_seconds(t) == 10
    clock[0] = 8
    assert p.pv_wait_seconds(t) == 2
    # Start delay longer than the freshness guarantee wakes to fail closed first.
    p.setting(t)["pv_start_delay"] = 200
    assert 0 < p.pv_wait_seconds(t) <= 90.001


@pytest.mark.parametrize("field", ["pv_start_delay", "pv_stop_delay"])
@pytest.mark.parametrize("value", [-1, 3601, float("nan")])
async def test_invalid_delay_values_rejected(grid, field, value):
    p, t, _ = grid
    with pytest.raises(ValueError):
        await p.set_value(t, field, value)


@pytest.mark.parametrize("action", ["off", "profile", "authority", "unload"])
async def test_pending_start_timer_is_cancelled_by_lifecycle_action(grid, action):
    p, t, (c, bound, peer, *_), clock = await prepare(grid, soc=96)
    p.setting(t)["pv_start_delay"] = 10
    await p.permission(t, True)
    assert t in p.pv_start_since
    if action == "off":
        await p.permission(t, False)
    elif action == "profile":
        await p.select(t, "NETZ")
    elif action == "authority":
        c.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
            ),
        )
    else:
        await p.close()
    count = len(peer.requests)
    clock[0] = 100
    await asyncio.sleep(0)
    assert len(peer.requests) == count
    assert t not in p.pv_start_since and t not in p.pv_stop_since
    assert not p.tasks


async def test_stop_delay_cannot_be_used_to_request_arbitrary_grid_power(grid):
    p, t, (c, _, peer, *_), _ = await prepare(grid)
    await p.permission(t, True)
    measurements(p, t, pv=0)
    await apply(p, t)
    count = len(peer.requests)
    result = await c.change(t, target_w=10000)
    assert result.status.value != "applied"
    assert len(peer.requests) == count
