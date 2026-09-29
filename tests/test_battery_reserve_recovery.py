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
    profile.battery.update.assert_awaited_with(None)


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


async def test_delayed_confirmation_waits_for_state_event(battery):
    import asyncio

    reserve, hass, calls, _ = battery
    dispatched = asyncio.Event()

    async def write(entity, value):
        calls.append(value)
        dispatched.set()

    reserve.write = write
    task = asyncio.create_task(reserve.update(40))
    await dispatched.wait()
    assert reserve.status == "confirmation_pending"
    assert not task.done()
    hass.states.async_set("number.reserve", "40")
    await task
    assert reserve.status == "active"
    assert reserve.last_error is None
    assert calls == [40]


async def test_confirmation_timeout_and_late_success(battery, monkeypatch):
    reserve, hass, _, _ = battery
    monkeypatch.setattr(
        "custom_components.wallbox_manager.battery.CONFIRMATION_TIMEOUT", 0.01
    )
    reserve.write = AsyncMock()
    await reserve.update(40)
    assert reserve.status == "write_unconfirmed"
    for _ in range(3):
        await reserve.update(40)
    assert reserve.status == "write_unconfirmed"
    reserve.write.assert_awaited_once()
    hass.states.async_set("number.reserve", "40")
    await reserve.update(40)
    assert reserve.status == "active"
    assert "reason" not in reserve.diagnostics


async def test_service_error_then_authoritative_success_clears_error(battery):
    reserve, hass, _, _ = battery
    reserve.write = AsyncMock(side_effect=ValueError("transient"))
    await reserve.update(40)
    assert reserve.status == "error"
    hass.states.async_set("number.reserve", "40")
    await reserve.update(40)
    assert reserve.status == "active"
    assert reserve.last_error is None
    assert "reason" not in reserve.diagnostics


async def test_superseded_adjustment_cannot_confirm_new_target(battery, monkeypatch):
    reserve, hass, calls, _ = battery
    monkeypatch.setattr(
        "custom_components.wallbox_manager.battery.CONFIRMATION_TIMEOUT", 0.01
    )
    await reserve.update(40)
    reserve.write = AsyncMock()
    await reserve.update(50)
    assert reserve.status == "write_unconfirmed"
    await reserve.update(60)
    assert reserve.record["pending"] == 60
    hass.states.async_set("number.reserve", "50")
    await reserve.update(60)
    assert reserve.status == "write_unconfirmed"
    assert reserve.record["pending"] == 60
    hass.states.async_set("number.reserve", "60")
    await reserve.update(60)
    assert reserve.status == "active"
    assert reserve.record["temporary"] == 60
    assert "pending" not in reserve.record
    assert calls == [40]


async def test_delayed_restoration_and_late_timeout_recovery(battery, monkeypatch):
    reserve, hass, calls, _ = battery
    monkeypatch.setattr(
        "custom_components.wallbox_manager.battery.CONFIRMATION_TIMEOUT", 0.01
    )
    await reserve.update(40)
    reserve.write = AsyncMock()
    await reserve.update(None)
    assert reserve.status == "write_unconfirmed"
    assert reserve.record is not None
    await reserve.update(None)
    reserve.write.assert_awaited_once()
    hass.states.async_set("number.reserve", "10")
    await reserve.update(None)
    assert reserve.status == "restored"
    assert reserve.record is None
    assert not reserve.failed_restore
    assert calls == [40]


async def test_pending_adjustment_survives_reload_without_reassertion(
    battery, monkeypatch
):
    reserve, hass, _, entry = battery
    monkeypatch.setattr(
        "custom_components.wallbox_manager.battery.CONFIRMATION_TIMEOUT", 0.01
    )
    await reserve.update(40)
    reserve.write = AsyncMock()
    await reserve.update(50)
    restored = BatteryReserve(hass, entry)
    await restored.load(preserve=True)
    restored.resume()
    restored.write = AsyncMock()
    await restored.update(50)
    restored.write.assert_not_awaited()
    assert restored.record["pending"] == 50
    hass.states.async_set("number.reserve", "50")
    await restored.update(50)
    assert restored.status == "active"
    assert restored.record["temporary"] == 50


async def test_pv_legacy_reserve_never_writes_or_creates_journal(grid, battery):
    profile, target, _ = grid
    reserve, _, calls, _ = battery
    profile.battery = reserve
    await profile.permission(target, True)
    profile.active = lambda _: True
    profile.setting(target).update(profile="PV_SURPLUS", min_soc=80)
    await profile.reconcile_battery()
    assert calls == []
    assert reserve.record is None
    assert await reserve.store.async_load() is None
    assert not profile.attributes(target)["battery_reserve_configured"]


async def test_pending_external_change_still_wins(battery, monkeypatch):
    reserve, hass, _, _ = battery
    monkeypatch.setattr(
        "custom_components.wallbox_manager.battery.CONFIRMATION_TIMEOUT", 0.01
    )
    await reserve.update(40)
    reserve.write = AsyncMock()
    await reserve.update(50)
    hass.states.async_set("number.reserve", "35")
    await reserve.update(50)
    assert reserve.status == "external_change"
    assert reserve.record is None
    await reserve.update(None)
    reserve.write.assert_awaited_once()


async def test_delayed_confirmation_of_restoration(battery):
    import asyncio

    reserve, hass, _, _ = battery
    await reserve.update(40)
    dispatched = asyncio.Event()

    async def write(entity, value):
        assert value == 10
        dispatched.set()

    reserve.write = write
    task = asyncio.create_task(reserve.update(None))
    await dispatched.wait()
    assert reserve.status == "confirmation_pending"
    hass.states.async_set("number.reserve", "10")
    await task
    assert reserve.status == "restored"
    assert reserve.record is None


async def test_late_success_automatically_reconciles_through_profile_event(grid):
    from types import SimpleNamespace

    profile, target, _ = grid
    hass = profile.hass
    hass.states.async_set("number.reserve", "10")
    hass.states.async_set("sensor.soc", "60")
    reserve = BatteryReserve(
        hass,
        SimpleNamespace(
            entry_id="late",
            options={
                "min_soc_speicher": "number.reserve",
                "soc_speicher_aktuell": "sensor.soc",
            },
        ),
    )
    profile.battery = reserve
    profile.active = lambda _: True
    profile.setting(target)["min_soc"] = 40
    reserve.write = AsyncMock(side_effect=ValueError("transient"))
    await profile.permission(target, True)
    await profile.reconcile_battery()
    assert reserve.status == "error"
    hass.states.async_set("number.reserve", "40")
    await hass.async_block_till_done()
    if profile.battery_task:
        await profile.battery_task
    assert reserve.status == "active"
    assert reserve.last_error is None


@pytest.mark.parametrize("enabled", [False, True])
async def test_reserve_diagnostics_are_opt_in_and_transition_level(
    battery, caplog, enabled
):
    import logging

    reserve, _, _, entry = battery
    entry.options["pv_diagnostic_logging"] = enabled
    caplog.set_level(logging.INFO)
    await reserve.update(40)
    events = [
        r.message for r in caplog.records if "subsystem=battery_reserve" in r.message
    ]
    assert bool(events) is enabled
    if enabled:
        assert any('"stage":"write_requested"' in e for e in events)
        assert any('"stage":"confirmation_pending"' in e for e in events)
        assert any('"stage":"confirmed"' in e for e in events)
    count = len(caplog.records)
    await reserve.update(40)
    assert len(caplog.records) == count
