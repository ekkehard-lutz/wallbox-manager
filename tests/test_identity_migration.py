"""Current identity, scoped settings and retired storage isolation."""

import json
from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from conftest import config, entities
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import State as HAState
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from homeassistant.helpers.storage import Store

from custom_components.wallbox_manager import async_unload_entry
from custom_components.wallbox_manager.config_flow import WallboxManagerConfigFlow
from custom_components.wallbox_manager.entity import unique_id
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


async def test_current_sensor_registry_and_recorder_metadata_are_preserved(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    devices, registry = dr.async_get(ha), er.async_get(ha)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
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
        device_id=device.id,
        suggested_object_id="my_energy",
    )
    disabled = registry.async_get_or_create(
        "sensor",
        "wallbox_manager",
        unique_id(entry.entry_id, "observation", ("Wallbox01", "1", None), "power"),
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await ha.install(entry)
    assert devices.async_get(device.id).name_by_user == "Garage"
    assert registry.async_get(entity.entity_id).device_id == device.id
    assert registry.async_get(disabled.entity_id).device_id == device.id
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
    with pytest.raises(ConfigEntryError, match="Registered entity"):
        await ha.install(entry)
    assert (dict(entry.data), dict(entry.options), dict(entry.subentries)) == before
    assert registry.async_get(foreign.entity_id) is foreign


@pytest.mark.parametrize("count", [1, 2])
async def test_legacy_subentries_fail_without_reparenting(ha, count):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    devices, registry = dr.async_get(ha), er.async_get(ha)
    for connector in range(1, count + 1):
        sub = ConfigSubentry(
            subentry_type="wallbox",
            title=str(connector),
            unique_id=str(connector),
            data=MappingProxyType(
                {"station": "Wallbox01", "evse": "1", "connector": str(connector)}
            ),
        )
        ha.config_entries.async_add_subentry(entry, sub)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=sub.subentry_id,
        identifiers={("wallbox_manager", entry.entry_id + ":Wallbox01")},
    )
    entity = registry.async_get_or_create(
        "sensor",
        "wallbox_manager",
        unique_id(
            entry.entry_id, "observation", ("Wallbox01", "1", str(count)), "energy"
        ),
        config_entry=entry,
        config_subentry_id=sub.subentry_id,
        device_id=device.id,
    )
    before = dict(entry.data), dict(entry.options), dict(entry.subentries)
    with pytest.raises(ConfigEntryError, match="cannot have subentries"):
        await ha.install(entry)
    assert (dict(entry.data), dict(entry.options), dict(entry.subentries)) == before
    assert registry.async_get(entity.entity_id) is entity
    assert devices.async_get(device.id).config_subentry_id == sub.subentry_id


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
            "target_w": 51000,
            "approximation": "up",
            "optimum_max_discharge_w": 1,
            "foreign": "preserve",
        }
    }
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    await store.async_save(saved)
    entry = await ha.install(entry)
    settings = entry.runtime_data.settings
    assert settings.get("charging_profile") == "PV_OPTIMUM"
    assert settings.get("soll_power") == 7.2
    assert settings.get("power_approximation") == "nearest"
    assert (
        not {"target_w", "approximation", "optimum_max_discharge_w"}
        & settings.values.keys()
    )
    assert not {
        "desired_charging_power",
        "pv_approximation",
        "optimum_max_discharge_w",
    } & {e.role for e in entities(ha, entry)}
    migrated = await store.async_load()
    assert {k: migrated[key][k] for k in saved[key]} == saved[key]
    assert migrated[key]["grid_approved"]["power_kw"] == 7.2
    discovery = next(e for e in entities(ha, entry) if e.role == "discovery")
    assert discovery.options == ["unknown", "verified", "unsupported", "degraded"]
    await settings.set("soll_power", 6.8)
    await settings.set("charging_profile", "NETZ")
    await settings.set("grid_duration", "12:34")
    await settings.set("power_approximation", "up")
    restored = Settings(
        ha, entry, entry.runtime_data.state.binding, store, await store.async_load()
    )
    assert restored.get("grid_duration") == 45240
    assert restored.get("soll_power") == 6.8
    assert restored.get("power_approximation") == "up"
    assert (await store.async_load())[key]["foreign"] == "preserve"
    assert (await store.async_load())[key]["legacy_soc_targets"] == {"x": 3}
    for role, value in (
        ("soll_power", float("nan")),
        ("min_soc", 101),
        ("grid_duration", "1:78"),
        ("charging_profile", "BAD"),
        ("soll_soc_speicher", 42.5),
        ("desired_charging_power", 100),
        ("pv_approximation", "down"),
        ("optimum_max_discharge_w", 100),
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


async def test_missing_passive_session_leaves_historical_ledger_inert(ha, monkeypatch):
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
    reads = []
    load = Store.async_load

    async def record_load(store):
        reads.append(store.key)
        return await load(store)

    monkeypatch.setattr(Store, "async_load", record_load)
    await ha.install(entry)
    runtime = entry.runtime_data
    assert runtime.session_store.key in reads
    assert store.key not in reads
    assert runtime.state.session is None and not runtime.state.closed_transactions
    assert not any(e.family == "session" for e in entities(ha, entry))
    await async_unload_entry(ha, entry)
    assert await load(runtime.session_store) is None
    assert await load(store) == original


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


async def test_stored_fractional_cap_keeps_exact_value_and_current_normalization(ha):
    entry = config()
    scope = ("Wallbox01", "1", "1")
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    await store.async_save(
        {json.dumps(scope): {"allowed_current_1p": "19/3", "direction": "up"}}
    )
    await ha.install(entry)
    settings = entry.runtime_data.settings
    assert settings.get("allowed_current_1p") == "19/3"
    assert not any(e.role == "allowed_current_1p" for e in entities(ha, entry))
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    for scope, role, value in (
        (("Wallbox01", "1", None), "minimum_current", 6),
        (state.binding, "maximum_current", 27),
        (state.binding, "maximum_asymmetry_current", 20),
        (("Wallbox01", "1", None), "current_step", 1),
        (("Wallbox01", "1", None), "supported_phases", "1,3"),
    ):
        state.observe(generation, ("capability", scope, role), value, "inventory")
    await ha.async_block_till_done()
    assert settings.get("allowed_current_1p") == "19/3"
    assert settings.get("power_approximation") == "up"
    current = next(e for e in entities(ha, entry) if e.role == "allowed_current_1p")
    assert current.extra_state_attributes["exact_current_limit_a"] == "19/3"
    assert current.native_value == 6
    assert "legacy_limit_migration" not in current.extra_state_attributes
    await settings.set("allowed_current_1p", 16)
    await settings.set("allowed_current_3p", 0)
    state.observe(
        generation,
        ("capability", state.binding, "maximum_asymmetry_current"),
        10,
        "inventory",
    )
    assert current.native_max_value == current.native_value == 10
    assert current.extra_state_attributes["exact_current_limit_a"] == "16"
    assert "legacy_limit_migration" not in current.extra_state_attributes
    assert settings.get("allowed_current_1p") == 16
    disabled = next(e for e in entities(ha, entry) if e.role == "allowed_current_3p")
    assert disabled.native_value == 0
    assert (await settings.store.async_load())[settings.key]["allowed_current_3p"] == 0


async def test_failed_setting_save_does_not_commit_options_or_memory(ha, monkeypatch):
    entry = await ha.install()
    settings = entry.runtime_data.settings
    before = dict(entry.options), dict(settings.values), dict(settings.profiles)
    monkeypatch.setattr(
        settings.store, "async_save", AsyncMock(side_effect=OSError("disk"))
    )
    with pytest.raises(OSError, match="disk"):
        # PV-TUNE-10 retires the seconds-based delay; storage failure semantics
        # are now checked with its independently named cycle-based successor.
        await settings.set("pv_decrease_delay_cycles", 120)
    assert (dict(entry.options), settings.values, settings.profiles) == before


async def test_retired_registry_entries_stay_inert(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    registry = er.async_get(ha)
    scope = ("Wallbox01", "1", "1")
    retired = []
    for domain, family, role in (
        ("sensor", "capability", "maximum_current_1"),
        ("sensor", "capability", "maximum_current_3"),
    ):
        retired.append(
            registry.async_get_or_create(
                domain,
                "wallbox_manager",
                unique_id(entry.entry_id, family, scope, role),
                config_entry=entry,
            ).entity_id
        )
    preserved = [
        registry.async_get_or_create(
            "number",
            "wallbox_manager",
            unique_id(entry.entry_id, "control", scope, role),
            config_entry=entry,
        ).entity_id
        for role in (
            "min_soc",
            "phase_switch_deviation_pct",
            "pv_start_delay",
            "pv_stop_delay",
        )
    ]
    await ha.install(entry)
    assert all(
        registry.async_get(entity_id) is not None for entity_id in retired + preserved
    )
    assert not {
        "phase_switch_deviation_pct",
        "pv_start_delay",
        "pv_stop_delay",
        "maximum_current_1",
        "maximum_current_3",
    } & {entity.role for entity in entities(ha, entry)}


async def test_retired_profile_fields_are_inert_without_cleanup_writes(ha, monkeypatch):
    entry = await ha.install()
    settings = entry.runtime_data.settings
    await settings.set("min_soc", 33)
    store, key = settings.store, settings.key
    await async_unload_entry(ha, entry)

    data = await store.async_load()
    retired = {
        "pv_start_delay": 75,
        "pv_stop_delay": 90,
        "phase_switch_deviation_pct": 5,
    }
    data[key].update(retired)
    await store.async_save(data)
    write = AsyncMock()
    monkeypatch.setattr(Store, "async_save", write)
    await ha.install(entry)

    assert await store.async_load() == data
    write.assert_not_awaited()
    settings = entry.runtime_data.settings
    assert all(settings.get(key) is None for key in retired)
    assert settings.get("min_soc") == 33
    assert settings.get("pv_startup_hold_cycles") == 10
    assert settings.get("grid_start_delay") is None


@pytest.mark.parametrize("stored", [False, True])
async def test_scoped_caps_and_direction_override_restored_entity_states(ha, stored):
    entry = config()
    scope = ("Wallbox01", "1", "1")
    ha.config_entries._entries[entry.entry_id] = entry
    raw = {
        "allowed_current_1p": 16,
        "allowed_current_2p": 12,
        "allowed_current_3p": 0,
        "direction": "down",
    }
    store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    if stored:
        await store.async_save({json.dumps(scope): raw})
    restored = restore_state.async_get(ha)
    for domain, role, value in (
        ("number", "allowed_current_1p", "7"),
        ("number", "allowed_current_2p", "8"),
        ("number", "allowed_current_3p", "9"),
        ("select", "power_approximation", "up"),
    ):
        item = er.async_get(ha).async_get_or_create(
            domain,
            "wallbox_manager",
            unique_id(entry.entry_id, "control", scope, role),
            config_entry=entry,
        )
        restored.last_states[item.entity_id] = restore_state.StoredState(
            HAState(item.entity_id, value), None, datetime.now(UTC)
        )

    for _ in range(2):
        await ha.install(entry)
        state, settings = entry.runtime_data.state, entry.runtime_data.settings
        generation = state.reset(connected=True)
        parent = ("Wallbox01", "1", None)
        for location, role, value in (
            (parent, "minimum_current", 6),
            (parent, "current_step", 1),
            (parent, "supported_phases", "1,2,3"),
            (scope, "maximum_current", 27),
            (scope, "maximum_asymmetry_current", 10),
        ):
            state.observe(
                generation, ("capability", location, role), value, "inventory"
            )
        await ha.async_block_till_done()
        registered = {e.role: e for e in entities(ha, entry)}
        for mode, effective in enumerate((10, 10, 0 if stored else 27), start=1):
            role = f"allowed_current_{mode}p"
            assert (
                settings.get(role) == raw[role]
                if stored
                else settings.get(role) is None
            )
            assert registered[role].native_value == effective
        assert registered["power_approximation"].current_option == (
            "down" if stored else "nearest"
        )
        await async_unload_entry(ha, entry)
        del entry.runtime_data
