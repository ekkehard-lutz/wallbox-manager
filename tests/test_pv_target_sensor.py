"""Real HA registration, canonical planning and change-only Recorder publication."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from conftest import config, entities
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from test_pv_planning_release import selected_pv, set_source
from test_pv_runtime import install, tick

from custom_components.wallbox_manager import async_unload_entry
from custom_components.wallbox_manager.entity import unique_id
from custom_components.wallbox_manager.sensor import PVTarget


def sensor(ha, entry):
    found = [e for e in entities(ha, entry) if isinstance(e, PVTarget)]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(
    "profile,target", [("PV_MAXIMUM", 22), ("PV_SURPLUS", 50), ("PV_OPTIMUM", 40)]
)
async def test_registration_passive_target_identity_and_card(ha, profile, target):
    entry, pv, _ = await selected_pv(ha, profile)
    entity = sensor(ha, entry)
    assert entity.unique_id == unique_id(
        entry.entry_id, "pv", pv.state.binding, "effective_target_soc"
    )
    assert (
        entity.unique_id
        == f'{entry.entry_id}:pv:["Wallbox01","1","1","effective_target_soc"]'
    )
    registry = er.async_get(ha).async_get(entity.entity_id)
    device = dr.async_get(ha).async_get_device(entity.device_info["identifiers"])
    assert device and registry.device_id == device.id
    assert entity.available and entity.native_value == target
    assert entity.native_unit_of_measurement == "%"
    assert entity.state_class == "measurement" and entity.device_class is None
    assert entity.should_poll is False
    assert not pv.settings.get("charge_release") and not pv.state.connected
    assert pv.attributes()["effective_target_soc"] == target
    assert pv.grid.attributes()["effective_target_soc"] == target
    assert ha.states.get(entity.entity_id).state == str(float(target))
    assert (
        not {
            "connected",
            "observed_at",
            "received_at",
            "pv_import_energy_wh",
            "pv_planning_phase",
        }
        & entity.extra_state_attributes.keys()
    )


def test_translations():
    root = Path(__file__).parents[1] / "custom_components/wallbox_manager"
    for file, expected in (
        ("strings.json", "PV Target SoC"),
        ("translations/en.json", "PV Target SoC"),
        ("translations/de.json", "PV Ziel-SoC"),
    ):
        assert (
            json.loads((root / file).read_text())["entity"]["sensor"][
                "effective_target_soc"
            ]["name"]
            == expected
        )


async def test_no_target_for_netz_or_battery_free_surplus(ha):
    entry = await install(ha, battery=False)
    entity = sensor(ha, entry)
    assert not entity.available and entity.native_value is None
    await entry.runtime_data.settings.set("charging_profile", "PV_SURPLUS")
    await tick(entry.runtime_data.pv)
    assert (
        not entity.available and ha.states.get(entity.entity_id).state == "unavailable"
    )


@pytest.mark.parametrize(
    "role,bad",
    [
        ("min_soc_speicher", "unknown"),
        ("remaining_pv_energy", "unavailable"),
        ("remaining_pv_energy", "nan"),
        ("storage_capacity", "0"),
    ],
)
async def test_input_invalidation_and_recovery(ha, role, bad):
    entry, pv, _ = await selected_pv(ha)
    entity = sensor(ha, entry)
    before = pv.values[role]
    await set_source(ha, entry, role, bad)
    assert not entity.available and entity.native_value is None
    assert ha.states.get(entity.entity_id).state == "unavailable"
    await set_source(ha, entry, role, before)
    await tick(pv)
    assert entity.available and entity.native_value == pv.target == 40


async def test_forecast_precision_configuration_mapping_and_profile_changes(ha):
    entry, pv, _ = await selected_pv(ha)
    entity = sensor(ha, entry)
    await set_source(ha, entry, "remaining_pv_energy", 8419.53)
    await tick(pv)
    assert entity.native_value == pv.target == 55.8047
    assert pv.grid.attributes()["effective_target_soc"] == 55.8047
    await pv.settings.set("optimum_upper_soc", 85)
    await tick(pv)
    assert entity.native_value == 60.8047
    # Actual battery SoC is a regulation input, not a target-formula dependency.
    await set_source(ha, entry, "soc_speicher_aktuell", "unknown")
    assert entity.available
    await pv.settings.set("charging_profile", "PV_MAXIMUM")
    assert not entity.available
    await tick(pv)
    assert entity.native_value == 22
    await pv.settings.set("optimum_lower_soc", 25)
    await pv.settings.set("soc_hysterese", 3)
    await tick(pv)
    assert entity.native_value == 23
    ha.states.async_set("sensor.new_reserve", "25", {"unit_of_measurement": "%"})
    ha.config_entries.async_update_entry(
        entry, options={**entry.options, "min_soc_speicher": "sensor.new_reserve"}
    )
    await ha.async_block_till_done()
    assert not entity.available
    await tick(pv)
    assert entity.native_value == 28
    ha.config_entries.async_update_entry(
        entry, options={**entry.options, "min_soc_speicher": ""}
    )
    await ha.async_block_till_done()
    await tick(pv)
    assert not entity.available
    await pv.settings.set("charging_profile", "NETZ")
    assert not entity.available and entity.native_value is None


async def test_reload_and_non_default_binding_no_duplicates(ha):
    entry = config("OtherStation")
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(
        entry, data={**entry.data, "evse_id": "4", "connector_id": "7"}
    )
    await ha.install(entry)
    entity = sensor(ha, entry)
    identity, entity_id = entity.unique_id, entity.entity_id
    assert '["OtherStation","4","7","effective_target_soc"]' in identity
    for _ in range(2):
        await async_unload_entry(ha, entry)
        await ha.install(entry)
        entity = sensor(ha, entry)
        assert (entity.unique_id, entity.entity_id) == (identity, entity_id)
    entries = er.async_entries_for_config_entry(er.async_get(ha), entry.entry_id)
    assert len([r for r in entries if r.unique_id == identity]) == 1


async def test_reload_restores_passive_planning_target(ha):
    entry, pv, _ = await selected_pv(ha, "PV_MAXIMUM")
    identity = sensor(ha, entry).unique_id
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    entity = sensor(ha, entry)
    assert (
        entity.unique_id == identity and entity.available and entity.native_value == 22
    )


async def test_300_cycles_zero_redundant_writes_reads_timers_or_calculations(
    ha, monkeypatch
):
    entry, pv, clock = await selected_pv(ha, "PV_MAXIMUM")
    entity = sensor(ha, entry)
    before = ha.states.get(entity.entity_id)
    attrs, handles = dict(entity.extra_state_attributes), len(clock.calls)
    calculate = Mock(wraps=pv.planner.calculate)
    write = Mock(wraps=entity.async_write_ha_state)
    monkeypatch.setattr(pv.planner, "calculate", calculate)
    monkeypatch.setattr(entity, "async_write_ha_state", write)
    with monkeypatch.context() as patch:
        # Reading existing HA source entities again would violate the contract.
        patch.setattr(
            type(ha.states),
            "get",
            Mock(side_effect=AssertionError("additional HA read")),
        )
        for n in range(300):
            pv.values["grid_import_power"] = n
            pv.budget.wh = n / 10
            await tick(pv)
            pv.grid.publish()
            pv.state.publish(("session", pv.state.binding, "power"))
    assert (
        calculate.call_count == 300
    )  # Exactly the pre-existing planning call per tick.
    assert write.call_count == 0
    assert len(clock.calls) == handles
    assert ha.states.get(entity.entity_id).last_reported == before.last_reported
    assert entity.extra_state_attributes == attrs
    # One real change, then one invalidation, each publishes once.
    await set_source(ha, entry, "min_soc_speicher", 21.125)
    await tick(pv)
    assert write.call_count == 1 and entity.native_value == 23.125
    await set_source(ha, entry, "min_soc_speicher", "unavailable")
    assert write.call_count == 2 and not entity.available
    pv.invalidate_unavailable_target()
    assert write.call_count == 2
