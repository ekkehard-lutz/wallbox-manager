"""Persistent explicit NETZ approval through HA entities and real OCPP peers."""

import asyncio
import json

import pytest
from conftest import config, entities, eventually
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from test_grid_profile import (
    FakeClock,
    GridStation,
    ReviewedLockoutStation,
    pending_interim,
    start_grid,
)
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
from custom_components.wallbox_manager.settings import Settings


async def legacy_install(ha, entry=None):
    """Approval regressions retain an explicitly saved beta.12 11-kW setting."""
    entry = entry or config()
    key = json.dumps([entry.data[k] for k in ("station_id", "evse_id", "connector_id")])
    await Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles").async_save(
        {key: {"power_kw": 11}}
    )
    return await ha.install(entry)


def by_role(ha, entry, role):
    return next(e for e in entities(ha, entry) if e.role == role)


async def test_draft_entity_steps_and_times_never_execute_until_set(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        count, revision = (
            len(station.profiles),
            settings.approval_attributes()["grid_approved_revision"],
        )
        number = by_role(ha, entry, "soll_power")
        for value in [5, 5.1, 5.2, 5.3]:
            await number.async_set_native_value(value)
        await by_role(ha, entry, "grid_start_delay").async_set_value("00:05")
        await by_role(ha, entry, "grid_duration").async_set_value("02:00")
        await asyncio.sleep(0)
        assert len(station.profiles) == count
        assert grid.requested.amps == 16 and settings.approved("soll_power") == 11
        assert settings.values["grid_request"]["start_at"] == 1000
        assert settings.approval_attributes()["grid_set_required"]
        button = by_role(ha, entry, "charge_action")
        assert button.available and button.scope == entry.runtime_data.state.binding
        assert button.extra_state_attributes["wallbox_manager_scope"] == "connector"
        await button.async_press()
        assert not button.extra_state_attributes["grid_set_required"]
        assert settings.approved("soll_power") == 5.3
        assert settings.approved("grid_start_delay") == 300
        assert settings.approved("grid_duration") == 7200
        assert settings.approval_attributes()["grid_approved_revision"] == revision + 1
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == count + 1
        # Apply rearms the current business schedule and pauses this transaction.
        assert settings.values["grid_request"]["start_at"] == 1300
        assert station.amps == 0
        await asyncio.gather(button.async_press(), button.async_press())
        assert settings.approval_attributes()["grid_approved_revision"] == revision + 1
        await eventually(lambda: station.amps == 0)
        assert len(station.profiles) == count + 1  # Already confirms zero.
        assert settings.get("charge_release") is False


async def test_equivalent_numeric_power_is_durable_without_set_or_ocpp(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        count = len(station.profiles)
        await settings.set("soll_power", 11.1)
        assert settings.approved("soll_power") == 11.1
        assert not settings.approval_attributes()["grid_set_required"]
        assert (await settings.store.async_load())[settings.key]["grid_approved"][
            "power_kw"
        ] == 11.1
        assert len(station.profiles) == count
        await settings.set("grid_duration", "00:00")
        await settings.set("soll_power", 11.2)
        assert settings.approved("soll_power") == 11.1
        assert settings.approval_attributes()["grid_set_required"]
        await settings._approve_release()
        assert settings.approved("grid_duration") == 0
        assert settings.approved("soll_power") == 11.2
        assert len(station.profiles) == count


async def test_reverted_timing_releases_equivalent_numeric_edit(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        count = len(station.profiles)
        await settings.set("grid_start_delay", "00:01")
        await settings.set("soll_power", 11.1)
        assert settings.approval_attributes()["grid_set_required"]
        await settings.set("grid_start_delay", "")
        assert settings.approved("soll_power") == 11.1
        assert not settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count


async def test_approximation_uses_approved_power_and_reserve_is_immediate(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        await settings.set("power_approximation", "down")
        await settings.set("soll_power", 1.5)
        await settings._approve_release()
        await eventually(lambda: grid.status == "confirmed")
        assert grid.requested.amps == 6 and grid.requested.phases == 1
        await settings.set("soll_power", 3)
        await settings.set("power_approximation", "up")
        await eventually(lambda: grid.status == "confirmed")
        assert grid.requested.amps == 7 and settings.approved("soll_power") == 1.5
        assert settings.get("soll_power") == 3
        assert settings.approval_attributes()["grid_set_required"]
        count = len(station.profiles)
        seen = []
        remove = settings.watch("min_soc", lambda: seen.append(settings.get("min_soc")))
        await settings.set("min_soc", 40)
        remove()
        assert seen == [40] and len(station.profiles) == count
        await settings.set("allowed_current_1p", 6)
        await eventually(
            lambda: grid.status == "confirmed" and grid.requested.amps == 6
        )
        assert (
            settings.get("soll_power") == 3 and settings.approved("soll_power") == 1.5
        )


@pytest.mark.parametrize(
    "mode", ["offline", "local", "no_transaction", "permission_off"]
)
async def test_set_is_durable_without_execution_eligibility(ha, mode):
    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings

    async def approve(station=None):
        await settings.set("soll_power", 3)
        await settings.set("grid_duration", "00:00")
        button = by_role(ha, entry, "charge_action")
        assert button.available
        await button.async_press()
        assert settings.approved("soll_power") == 3
        assert settings.get("charge_release") is False  # Explicit zero duration.
        assert not settings.approval_attributes()["grid_set_required"]
        if station:
            assert not station.profiles
            assert not any(
                w["variable"]["name"] == "ChargingEnabled"
                and w["attribute_value"] == "true"
                for w in station.writes
            )

    if mode == "offline":
        await approve()
    else:
        async with peer(entry, station_type=GridStation) as station:
            from test_active_control import ready

            control = await ready(entry, station)
            if mode != "local":
                await control.request("ControlAuthority", "OCPP")
            if mode == "permission_off":
                await control.request("ChargingEnabled", "false")
            await approve(station)


async def test_failed_set_keeps_durable_drafts_approved_values_and_execution(
    ha, monkeypatch
):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 3)
        await settings.set("grid_duration", "00:05")
        document = await settings.store.async_load()
        count, epoch = len(station.profiles), grid.epoch
        save = settings.store.async_save

        async def fail(data):
            raise OSError("storage unavailable")

        monkeypatch.setattr(settings.store, "async_save", fail)
        with pytest.raises(HomeAssistantError, match="storage unavailable"):
            await by_role(ha, entry, "charge_action").async_press()
        assert settings.get("soll_power") == 3 and settings.approved("soll_power") == 11
        assert settings.approved("grid_duration") is None
        assert settings.approval_attributes()["grid_set_required"]
        assert (
            settings.approval_attributes()["grid_approval_error"]
            == "storage unavailable"
        )
        assert await settings.store.async_load() == document
        assert len(station.profiles) == count and grid.epoch > epoch
        monkeypatch.setattr(settings.store, "async_save", save)
        await settings._approve_release()
        assert not settings.approval_attributes()["grid_set_required"]
        assert settings.approval_attributes()["grid_approval_error"] is None
        await eventually(lambda: grid.status == "confirmed")


async def test_concurrent_set_and_edit_are_serialized_at_durable_commit(
    ha, monkeypatch
):
    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    await settings.set("soll_power", 3)
    await settings.set("grid_duration", "00:05")
    save = settings.store.async_save
    waiting, release = asyncio.Event(), asyncio.Event()
    saves = []

    async def delayed(data):
        saves.append(data)
        if len(saves) == 1:
            waiting.set()
            await release.wait()
        await save(data)

    monkeypatch.setattr(settings.store, "async_save", delayed)
    first = asyncio.create_task(settings._approve_release())
    await waiting.wait()
    assert settings.approved("soll_power") == 11
    assert settings.approval_attributes()["grid_set_required"]
    duplicate = asyncio.create_task(settings._approve_release())
    edit = asyncio.create_task(settings.set("soll_power", 4))
    release.set()
    await asyncio.gather(first, duplicate, edit)
    assert len(saves) == 2  # one atomic triple approval, one subsequent draft edit
    assert saves[0][settings.key]["grid_approved"] == {
        "power_kw": 3,
        "grid_start_delay": None,
        "grid_duration": 300,
        "revision": 1,
        "power_phases": None,
    }
    assert settings.approved("soll_power") == 3 and settings.get("soll_power") == 4
    assert settings.approval_attributes()["grid_set_required"]


async def test_migration_reload_and_foreign_profile_data_preserve_both_states(ha):
    entry = config()
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    key = json.dumps(["Wallbox01", "1", "1"])
    await store.async_save(
        {
            key: {
                "power_kw": 7.2,
                "grid_start_delay": 300,
                "grid_duration": 0,
                "foreign": {"x": [1]},
            },
            json.dumps(["Wallbox01", 1, 1]): {"keep": True},
        }
    )
    entry = await ha.install(entry)
    settings = entry.runtime_data.settings
    assert not settings.approval_attributes()["grid_set_required"]
    assert settings.approved("soll_power") == 7.2
    await settings.set("soll_power", 4)
    await settings.set("grid_duration", "")
    assert await async_unload_entry(ha, entry)
    assert await async_setup_entry(ha, entry)
    settings = entry.runtime_data.settings
    assert settings.get("soll_power") == 4 and settings.approved("soll_power") == 7.2
    assert (
        settings.get("grid_duration") is None
        and settings.approved("grid_duration") == 0
    )
    assert settings.approval_attributes()["grid_set_required"]
    data = await settings.store.async_load()
    assert data[key]["foreign"] == {"x": [1]} and data[
        json.dumps(["Wallbox01", 1, 1])
    ] == {"keep": True}
    assert settings.values.get("grid_request") is None
    await settings._approve_release()
    restored = Settings(
        ha, entry, entry.runtime_data.state.binding, store, await store.async_load()
    )
    assert not restored.approval_attributes()["grid_set_required"]
    assert (
        restored.approved("soll_power") == 4
        and restored.approved("grid_duration") is None
    )


async def test_apply_rearms_delay_and_duration_uses_unchanged_planned_start(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await grid.stop_release()
        await settings.set("grid_start_delay", "00:02")
        await settings.set("grid_duration", "00:03")
        await settings._approve_release()
        await grid.action()
        await eventually(lambda: grid.status == "confirmed")
        request = dict(settings.values["grid_request"])
        count = len(station.profiles)
        await settings.set("grid_start_delay", "00:05")
        assert settings.values["grid_request"] == request
        await settings._approve_release()
        assert settings.values["grid_request"]["start_at"] == 1300
        assert settings.values["grid_request"]["end_at"] == 1480
        assert len(station.profiles) == count
        await clock.advance(300)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 16
        )
        await settings.set("grid_duration", "00:01")
        await settings._approve_release()
        assert settings.values["grid_request"]["start_at"] == 1300
        assert settings.values["grid_request"]["end_at"] == 1360
        await clock.advance(60)
        await eventually(lambda: settings.get("charge_release") is False)
        await grid.action()
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 0
        )
        assert settings.values["grid_request"]["start_at"] == clock.now + 300
        await grid.stop_release()
        await settings.set("soll_power", 5)
        await settings._approve_release()
        await grid.action()
        assert settings.values["grid_request"]["start_at"] == clock.now + 300
        await clock.advance(300)
        await eventually(lambda: grid.status == "confirmed" and grid.confirmed.amps > 0)
        assert settings.values["grid_request"]["start_at"] == clock.now
        assert settings.values["grid_request"]["end_at"] == clock.now + 60


async def test_set_visibility_uses_approved_desired_during_lockout(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, clock = await pending_interim(entry, station)
        settings = entry.runtime_data.settings
        assert grid.confirmed.amps == 0 and grid.requested.amps == 6
        assert not settings.approval_attributes()["grid_set_required"]
        deadline = grid.retry_deadline
        count = len(station.profiles)
        await settings.set("soll_power", 1.4)
        assert settings.approved("soll_power") == 1.4
        assert not settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count and grid.retry_deadline == deadline
        station.locked = False
        station.reenable = True
        await clock.advance(6)
        await eventually(lambda: grid.reason == "ReenableLockout")
        assert not settings.approval_attributes()["grid_set_required"]
        assert grid.requested.amps == 6 and grid.confirmed.amps == 0
        station.reenable = False
        await clock.advance(14)
        await eventually(lambda: grid.status == "confirmed")
        assert grid.confirmed.amps == 6 and grid.confirmed.phases == 1


async def test_missing_confirmation_cannot_autoapprove_numeric_draft(ha):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.fail_interim_read = True
        grid, _ = await pending_interim(entry, station)
        settings = entry.runtime_data.settings
        assert grid.point_uncertain
        await settings.set("soll_power", 1.4)
        assert settings.approved("soll_power") == 1.5
        assert settings.approval_attributes()["grid_set_required"]


async def test_ha_number_text_and_button_services_share_approval_boundary(ha):
    from homeassistant.components import button, number, text

    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        await start_grid(entry, station)
        # Register the real HA domain entity services against the test platforms.
        for component in (number, text, button):
            assert await component.async_setup(ha, {})
        settings = entry.runtime_data.settings
        power = by_role(ha, entry, "soll_power")
        duration = by_role(ha, entry, "grid_duration")
        approve = by_role(ha, entry, "charge_action")
        await ha.services.async_call(
            "number",
            "set_value",
            {"entity_id": power.entity_id, "value": 3},
            blocking=True,
        )
        await ha.services.async_call(
            "text",
            "set_value",
            {"entity_id": duration.entity_id, "value": "00:00"},
            blocking=True,
        )
        assert settings.approved("soll_power") == 11
        assert settings.get("soll_power") == 3
        assert settings.approval_attributes()["grid_set_required"]
        await ha.services.async_call(
            "button", "press", {"entity_id": approve.entity_id}, blocking=True
        )
        assert (
            settings.approved("soll_power") == 3
            and settings.approved("grid_duration") == 0
        )
        assert not settings.approval_attributes()["grid_set_required"]


async def test_reserve_service_remains_immediate_with_unapproved_power(ha):
    from test_grid_battery import battery
    from test_grid_battery_restart import transaction

    _, writes, entry = await battery(ha)
    assert await async_setup_entry(ha, entry)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        await settings.set("min_soc", 40)
        await transaction(station, "Updated", power=10000)
        await eventually(lambda: writes == [40])
        count = len(station.profiles)
        await settings.set("soll_power", 3)
        await by_role(ha, entry, "min_soc").async_set_native_value(45)
        await eventually(lambda: writes == [40, 45])
        assert settings.approved("soll_power") == 11
        assert settings.approval_attributes()["grid_set_required"]
        assert len(station.profiles) == count


async def test_applied_timings_ignore_transaction_changes_and_duplicate_hardware_on(
    ha,
):
    from datetime import UTC, datetime

    from ocpp.v21 import call

    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings, control = (
            entry.runtime_data.settings,
            entry.runtime_data.server.control,
        )
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await settings.set("grid_start_delay", "00:01")
        await settings.set("grid_duration", "00:02")
        await settings._approve_release()
        request = dict(settings.values["grid_request"])
        assert request["start_at"] == 1060 and request["end_at"] == 1180
        # Hardware permission and transaction changes do not rearm business time.
        await control.request("ChargingEnabled", "true")
        assert settings.values["grid_request"] == request
        for kind, tx, seq in [("Ended", "net-test", 1), ("Started", "next-test", 0)]:
            await station.call(
                call.TransactionEvent(
                    event_type=kind,
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason="EVDeparted"
                    if kind == "Ended"
                    else "CablePluggedIn",
                    seq_no=seq,
                    transaction_info={
                        "transaction_id": tx,
                        "charging_state": "EVConnected",
                    },
                    evse={"id": 1, "connector_id": 1},
                )
            )
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 0
        )
        assert settings.values["grid_request"] == request
        await settings.set("grid_duration", "00:03")
        await settings._approve_release()
        await control.request("ChargingEnabled", "true")
        assert settings.values["grid_request"]["start_at"] == request["start_at"]
        assert settings.values["grid_request"]["end_at"] == 1240
        await clock.advance(60)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 16
        )
        await clock.advance(180)
        await eventually(lambda: settings.get("charge_release") is False)


async def test_invalid_entity_drafts_do_not_bypass_approval(ha):
    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    before = await settings.store.async_load()
    for role, value in [
        ("soll_power", -1),
        ("soll_power", True),
        ("grid_duration", "00:99"),
    ]:
        with pytest.raises(ValueError):
            await settings.set(role, value)
    assert await settings.store.async_load() == before
    assert not settings.approval_attributes()["grid_set_required"]


async def test_backend_entries_never_share_draft_or_approved_state(ha):
    one, two = (
        await legacy_install(ha, config("One")),
        await legacy_install(ha, config("Two")),
    )
    first, second = one.runtime_data.settings, two.runtime_data.settings
    await first.set("soll_power", 3)
    await second.set("soll_power", 4)
    await first._approve_release()
    assert first.approved("soll_power") == 3 and second.approved("soll_power") == 11
    assert not first.approval_attributes()["grid_set_required"]
    assert second.approval_attributes()["grid_set_required"]
    assert (
        by_role(ha, one, "charge_action").unique_id
        != by_role(ha, two, "charge_action").unique_id
    )


async def test_capability_change_during_equivalent_save_uses_committed_numeric_power(
    ha, monkeypatch
):
    entry = await legacy_install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings, state = entry.runtime_data.settings, entry.runtime_data.state
        save = settings.store.async_save
        waiting, release = asyncio.Event(), asyncio.Event()

        async def delayed(data):
            waiting.set()
            await release.wait()
            await save(data)

        monkeypatch.setattr(settings.store, "async_save", delayed)
        edit = asyncio.create_task(settings.set("soll_power", 11.1))
        await waiting.wait()
        for role, value in [("minimum_current", 7), ("current_step", 2)]:
            state.observe(
                state.generation,
                ("capability", ("Wallbox01", "1", None), role),
                value,
                "NotifyReport",
            )
        await eventually(
            lambda: grid.status == "confirmed" and grid.requested.amps == 15
        )
        release.set()
        await edit
        await eventually(
            lambda: grid.status == "confirmed" and grid.requested.amps == 17
        )
        assert settings.approved("soll_power") == 11.1
        assert not settings.approval_attributes()["grid_set_required"]


async def test_set_validation_failure_keeps_pending_draft_and_approval(ha, monkeypatch):
    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    await settings.set("soll_power", 3)
    original = settings.validate

    def invalid(role, value):
        if role == "soll_power":
            raise ValueError("Invalid staged request")
        return original(role, value)

    monkeypatch.setattr(settings, "validate", invalid)
    with pytest.raises((ValueError, OSError), match="Invalid staged request"):
        await settings._approve_release()
    assert settings.approval_attributes()["grid_set_required"]
    assert not settings.approval_attributes()["grid_set_ready"]
    assert settings.get("soll_power") == 3 and settings.approved("soll_power") == 11
    monkeypatch.setattr(settings, "validate", original)
    await settings._approve_release()
    assert settings.approved("soll_power") == 3


async def test_start_consumes_timing_after_concurrent_set_commits(ha, monkeypatch):
    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    await settings.set("grid_start_delay", "00:01")
    await settings._approve_release()
    await settings.set("grid_start_delay", "00:05")
    save = settings.store.async_save
    waiting, release = asyncio.Event(), asyncio.Event()

    async def delayed(data):
        waiting.set()
        await release.wait()
        await save(data)

    monkeypatch.setattr(settings.store, "async_save", delayed)
    approval = asyncio.create_task(settings._approve_release())
    await waiting.wait()
    start = asyncio.create_task(settings._approve_release(start=True, now=lambda: 1000))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(approval, start)
    assert settings.values["grid_request"]["start_at"] == 1300
    assert settings.approved("grid_start_delay") == 300
    assert settings.values["grid_request"]["epoch_at"] == 1000


async def test_failed_start_persistence_cannot_bypass_approved_delay(ha, monkeypatch):
    entry = await legacy_install(ha)
    settings, grid = entry.runtime_data.settings, entry.runtime_data.grid
    clock = FakeClock()
    grid.wall_time, grid.call_later = lambda: clock.now, clock.later
    await settings.set("grid_start_delay", "00:01")
    await settings._approve_release()
    save = settings.store.async_save

    async def fail(data):
        if data[settings.key].get("grid_request"):
            raise OSError("Cannot persist schedule")
        await save(data)

    monkeypatch.setattr(settings.store, "async_save", fail)
    async with peer(entry, station_type=GridStation) as station:
        with pytest.raises(OSError, match="Cannot persist schedule"):
            await start_grid(entry, station)
        assert not station.profiles and grid.transaction() == "net-test"
        assert settings.get("charge_release") is False
        assert settings.values["grid_request"] is None
        assert settings.approved("grid_start_delay") == 60
        monkeypatch.setattr(settings.store, "async_save", save)
        await settings.set("soll_power", 3)
        await grid.action()
        await eventually(lambda: grid.status == "confirmed")
        assert settings.values["grid_request"]["start_at"] == 1060
        assert not station.profiles and grid.confirmed.amps == 0
        await clock.advance(60)
        await eventually(lambda: grid.status == "confirmed" and grid.confirmed.amps > 0)


async def test_real_ha_store_swallowed_write_error_does_not_approve(ha, monkeypatch):
    from homeassistant.util.file import WriteError

    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    await settings.set("soll_power", 3)
    before = await settings.store.async_load()

    def fail(*args):
        raise WriteError("Disk write failed")

    monkeypatch.setattr(settings.store, "_write_prepared_data", fail)
    with pytest.raises((ValueError, OSError)):
        await settings._approve_release()
    assert settings.approved("soll_power") == 11
    assert settings.approval_attributes()["grid_set_required"]
    assert await settings.store.async_load() == before


async def test_shutdown_deferred_store_write_cannot_approve(ha):
    from homeassistant.core import CoreState

    entry = await legacy_install(ha)
    settings = entry.runtime_data.settings
    await settings.set("soll_power", 3)
    before = await settings.store.async_load()
    previous = ha.state
    ha.set_state(CoreState.stopping)
    try:
        with pytest.raises((ValueError, OSError), match="stopping"):
            await settings._approve_release()
        assert settings.approved("soll_power") == 11
        assert settings.approval_attributes()["grid_set_required"]
        assert await settings.store.async_load() == before
    finally:
        ha.set_state(previous)
