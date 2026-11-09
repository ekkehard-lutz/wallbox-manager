"""Current-format entry, registry, settings and options contracts."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import config, entities
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

import custom_components.wallbox_manager as integration
from custom_components.wallbox_manager.entity import unique_id


def entity(ha, entry, role):
    return next(e for e in entities(ha, entry) if e.role == role)


@pytest.mark.parametrize("station", ["Garage:west", "control"])
async def test_current_registry_and_device_survive_reload(ha, monkeypatch, station):
    entry = config(station)
    data = dict(entry.data, evse_id="2", connector_id="3")
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(entry, data=data)
    await ha.install(entry)
    registry, devices = er.async_get(ha), dr.async_get(ha)
    power = registry.async_get(entity(ha, entry, "soll_power").entity_id)
    device_id = power.device_id
    power = registry.async_update_entity(
        power.entity_id, new_entity_id="number.garage_power"
    )
    devices.async_update_device(device_id, name_by_user="Garage")
    original_runtime = entry.runtime_data
    await integration.async_unload_entry(ha, entry)
    update = Mock(wraps=ha.config_entries.async_update_entry)
    monkeypatch.setattr(ha.config_entries, "async_update_entry", update)
    await ha.install(entry)
    update.assert_not_called()
    assert entry.data == data
    assert entry.runtime_data.state.binding == (station, "2", "3")
    assert original_runtime.server.server is None
    assert entity(ha, entry, "soll_power").unique_id == power.unique_id
    assert entity(ha, entry, "soll_power").entity_id == power.entity_id
    assert registry.async_get(power.entity_id).device_id == device_id
    assert devices.async_get(device_id).name_by_user == "Garage"
    assert devices.async_get(device_id).identifiers == {
        ("wallbox_manager", f"{entry.entry_id}:{station}")
    }


@pytest.mark.parametrize(
    ("target_data", "stored_targets"),
    [
        ({"station_id": ""}, []),
        ({"evse_id": "01"}, []),
        ({"connector_id": None}, []),
        ({"station_id": None}, [("Wallbox01", "1", "1")]),
        ({}, [("OtherStation", "1", "1")]),
        ({}, [("Wallbox01", "2", "1")]),
        ({}, [("Wallbox01", "1", "2")]),
        ({}, [("Wallbox01", "1", "1"), ("Wallbox01", "1", "2")]),
    ],
)
async def test_invalid_binding_fails_before_mutation(
    ha, monkeypatch, target_data, stored_targets
):
    entry = config()
    data = dict(entry.data)
    for key, value in target_data.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(entry, data=data)
    profiles = {json.dumps(scope): {"power_kw": 7.2} for scope in stored_targets}
    profile_store = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.profiles")
    await profile_store.async_save(profiles)
    write = AsyncMock()
    monkeypatch.setattr(Store, "async_save", write)
    with pytest.raises(ConfigEntryError):
        await ha.install(entry)
    write.assert_not_awaited()
    assert entry.data == data
    assert not entry.options and not entry.subentries
    assert await profile_store.async_load() == profiles
    assert not dr.async_entries_for_config_entry(dr.async_get(ha), entry.entry_id)
    assert not er.async_entries_for_config_entry(er.async_get(ha), entry.entry_id)
    assert not hasattr(entry, "runtime_data")


@pytest.mark.parametrize(
    ("version", "supported"), [(1, False), (2, False), (3, True), (4, False)]
)
async def test_home_assistant_migration_lifecycle_preserves_entry_version(
    ha, version, supported
):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(entry, version=version, minor_version=0)
    original_data = dict(entry.data)
    entry._integration_for_domain = SimpleNamespace(
        async_get_component=AsyncMock(return_value=integration),
        logger=logging.getLogger(__name__),
    )
    assert await entry.async_migrate(ha) is supported
    assert entry.version == version and entry.minor_version == 0
    assert entry.data == original_data


@pytest.mark.parametrize(
    "fault",
    ["station_entity", "linked_device", "entry_device", "pv_connector", "malformed_id"],
)
async def test_foreign_registry_data_is_rejected_without_writes(ha, fault):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    registry, devices = er.async_get(ha), dr.async_get(ha)
    device = None
    if fault in ("linked_device", "entry_device"):
        owner = (
            entry
            if fault == "entry_device"
            else await ha.install(config("OtherStation"))
        )
        device = devices.async_get_or_create(
            config_entry_id=owner.entry_id,
            identifiers={("wallbox_manager", f"{owner.entry_id}:OtherStation")},
        )
    if fault != "entry_device":
        identifier = {
            "station_entity": f"{entry.entry_id}:OtherStation:discovery",
            "linked_device": unique_id(
                entry.entry_id, "control", ("Wallbox01", "1", "1"), "soll_power"
            ),
            "pv_connector": unique_id(
                entry.entry_id, "pv", ("Wallbox01", "1", "2"), "effective_target_soc"
            ),
            "malformed_id": f"{entry.entry_id}:observation:[null]",
        }[fault]
        registered = registry.async_get_or_create(
            "number" if fault == "linked_device" else "sensor",
            "wallbox_manager",
            identifier,
            config_entry=entry,
            device_id=device.id if device else None,
        )
    with pytest.raises(ConfigEntryError, match="(?i)registered|registry"):
        await ha.install(entry)
    assert not hasattr(entry, "runtime_data")
    if fault != "entry_device":
        assert registry.async_get(registered.entity_id) is registered
    if device:
        assert devices.async_get(device.id) is device


async def test_explicit_binding_ignores_legacy_option_snapshots(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    options = {
        "station_references": {"obsolete": 123},
        "passive_v04_legacy_subentries": ["obsolete"],
    }
    ha.config_entries.async_update_entry(entry, options=options)
    await ha.install(entry)
    assert entry.runtime_data.state.binding == ("Wallbox01", "1", "1")
    assert entry.options == options
