"""Reserve arithmetic, durable continuation and external ownership boundaries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_control_runtime import manual as manual
from test_grid_profiles import battery as battery
from test_grid_profiles import grid as grid
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.battery import BatteryReserve
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)


@pytest.mark.parametrize(
    "soc,step,expected",
    [
        (65, 1, 40),
        (37.8, 1, 37),
        (37.8, 2, 36),
        (37.8, 0.5, 37.5),
        (20, 1, None),
        (15, 1, None),
    ],
)
async def test_bounded_downsized_reserve(battery, soc, step, expected):
    reserve, hass, calls, _ = battery
    hass.states.async_set("number.reserve", "20", {"step": step})
    hass.states.async_set("sensor.soc", str(soc))
    for _ in range(5):
        await reserve.update(40)
    assert calls == ([] if expected is None else [expected])
    await reserve.update(None)
    assert calls == ([] if expected is None else [expected, 20])


async def test_preserved_journal_waits_then_resumes_without_reserve_oscillation(
    battery,
):
    reserve, hass, calls, entry = battery
    hass.states.async_set("number.reserve", "20")
    await reserve.update(40)
    restored = BatteryReserve(hass, entry)
    await restored.load(preserve=True)
    assert calls == [40]
    assert restored.record["original"] == 20
    restored.resume()
    await restored.update(40)
    assert calls == [40]
    await restored.update(None)
    assert calls == [40, 20]


@pytest.mark.parametrize("external", [20, 35, 50])
async def test_external_change_is_never_reasserted_or_restored(battery, external):
    reserve, hass, calls, _ = battery
    hass.states.async_set("number.reserve", "20")
    await reserve.update(40)
    hass.states.async_set("number.reserve", str(external))
    for _ in range(3):
        await reserve.update(45)
    await reserve.update(None)
    assert calls == [40]
    assert hass.states.get("number.reserve").state == str(external)


async def test_profile_reserve_change_updates_owned_override(battery):
    reserve, hass, calls, _ = battery
    await reserve.update(40)
    await reserve.update(50)
    await reserve.update(50)
    await reserve.update(5)
    assert calls == [40, 50, 10]
    await reserve.update(None)
    assert calls == [40, 50, 10]  # Already restored by the lower request.


@pytest.mark.parametrize(
    "entity,value",
    [
        ("sensor.soc", "unavailable"),
        ("sensor.soc", "nan"),
        ("number.reserve", "unknown"),
    ],
)
async def test_invalid_battery_evidence_cannot_write(battery, entity, value):
    reserve, hass, calls, _ = battery
    hass.states.async_set(entity, value)
    await reserve.update(40)
    assert not calls


async def test_old_soc_cannot_write(battery):
    reserve, hass, calls, _ = battery
    hass.states.get("sensor.soc").last_reported = datetime.now(UTC) - timedelta(
        minutes=3
    )
    await reserve.update(40)
    assert not calls


@pytest.mark.parametrize("parent", [False, True])
async def test_transaction_event_power_drives_reserve_without_normal_meter_channel(
    grid, parent
):
    profile, target, (control, bound, _, *_) = grid
    await profile.permission(target, True)
    profile.battery.update = AsyncMock()
    now = datetime.now(UTC)
    control.runtime.observe(
        bound.token,
        (
            Observation(
                Channel(target.evse if parent else target, Quantity.CHARGING_STATE),
                State.CHARGING,
                now,
                now,
                None,
                "test",
            ),
        ),
    )
    # TransactionEvent samples are intentionally kept in the session ledger.
    control.runtime.sessions._remember(
        (
            Observation(
                Channel(target, Quantity.POWER),
                4000,
                now,
                now,
                now + timedelta(seconds=60),
                "ocpp2.1:TransactionEvent",
            ),
        ),
        transaction=True,
    )
    assert (
        control.runtime.get(target.station).observation(Channel(target, Quantity.POWER))
        is None
    )
    await profile.reconcile_battery()
    profile.battery.update.assert_awaited_with(20)
    profile.setting(target).update(
        profile="PV_SURPLUS", min_soc=40, soll_soc_speicher=41, soc_hysterese=5
    )
    await profile.reconcile_battery()
    profile.battery.update.assert_awaited_with(40)


async def test_reload_external_change_is_not_reasserted(battery):
    reserve, hass, calls, entry = battery
    await reserve.update(40)
    restored = BatteryReserve(hass, entry)
    await restored.load(preserve=True)
    hass.states.async_set("number.reserve", "35")
    restored.resume()
    await restored.update(40)
    await restored.update(None)
    assert calls == [40]


async def test_failed_adjustment_keeps_old_override_restorable(battery):
    reserve, _, calls, _ = battery
    await reserve.update(40)
    write = reserve.write
    reserve.write = AsyncMock(side_effect=ValueError("offline"))
    await reserve.update(50)
    await reserve.update(50)
    assert reserve.write.await_count == 1
    reserve.write = write
    await reserve.update(None)
    assert calls == [40, 10]


@pytest.mark.parametrize("entity", ["number.reserve", "sensor.soc"])
async def test_explicitly_expired_evidence_cannot_write(battery, entity):
    reserve, hass, calls, _ = battery
    hass.states.async_set(
        entity,
        "30",
        {"valid_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    await reserve.update(40)
    assert not calls
