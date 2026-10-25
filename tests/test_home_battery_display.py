"""Read-only card mapping and preserved HA Number APIs (WBM-HS-UI-12/15)."""

import pytest
from conftest import config, entities
from homeassistant.components import number

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


async def test_mapping_projection_and_five_number_services_survive_reload(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(
        entry,
        options={
            "soc_speicher_aktuell": "sensor.battery_soc",
            "min_soc_speicher": "number.battery_reserve",
        },
    )
    ha.states.async_set("sensor.battery_soc", "69.25")
    ha.states.async_set("number.battery_reserve", "0")
    await ha.install(entry)
    assert await number.async_setup(ha, {})

    def role(name):
        return next(e for e in entities(ha, entry) if e.role == name)

    profile = role("charging_profile")
    assert (
        profile.extra_state_attributes["battery_soc_entity_id"] == "sensor.battery_soc"
    )
    assert (
        profile.extra_state_attributes["battery_reserve_entity_id"]
        == "number.battery_reserve"
    )
    assert not entry.runtime_data.state.connected
    values = {
        "optimum_lower_soc": 25,
        "optimum_upper_soc": 85,
        "optimum_max_discharge_w": 3456,
        "estimated_daily_house_consumption_kwh": 12.5,
        "soll_soc_speicher": 40,
    }
    identities = {key: role(key).unique_id for key in values}
    for key, value in values.items():
        await ha.services.async_call(
            "number",
            "set_value",
            {"entity_id": role(key).entity_id, "value": value},
            blocking=True,
        )
        assert role(key).native_value == value
    with pytest.raises(ValueError, match="whole percent"):
        await ha.services.async_call(
            "number",
            "set_value",
            {"entity_id": role("soll_soc_speicher").entity_id, "value": 40.5},
            blocking=True,
        )
    assert role("soll_soc_speicher").native_value == 40
    for selected in ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM", "NETZ"):
        await profile.async_select_option(selected)
        assert all(role(key).native_value == value for key, value in values.items())
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    await ha.async_block_till_done()
    for key, value in values.items():
        assert role(key).native_value == value
        assert role(key).unique_id == identities[key]
    assert role("charging_profile").extra_state_attributes["battery_soc_entity_id"]


async def test_battery_display_adds_no_ha_state_publication_or_control(ha):
    entry = await ha.install()
    profile = next(e for e in entities(ha, entry) if e.role == "charging_profile")
    assert profile.extra_state_attributes["battery_soc_entity_id"] is None
    assert profile.extra_state_attributes["battery_reserve_entity_id"] is None
    published = []
    remove = ha.bus.async_listen("state_changed", lambda event: published.append(event))
    for _ in range(3):
        ha.states.async_set("sensor.unmapped_battery", "69")
        await ha.async_block_till_done()
    remove()
    assert not any(event.data["entity_id"] == profile.entity_id for event in published)


async def test_changed_mapping_publishes_once_without_observation_churn(ha):
    entry = await ha.install()
    profile = next(e for e in entities(ha, entry) if e.role == "charging_profile")
    published = []
    remove = ha.bus.async_listen("state_changed", lambda event: published.append(event))
    ha.config_entries.async_update_entry(
        entry,
        options={
            "soc_speicher_aktuell": "sensor.new_soc",
            "min_soc_speicher": "number.new_reserve",
        },
    )
    await ha.async_block_till_done()
    assert ha.states.get(profile.entity_id).attributes["battery_soc_entity_id"] == (
        "sensor.new_soc"
    )
    assert len([e for e in published if e.data["entity_id"] == profile.entity_id]) == 1
    published.clear()
    for value in ("69", "70", "unavailable", "0"):
        ha.states.async_set("sensor.new_soc", value)
        ha.states.async_set("number.new_reserve", value)
        await ha.async_block_till_done()
    assert not any(e.data["entity_id"] == profile.entity_id for e in published)
    remove()
