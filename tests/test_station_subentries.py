"""Scoped subentries and migration preserve technical reference identity."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from test_ha_lifecycle import entry

from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.runtime import Runtime
from custom_components.wallbox_manager.station_config import (
    EntryReference,
    setup_station_configuration,
)


@pytest.mark.parametrize("legacy", [True, False])
async def test_migrates_explicit_reference_to_subentry_and_isolates_station(
    tmp_path, legacy
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    references = {
        "reference_station_id": "A",
        "reference_evse_id": 1,
        "reference_connector_id": 1,
        "reference_min_a": "6",
    }
    options = (
        references if legacy else {"station_references": {"A": {"1:1": references}}}
    )
    hass.config_entries.async_update_entry(
        config,
        options={
            **options,
            "min_soc_speicher": "number.reserve",
            "soc_speicher_aktuell": "sensor.soc",
        },
    )
    runtime = Runtime()
    try:
        setup_station_configuration(hass, config, runtime)
        assert len(config.subentries) == 1
        assert config.options == {
            "min_soc_speicher": "number.reserve",
            "soc_speicher_aktuell": "sensor.soc",
        }
        subentry = next(iter(config.subentries.values()))
        assert subentry.data["station"] == "A"
        source = EntryReference(config)
        a = ConnectorId(EvseId(StationId("A"), "1"), "1")
        b = ConnectorId(EvseId(StationId("B"), "1"), "1")
        assert source.capabilities(a, datetime.now(UTC))
        assert not source.capabilities(b, datetime.now(UTC))
        setup_station_configuration(hass, config, runtime)
        assert len(config.subentries) == 1
    finally:
        await config._async_process_on_unload(hass)
        await hass.async_stop()


async def test_discovery_creates_scoped_empty_configuration_without_battery_copy(
    tmp_path,
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    runtime = Runtime()
    config.runtime_data = SimpleNamespace(state=runtime)
    try:
        setup_station_configuration(hass, config, runtime)
        station = StationId("Wallbox01")
        target = ConnectorId(EvseId(station, "1"), "1")
        runtime.connect(station, protocol="ocpp", protocol_version="2.1")
        runtime._publish(replace(runtime.get(station), connectors=(target,)))
        subentry = next(iter(config.subentries.values()))
        assert subentry.data == {
            "station": "Wallbox01",
            "evse": "1",
            "connector": "1",
            "references": {},
        }
        runtime._publish(runtime.get(station))
        assert len(config.subentries) == 1
    finally:
        await config._async_process_on_unload(hass)
        await hass.async_stop()


@pytest.mark.parametrize(
    "change",
    [
        {"reference_step_a": "1/0"},
        {"reference_station_id": "B"},
        {"reference_connector_id": 7},
    ],
)
async def test_ambiguous_or_invalid_nested_references_are_preserved_unused(
    tmp_path, change
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    references = {
        "reference_station_id": "A",
        "reference_evse_id": 1,
        "reference_connector_id": 1,
        **change,
    }
    hass.config_entries.async_update_entry(
        config, options={"station_references": {"A": {"1:1": references}}}
    )
    try:
        setup_station_configuration(hass, config, Runtime())
        assert not config.subentries
        assert config.options["unassigned_references"]["A/1:1"] == references
    finally:
        await config._async_process_on_unload(hass)
        await hass.async_stop()
