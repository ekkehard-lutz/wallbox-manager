"""Public identity/statistics continuity and reversible registry association changes."""

import json
from types import MappingProxyType

import pytest
from conftest import config, entities
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from custom_components.wallbox_manager.config_flow import WallboxManagerConfigFlow
from custom_components.wallbox_manager.entity import unique_id
from custom_components.wallbox_manager.migration import migrate
from custom_components.wallbox_manager.settings import Settings


def test_exact_public_identity_formats():
    scope = ("Wallbox01", "1", "1")
    assert (
        unique_id("E", "control", scope, "soll_power")
        == 'E:control:["Wallbox01","1","1","soll_power"]'
    )
    assert (
        unique_id("E", "observation", scope, "energy")
        == 'E:observation:["Wallbox01","1","1","energy",null]'
    )
    assert (
        unique_id("E", "observation", ("Wallbox01", "1", None), "power")
        == 'E:observation:["Wallbox01","1",null,"power",null]'
    )
    assert (
        unique_id("E", "observation", ("Wallbox01", None, None), "power")
        == 'E:observation:["Wallbox01",null,null,"power",null]'
    )
    assert (
        unique_id("E", "session", scope, "energy")
        == 'E:session:["Wallbox01","1","1","energy"]'
    )
    assert (
        unique_id("E", "capability", scope, "maximum_current_1")
        == 'E:capability:["Wallbox01","1","1","maximum_current_1"]'
    )
    assert unique_id("E", "station", scope, "connected") == "E:Wallbox01:connected"
    assert not WallboxManagerConfigFlow.async_get_supported_subentry_types(None)


async def test_subentry_migration_preserves_registry_and_rollback(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    subentry = ConfigSubentry(
        subentry_type="wallbox",
        title="Wallbox01 / 1 / 1",
        unique_id='["Wallbox01","1","1"]',
        data=MappingProxyType(
            {"station": "Wallbox01", "evse": "1", "connector": "1", "references": {}}
        ),
    )
    ha.config_entries.async_add_subentry(entry, subentry)
    devices, registry = dr.async_get(ha), er.async_get(ha)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry.subentry_id,
        identifiers={("wallbox_manager", entry.entry_id + ":Wallbox01")},
        name="Wallbox01",
    )
    devices.async_update_device(device.id, name_by_user="Garage")
    energy_id = unique_id(
        entry.entry_id, "observation", ("Wallbox01", "1", None), "energy"
    )
    entity = registry.async_get_or_create(
        "sensor",
        "wallbox_manager",
        energy_id,
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        device_id=device.id,
        suggested_object_id="my_historical_energy",
    )
    disabled = registry.async_get_or_create(
        "sensor",
        "wallbox_manager",
        unique_id(entry.entry_id, "observation", ("Wallbox01", "1", None), "power"),
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    ha.config_entries.async_update_entry(entry, data={"host": "127.0.0.1", "port": 0})
    original_id = entry.entry_id
    assert migrate(ha, entry, {}) == ("Wallbox01", "1", "1")
    await ha.async_block_till_done()
    assert not entry.subentries
    assert entry.entry_id == original_id and entry.version == 3
    assert devices.async_get(device.id).name_by_user == "Garage"
    assert devices.async_get(device.id).config_subentry_id is None
    kept = registry.async_get(entity.entity_id)
    assert kept.unique_id == energy_id and kept.device_id == device.id
    assert kept.config_subentry_id is None
    assert (
        registry.async_get(disabled.entity_id).disabled_by
        == er.RegistryEntryDisabler.USER
    )
    assert registry.async_get(disabled.entity_id).config_subentry_id is None
    assert (
        entry.options["passive_v04_legacy_subentries"][0]["id"] == subentry.subentry_id
    )
    # beta.18's documented ensure_subentry format can be reconstructed without
    # deleting/recreating entities or changing statistics identity.
    ref = entry.options["station_references"]["Wallbox01"]["1:1"]
    assert (
        ref["reference_station_id"],
        ref["reference_evse_id"],
        ref["reference_connector_id"],
    ) == ("Wallbox01", 1, 1)
    restored = ConfigSubentry(
        data=subentry.data,
        subentry_type="wallbox",
        title=subentry.title,
        unique_id=subentry.unique_id,
    )
    ha.config_entries.async_add_subentry(entry, restored)
    assert registry.async_get(entity.entity_id).device_id == device.id
    # Remove it through migration again, then register the actual new HA sensor.
    await ha.install(entry)
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    state.observe(
        generation, ("observation", ("Wallbox01", "1", None), "energy"), 123.456, "test"
    )
    await ha.async_block_till_done()
    assert registry.async_get(entity.entity_id).unique_id == energy_id
    attrs = ha.states.get(entity.entity_id).attributes
    assert (
        attrs["unit_of_measurement"],
        attrs["device_class"],
        attrs["state_class"],
    ) == ("kWh", "energy", "total_increasing")
    assert len(dr.async_entries_for_config_entry(devices, entry.entry_id)) == 1
    assert ha.states.get(entity.entity_id).state == "123.456"
    assert (
        registry.async_get(disabled.entity_id).disabled_by
        == er.RegistryEntryDisabler.USER
    )
    assert ha.states.get(disabled.entity_id) is None


async def test_foreign_parent_scope_registry_fails_before_writes(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    registry = er.async_get(ha)
    foreign = registry.async_get_or_create(
        "sensor",
        "wallbox_manager",
        unique_id(entry.entry_id, "observation", ("Wallbox01", "2", None), "energy"),
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    before = dict(entry.data), dict(entry.options), dict(entry.subentries)
    with pytest.raises(ConfigEntryError, match="migration decision"):
        migrate(ha, entry, {})
    assert (dict(entry.data), dict(entry.options), dict(entry.subentries)) == before
    assert registry.async_get(foreign.entity_id) is foreign


async def test_ambiguous_migration_fails_before_any_write(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    for connector in ("1", "2"):
        sub = ConfigSubentry(
            subentry_type="wallbox",
            title=connector,
            unique_id=connector,
            data=MappingProxyType(
                {"station": "Wallbox01", "evse": "1", "connector": connector}
            ),
        )
        ha.config_entries.async_add_subentry(entry, sub)
    before = dict(entry.data), dict(entry.options), dict(entry.subentries)
    with pytest.raises(ConfigEntryError, match="migration decision"):
        migrate(ha, entry, {})
    assert (dict(entry.data), dict(entry.options), dict(entry.subentries)) == before


async def test_settings_keep_legacy_storage_and_have_no_physical_actions(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    key = json.dumps(["Wallbox01", "1", "1"])
    saved = {
        key: {
            "profile": "PV_OPTIMUM",
            "power_kw": 7.2,
            "min_soc": 33,
            "legacy_soc_targets": {"x": 3},
            "foreign": "preserve",
        }
    }
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    await store.async_save(saved)
    entry = await ha.install(entry)
    settings = entry.runtime_data.settings
    assert settings.get("charging_profile") == "PV_OPTIMUM"
    assert settings.get("soll_power") == 7.2
    assert await store.async_load() == saved
    await settings.set("soll_power", 6.8)
    await settings.set("charging_profile", "NETZ")
    await settings.set("grid_duration", "12:34")
    await settings.set("pv_approximation", "up")
    restored = Settings(
        ha, entry, entry.runtime_data.state.binding, store, await store.async_load()
    )
    assert restored.get("grid_duration") == 45240
    assert restored.get("soll_power") == 6.8
    assert (await store.async_load())[key]["foreign"] == "preserve"
    assert (await store.async_load())[key]["legacy_soc_targets"] == {"x": 3}
    for role, value in (
        ("soll_power", float("nan")),
        ("min_soc", 101),
        ("grid_duration", "1:78"),
        ("charging_profile", "BAD"),
        ("soll_soc_speicher", 42.5),
    ):
        with pytest.raises(ValueError):
            await settings.set(role, value)
    permission = next(e for e in entities(ha, entry) if e.role == "charging_enabled")
    takeover = next(e for e in entities(ha, entry) if e.role == "take_control")
    assert takeover.available is False
    for action in (
        permission.async_turn_on,
        permission.async_turn_off,
        takeover.async_press,
    ):
        with pytest.raises(HomeAssistantError):
            await action()
    assert entry.runtime_data.server.adapter is None


async def test_legacy_session_display_load_is_read_only(ha):
    from datetime import UTC, datetime, timedelta

    entry = config()
    at = datetime.now(UTC) - timedelta(hours=2)
    record = {
        "session_id": "preserved-internal-id",
        "external_transaction_id": "actual-old-id",
        "scope": ["Wallbox01", "1", "1"],
        "started_at": at.isoformat(),
        "updated_at": (at + timedelta(hours=1)).isoformat(),
        "ended_at": (at + timedelta(hours=1)).isoformat(),
        "energy_start_wh": "100000",
        "energy_end_wh": "102500",
        "energy_charged_wh": "2500",
        "sequence": 12,
        "start_known": True,
        "foreign": "untouched",
    }
    original = {
        "records": [record],
        "latest": [record["session_id"]],
        "next_transaction_id": 2,
    }
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.sessions")
    await store.async_save(original)
    await ha.install(entry)
    assert entry.runtime_data.state.session["session_id"] == record["session_id"]
    reading = next(
        e for e in entities(ha, entry) if e.family == "session" and e.role == "energy"
    )
    assert reading.native_value == 2.5
    assert reading.unique_id == unique_id(
        entry.entry_id, "session", ("Wallbox01", "1", "1"), "energy"
    )
    assert await store.async_load() == original


async def test_profile_edits_are_serialized_without_lost_updates(ha):
    import asyncio

    entry = await ha.install()
    settings = entry.runtime_data.settings
    await asyncio.gather(
        settings.set("soll_power", 8.1),
        settings.set("min_soc", 44),
        settings.set("grid_duration", "04:20"),
    )
    data = await settings.store.async_load()
    assert (
        data[settings.key]["power_kw"],
        data[settings.key]["min_soc"],
        data[settings.key]["grid_duration"],
    ) == (8.1, 44, 15600)


async def test_manual_preferences_restore_historical_ids_and_exact_current(ha):
    from datetime import UTC, datetime

    from homeassistant.core import State
    from homeassistant.helpers import restore_state

    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    restored = restore_state.async_get(ha)
    for domain, role, value, attrs in (
        (
            "number",
            "allowed_current_1p",
            "6.3333333333",
            {"exact_current_limit_a": "19/3"},
        ),
        ("number", "desired_charging_power", "7200", {}),
        ("select", "power_approximation", "up", {}),
    ):
        registered = er.async_get(ha).async_get_or_create(
            domain,
            "wallbox_manager",
            unique_id(entry.entry_id, "control", ("Wallbox01", "1", "1"), role),
            config_entry=entry,
            suggested_object_id="historical_" + role,
        )
        restored.last_states[registered.entity_id] = restore_state.StoredState(
            State(registered.entity_id, value, attrs), None, datetime.now(UTC)
        )
    await ha.install(entry)
    settings = entry.runtime_data.settings
    assert settings.get("allowed_current_1p") == "19/3"
    assert settings.get("desired_charging_power") == 7200
    assert settings.get("power_approximation") == "up"
    current = next(e for e in entities(ha, entry) if e.role == "allowed_current_1p")
    assert current.entity_id == "number.historical_allowed_current_1p"
    assert current.extra_state_attributes["exact_current_limit_a"] == "19/3"


async def test_failed_setting_save_does_not_commit_options_or_memory(ha, monkeypatch):
    from unittest.mock import AsyncMock

    entry = await ha.install()
    settings = entry.runtime_data.settings
    before = dict(entry.options), dict(settings.values), dict(settings.profiles)
    monkeypatch.setattr(
        settings.store, "async_save", AsyncMock(side_effect=OSError("disk"))
    )
    with pytest.raises(OSError, match="disk"):
        await settings.set("pv_stop_delay", 120)
    assert (dict(entry.options), settings.values, settings.profiles) == before
