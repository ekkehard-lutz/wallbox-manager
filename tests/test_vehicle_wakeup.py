"""Removed remote wake-up settings are ignored; retire only integration-owned IDs."""

import pytest
from conftest import entities
from homeassistant.helpers import entity_registry as er
from test_active_control import Controllable, ready
from test_protocol import peer, row

from custom_components.wallbox_manager import async_unload_entry
from custom_components.wallbox_manager.entity import unique_id


class LegacyStation(Controllable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        automatic = row("WallboxController", "AutoWakeUpEnabled", "true")
        automatic["variable_attribute"][0]["mutability"] = "ReadWrite"
        self.rows.append(automatic)
        self.values["AutoWakeUpEnabled"] = "true"


async def test_legacy_wire_variable_ignored_and_not_writable(ha):
    entry = await ha.install()
    async with peer(entry, station_type=LegacyStation) as station:
        control = await ready(entry, station)
        assert not any(e.role == "auto_wakeup_enabled" for e in entities(ha, entry))
        assert "AutoWakeUpEnabled" not in entry.runtime_data.server.adapter.writable
        assert not any(
            "AutoWakeUpEnabled" in str(k) for k in entry.runtime_data.state.observations
        )
        with pytest.raises(ValueError):
            await control.request("AutoWakeUpEnabled", "true")
        assert not station.writes


async def test_obsolete_switch_and_set_registry_migration_is_exact(ha):
    entry = await ha.install()
    registry = er.async_get(ha)
    keys = [
        ("switch", "station", ("Wallbox01", None, None), "auto_wakeup_enabled"),
        ("button", "control", entry.runtime_data.state.binding, "grid_set"),
    ]
    old, foreign = [], []
    for domain, family, scope, role in keys:
        old.append(
            registry.async_get_or_create(
                domain,
                "wallbox_manager",
                unique_id(entry.entry_id, family, scope, role),
                config_entry=entry,
            )
        )
        foreign.append(
            registry.async_get_or_create(
                domain, "wallbox_manager", "foreign:" + role, config_entry=entry
            )
        )
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    assert all(registry.async_get(e.entity_id) is None for e in old)
    assert all(registry.async_get(e.entity_id) is not None for e in foreign)
    roles = {e.role for e in entities(ha, entry)}
    assert "charging_enabled" in roles and "charge_action" in roles
    assert not roles & {"auto_wakeup_enabled", "grid_set", "wake_up_vehicle"}


async def test_legacy_registry_button_is_retired_without_foreign_removal(ha):
    entry = await ha.install()
    registry = er.async_get(ha)
    legacy = registry.async_get_or_create(
        "button",
        "wallbox_manager",
        f"{entry.entry_id}:Wallbox01:wake_up_vehicle",
        config_entry=entry,
    )
    foreign = registry.async_get_or_create(
        "button",
        "wallbox_manager",
        "foreign:Wallbox01:wake_up_vehicle",
        config_entry=entry,
    )
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(foreign.entity_id) is not None
    assert not any(e.role == "wake_up_vehicle" for e in entities(ha, entry))
