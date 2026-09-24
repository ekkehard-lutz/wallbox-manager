"""Grid orchestration exercises real solver/primitive controls and durable reserve."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.battery import BatteryReserve
from custom_components.wallbox_manager.config_flow import migrate_options
from custom_components.wallbox_manager.control.reference import ConfiguredReference
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
)
from custom_components.wallbox_manager.profiles import GridProfiles


@pytest.fixture
async def grid(tmp_path, manual):
    hass = HomeAssistant(str(tmp_path))
    control, bound, *_ = manual
    entry = SimpleNamespace(entry_id="grid", options={})
    battery = BatteryReserve(hass, entry)
    profiles = GridProfiles(hass, entry, control, battery)
    await profiles.load()
    control.profiles = profiles
    try:
        yield profiles, bound.target, manual
    finally:
        await profiles.close()
        await hass.async_stop()


async def test_settings_permission_selection_and_storage(grid):
    profile, target, (control, _, peer, *_) = grid
    await profile.set_value(target, "power_kw", 5.5)
    await profile.permission(target, True)
    assert control.runtime.enabled(target)
    assert control.intent(target).request.target_w == 5500
    count = len(peer.requests)
    await profile.set_value(target, "power_kw", 7)
    assert len(peer.requests) > count
    await profile.select(target, "NETZ")
    assert not control.runtime.enabled(target)
    assert target not in profile.tasks
    assert (await profile.store.async_load())[next(iter(profile.settings))][
        "power_kw"
    ] == 7


@pytest.mark.parametrize("phases", [2, 3])
async def test_multiphase_detection_obeys_single_phase_limit(grid, phases):
    profile, target, (control, bound, peer, _, source, _) = grid
    source.snapshot = replace(
        source.snapshot,
        envelopes=tuple(
            replace(e, max_current_a=20 if e.mode.count == 1 else 32)
            for e in source.snapshot.envelopes
        ),
    )
    # Restrict the initial sequence to the supported multiphase point.
    if phases == 2:
        from custom_components.wallbox_manager.core.models import PhaseMode

        source.snapshot = replace(
            source.snapshot,
            envelopes=(
                source.snapshot.envelopes[0],
                replace(source.snapshot.envelopes[-1], mode=PhaseMode.canonical(2)),
            ),
        )
    await profile.set_value(target, "power_kw", 22)

    async def wait(seconds):
        assert seconds == 60
        now = datetime.now(UTC)
        control.runtime.observe(
            bound.token,
            tuple(
                Observation(
                    Channel(target, Quantity(f"current_l{n}")),
                    10 if n == 1 else 0,
                    now,
                    now,
                    now + timedelta(seconds=60),
                    "test",
                )
                for n in (1, 2, 3)
            ),
        )

    profile.wait = wait
    await profile.permission(target, True)
    await profile.tasks[target]
    point = control.intent(target).solver_result.point
    assert point.mode.count == 1
    assert point.current_a <= 20
    assert profile.status[target] == "complete"
    assert len(peer.requests) <= 3


async def test_stale_observation_does_not_dispatch(grid):
    profile, target, (control, _, peer, *_) = grid
    gate = asyncio.Event()
    profile.wait = lambda _: gate.wait()
    await profile.permission(target, True)
    task = profile.tasks[target]
    await asyncio.sleep(0)
    await profile.permission(target, False)
    count = len(peer.requests)
    gate.set()
    await asyncio.gather(task, return_exceptions=True)
    assert len(peer.requests) == count


@pytest.mark.parametrize(
    "options,configured",
    [
        ({}, False),
        ({"min_soc_speicher": "number.reserve"}, False),
        ({"soc_speicher_aktuell": "sensor.soc"}, False),
        (
            {
                "min_soc_speicher": "number.reserve",
                "soc_speicher_aktuell": "sensor.soc",
            },
            True,
        ),
    ],
)
async def test_optional_battery(tmp_path, options, configured):
    hass = HomeAssistant(str(tmp_path))
    battery = BatteryReserve(hass, SimpleNamespace(entry_id="test", options=options))
    assert battery.configured is configured
    await hass.async_stop()


@pytest.fixture
async def battery(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="battery",
        options={
            "min_soc_speicher": "number.reserve",
            "soc_speicher_aktuell": "sensor.soc",
        },
    )
    hass.states.async_set("number.reserve", "10", {"min": 0, "max": 100})
    hass.states.async_set("sensor.soc", "60")
    calls = []

    async def write(call):
        calls.append(call.data["value"])
        hass.states.async_set(
            "number.reserve", str(call.data["value"]), {"min": 0, "max": 100}
        )

    hass.services.async_register("number", "set_value", write)
    reserve = BatteryReserve(hass, entry)
    try:
        yield reserve, hass, calls, entry
    finally:
        await hass.async_stop()


async def test_battery_sessions_conflict_and_restart(battery):
    reserve, hass, calls, entry = battery
    await reserve.update(80)
    assert calls == [60]
    await reserve.update(80)
    assert calls == [60]
    await reserve.update(None)
    assert calls == [60, 10]
    hass.states.async_set("number.reserve", "15")
    await reserve.update(40)
    assert reserve.record["original"] == 15
    recovered = BatteryReserve(hass, entry)
    await recovered.load()
    assert calls == [60, 10, 40, 15]
    await recovered.update(50)
    hass.states.async_set("number.reserve", "35")
    await recovered.update(None)
    assert calls[-1] == 50 and recovered.status == "external_change"
    assert recovered.record is None


async def test_battery_high_reserve_and_failed_write(battery):
    reserve, hass, calls, _ = battery
    await reserve.update(5)
    assert calls == []
    await reserve.update(None)
    reserve.write = AsyncMock(side_effect=ValueError("offline"))
    await reserve.update(50)
    for _ in range(4):
        await reserve.update(50)
    assert reserve.write.await_count == 1
    assert reserve.status == "error"


async def test_failed_restore_retains_record_without_write_storm(battery):
    reserve, _, _, _ = battery
    await reserve.update(50)
    reserve.write = AsyncMock(side_effect=ValueError("offline"))
    for _ in range(4):
        await reserve.update(None)
    assert reserve.write.await_count == 1
    assert reserve.record["original"] == 10


def test_station_migration_isolation_and_ambiguous_preservation():
    old = {
        "reference_station_id": "A",
        "reference_evse_id": 1,
        "reference_connector_id": 1,
        "reference_min_a": "6",
    }
    migrated = migrate_options(old)
    source = ConfiguredReference(migrated)
    target = ConnectorId(EvseId(StationId("A"), "1"), "1")
    other = ConnectorId(EvseId(StationId("B"), "1"), "1")
    assert source.capabilities(target, datetime.now(UTC))
    assert not source.capabilities(other, datetime.now(UTC))
    assert migrate_options({"reference_min_a": "6"}) == {
        "unassigned_references": {"reference_min_a": "6"}
    }
    assert migrate_options(migrated) == migrated


async def test_phase_retry_then_observation(grid):
    from test_phase_lockout import setup

    profile, target, manual = grid
    control, bound, peer, source, locked, _ = await setup(manual)
    profile.setting(target)["power_kw"] = 1.7
    waits = []

    async def wait(seconds):
        waits.append((seconds, profile.status[target]))
        locked[0] = False

    profile.wait = wait
    await profile.permission(target, True)
    await profile.tasks[target]
    assert waits == [(60, "phase_lockout"), (60, "observing")]
    assert control.intent(target).solver_result.point.mode.count == 1


async def test_single_to_multi_and_back_is_bounded(grid):
    profile, target, (control, bound, peer, _, source, _) = grid
    await profile.set_value(target, "power_kw", 3.7)
    seen = []

    async def wait(seconds):
        point = control.intent(target).solver_result.point
        seen.append(point.mode.count)
        now = datetime.now(UTC)
        control.runtime.observe(
            bound.token,
            tuple(
                Observation(
                    Channel(target, Quantity(f"current_l{n}")),
                    6 if n == 1 else 0,
                    now,
                    now,
                    now + timedelta(seconds=60),
                    "test",
                )
                for n in (1, 2, 3)
            ),
        )

    profile.wait = wait
    await profile.permission(target, True)
    await profile.tasks[target]
    assert seen == [1, 3, 1]
    assert len(peer.requests) == 3
    assert control.intent(target).solver_result.point.mode.count == 1


async def test_new_power_supersedes_sleeping_observation(grid):
    profile, target, (control, _, peer, *_) = grid
    gate = asyncio.Event()
    profile.wait = lambda _: gate.wait()
    await profile.permission(target, True)
    old = profile.tasks[target]
    await asyncio.sleep(0)
    await profile.set_value(target, "power_kw", 2)
    assert old.cancelling()
    count = len(peer.requests)
    gate.set()
    await asyncio.gather(old, profile.tasks[target], return_exceptions=True)
    assert len(peer.requests) == count
    assert control.intent(target).request.target_w == 2000


async def test_local_never_acquired(grid):
    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    profile, target, (control, bound, peer, *_) = grid
    control.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            target.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    await profile.set_value(target, "power_kw", 5)
    await profile.permission(target, True)
    assert not peer.requests
    assert target not in profile.tasks
    assert control.intent(target).status == "no_authority"


@pytest.mark.parametrize("event", ["disable", "profile", "disconnect", "finish"])
async def test_battery_lifecycle_events(grid, event):
    profile, target, (control, bound, _, *_) = grid
    profile.battery.update = AsyncMock()
    profile.active = lambda t: True
    await profile.permission(target, True)
    await profile.reconcile_battery()
    profile.battery.update.assert_awaited_with(20)
    if event == "disable":
        await profile.permission(target, False)
    elif event == "profile":
        await profile.select(target, "NETZ")
    else:
        profile.active = lambda t: False
        await profile.reconcile_battery()
    profile.battery.update.assert_awaited_with(None)
    if event in ("disconnect", "finish"):
        profile.active = lambda t: True
        await profile.reconcile_battery()
        profile.battery.update.assert_awaited_with(20)


async def test_unavailable_restart_cannot_replace_original_journal(battery):
    reserve, hass, calls, entry = battery
    await reserve.update(50)
    hass.states.async_set("number.reserve", "unavailable")
    recovered = BatteryReserve(hass, entry)
    await recovered.load()
    assert recovered.record["original"] == 10
    hass.states.async_set("number.reserve", "50")
    await recovered.update(70)
    assert calls == [50, 10]
    assert recovered.record is None


async def test_unconfirmed_restore_keeps_journal(battery):
    reserve, _, _, _ = battery
    await reserve.update(50)
    reserve.write = AsyncMock()
    await reserve.update(None)
    assert reserve.record["original"] == 10
    assert reserve.status == "error"


async def test_actual_metered_charging_finish_and_resume(grid):
    from custom_components.wallbox_manager.core.telemetry import State

    profile, target, (control, bound, _, *_) = grid
    profile.battery.update = AsyncMock()
    await profile.permission(target, True)
    assert not profile.active(target)
    for charging, expected in (
        (State.CHARGING, 20),
        (State.FINISHING, None),
        (State.CHARGING, 20),
    ):
        now = datetime.now(UTC)
        control.runtime.observe(
            bound.token,
            (
                Observation(
                    Channel(target, Quantity.CHARGING_STATE),
                    charging,
                    now,
                    now,
                    None,
                    "test",
                ),
                Observation(
                    Channel(target, Quantity.POWER),
                    4000,
                    now,
                    now,
                    now + timedelta(seconds=60),
                    "test",
                ),
            ),
        )
        await profile.reconcile_battery()
        profile.battery.update.assert_awaited_with(expected)
    control.runtime.disconnect(bound.token)
    await profile.reconcile_battery()
    profile.battery.update.assert_awaited_with(None)
